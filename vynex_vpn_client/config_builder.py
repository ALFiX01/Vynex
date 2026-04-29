from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import LOCAL_PROXY_HOST
from .models import LocalProxyCredentials, ServerEntry
from .routing_profiles import RoutingProfile


class XrayConfigBuilder:
    TUN_INTERFACE_NAME = "VynexTun"
    TUN_MTU = 1500
    TUN_ROUTE_PREFIXES = ("0.0.0.0/1", "128.0.0.0/1")

    def build(
        self,
        *,
        server: ServerEntry,
        mode: str,
        routing_profile: RoutingProfile,
        socks_port: int | None = None,
        http_port: int | None = None,
        socks_credentials: LocalProxyCredentials | None = None,
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        mode_upper = mode.upper()
        if mode_upper == "TUN":
            return self._build_tun_config(
                server=server,
                routing_profile=routing_profile,
                outbound_interface_name=outbound_interface_name,
            )
        if mode_upper != "PROXY":
            raise ValueError("Поддерживаются только Proxy и TUN режимы.")
        config: dict[str, Any] = {
            "log": {"loglevel": "warning"},
            "dns": self._dns_config(),
            "outbounds": [
                self._build_outbound(server),
                {"tag": "dns-out", "protocol": "dns"},
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": self._routing_config(routing_profile),
        }
        config["inbounds"] = self._proxy_inbounds(
            socks_port=socks_port,
            http_port=http_port,
            socks_credentials=socks_credentials,
        )
        return config

    @staticmethod
    def write(config: dict[str, Any], target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    def _proxy_inbounds(
        self,
        *,
        socks_port: int | None,
        http_port: int | None,
        socks_credentials: LocalProxyCredentials | None,
    ) -> list[dict[str, Any]]:
        base_sniffing = {"enabled": True, "destOverride": ["http", "tls", "quic"]}
        inbounds: list[dict[str, Any]] = []
        if socks_port is not None and socks_credentials is not None:
            inbounds.append(
                {
                    "tag": "socks-in",
                    "listen": LOCAL_PROXY_HOST,
                    "port": socks_port,
                    "protocol": "socks",
                    "settings": {
                        "auth": "password",
                        "udp": True,
                        "accounts": [
                            {
                                "user": socks_credentials.username,
                                "pass": socks_credentials.password,
                            }
                        ],
                    },
                    "sniffing": base_sniffing,
                }
            )
        elif socks_port is not None:
            raise ValueError("SOCKS inbound не может быть запущен без аутентификации.")
        if http_port is not None:
            inbounds.append(
                {
                    "tag": "http-in",
                    "listen": LOCAL_PROXY_HOST,
                    "port": http_port,
                    "protocol": "http",
                    "settings": {},
                    "sniffing": base_sniffing,
                }
            )
        if not inbounds:
            raise ValueError("Для Proxy режима не настроен ни один локальный inbound.")
        return inbounds

    @staticmethod
    def _dns_config() -> dict[str, Any]:
        return {
            "servers": [
            "1.1.1.1",
            "8.8.8.8",
            "https://1.1.1.1/dns-query",
        ]
        }

    @staticmethod
    def _routing_config(routing_profile: RoutingProfile) -> dict[str, Any]:
        return {
            "domainStrategy": "IPIfNonMatch",
            "rules": routing_profile.rules,
        }

    def _build_tun_config(
        self,
        *,
        server: ServerEntry,
        routing_profile: RoutingProfile,
        outbound_interface_name: str | None,
    ) -> dict[str, Any]:
        if not outbound_interface_name:
            raise ValueError("Для TUN режима не определен активный сетевой интерфейс Windows.")
        return {
            "log": {"loglevel": "warning"},
            "dns": self._dns_config(),
            "inbounds": [self._tun_inbound()],
            "outbounds": [
                self._build_outbound(server, outbound_interface_name=outbound_interface_name),
                {"tag": "dns-out", "protocol": "dns"},
                self._attach_outbound_interface(
                    {"tag": "direct", "protocol": "freedom"},
                    outbound_interface_name=outbound_interface_name,
                ),
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": self._tun_routing_config(routing_profile),
        }

    @classmethod
    def _tun_inbound(cls) -> dict[str, Any]:
        return {
            "tag": "tun-in",
            "protocol": "tun",
            "settings": {
                "name": cls.TUN_INTERFACE_NAME,
                "MTU": cls.TUN_MTU,
            },
            "sniffing": {
                "enabled": True,
                "destOverride": ["http", "tls", "quic"],
            },
        }

    def _tun_routing_config(self, routing_profile: RoutingProfile) -> dict[str, Any]:
        rules = [
            {
                "type": "field",
                "process": ["self/", "xray/"],
                "outboundTag": "direct",
            },
            *routing_profile.rules,
            {
                "type": "field",
                "network": "tcp,udp",
                "outboundTag": "proxy",
            },
        ]
        return {
            "domainStrategy": "IPIfNonMatch",
            "rules": rules,
        }

    def _build_outbound(
        self,
        server: ServerEntry,
        *,
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        protocol = server.protocol.lower()
        if protocol == "vless":
            return self._build_vless_outbound(server, outbound_interface_name=outbound_interface_name)
        if protocol == "vmess":
            return self._build_vmess_outbound(server, outbound_interface_name=outbound_interface_name)
        if protocol == "trojan":
            return self._build_trojan_outbound(server, outbound_interface_name=outbound_interface_name)
        if protocol == "ss":
            return self._build_shadowsocks_outbound(server, outbound_interface_name=outbound_interface_name)
        raise ValueError(f"Неподдерживаемый протокол: {server.protocol}")

    def _build_vless_outbound(
        self,
        server: ServerEntry,
        *,
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        extra = server.extra
        stream_settings = self._build_stream_settings(
            network=extra.get("network", "tcp"),
            security=extra.get("security", "none"),
            server=server,
            extra=extra,
            outbound_interface_name=outbound_interface_name,
        )
        user = {
            "id": extra["id"],
            "encryption": extra.get("encryption", "none"),
        }
        if extra.get("flow"):
            user["flow"] = extra["flow"]
        return {
            "tag": "proxy",
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": server.host,
                        "port": server.port,
                        "users": [user],
                    }
                ]
            },
            "streamSettings": stream_settings,
        }

    def _build_vmess_outbound(
        self,
        server: ServerEntry,
        *,
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        extra = server.extra
        tls_mode = "tls" if str(extra.get("tls", "")).lower() == "tls" else "none"
        stream_settings = self._build_stream_settings(
            network=extra.get("network", "tcp"),
            security=tls_mode,
            server=server,
            extra=extra,
            outbound_interface_name=outbound_interface_name,
        )
        return {
            "tag": "proxy",
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": server.host,
                        "port": server.port,
                        "users": [
                            {
                                "id": extra["id"],
                                "alterId": extra.get("alter_id", 0),
                                "security": extra.get("security", "auto"),
                            }
                        ],
                    }
                ]
            },
            "streamSettings": stream_settings,
        }

    def _build_trojan_outbound(
        self,
        server: ServerEntry,
        *,
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        extra = server.extra
        if not extra.get("password"):
            raise ValueError("Для Trojan требуется пароль.")
        stream_settings = self._build_stream_settings(
            network=extra.get("network", "tcp"),
            security=extra.get("security", "tls"),
            server=server,
            extra=extra,
            outbound_interface_name=outbound_interface_name,
        )
        return {
            "tag": "proxy",
            "protocol": "trojan",
            "settings": {
                "servers": [
                    {
                        "address": server.host,
                        "port": server.port,
                        "password": extra["password"],
                    }
                ]
            },
            "streamSettings": stream_settings,
        }

    def _build_shadowsocks_outbound(
        self,
        server: ServerEntry,
        *,
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        extra = server.extra
        outbound = {
            "tag": "proxy",
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": server.host,
                        "port": server.port,
                        "method": extra["method"],
                        "password": extra["password"],
                    }
                ]
            },
        }
        return self._attach_outbound_interface(
            outbound,
            outbound_interface_name=outbound_interface_name,
        )

    def _build_stream_settings(
        self,
        *,
        network: str,
        security: str,
        server: ServerEntry,
        extra: dict[str, Any],
        outbound_interface_name: str | None = None,
    ) -> dict[str, Any]:
        normalized_network = (network or "tcp").lower()
        if normalized_network in {"splithttp", "split-http"}:
            normalized_network = "xhttp"
        normalized_security = (security or "none").lower()
        stream_settings: dict[str, Any] = {
            "network": normalized_network,
            "security": normalized_security,
        }
        if normalized_security == "tls":
            tls_settings: dict[str, Any] = {}
            if extra.get("sni"):
                tls_settings["serverName"] = extra["sni"]
            if extra.get("fingerprint"):
                tls_settings["fingerprint"] = extra["fingerprint"]
            if extra.get("alpn"):
                tls_settings["alpn"] = [item.strip() for item in str(extra["alpn"]).split(",") if item.strip()]
            if str(extra.get("allow_insecure", "false")).lower() == "true":
                tls_settings["allowInsecure"] = True
            stream_settings["tlsSettings"] = tls_settings
        elif normalized_security == "reality":
            reality_settings = {
                "serverName": extra.get("sni") or server.host,
                "fingerprint": extra.get("fingerprint", "chrome"),
                "publicKey": extra.get("public_key"),
            }
            if extra.get("short_id"):
                reality_settings["shortId"] = extra["short_id"]
            if extra.get("spider_x"):
                reality_settings["spiderX"] = extra["spider_x"]
            stream_settings["realitySettings"] = {
                key: value for key, value in reality_settings.items() if value
            }
        if normalized_network == "ws":
            ws_settings: dict[str, Any] = {"path": extra.get("path") or "/"}
            if extra.get("host"):
                ws_settings["headers"] = {"Host": extra["host"]}
            stream_settings["wsSettings"] = ws_settings
        elif normalized_network == "grpc":
            grpc_settings = {"serviceName": extra.get("service_name") or ""}
            if extra.get("authority"):
                grpc_settings["authority"] = extra["authority"]
            stream_settings["grpcSettings"] = grpc_settings
        elif normalized_network == "xhttp":
            xhttp_settings: dict[str, Any] = {"path": extra.get("path") or "/"}
            if extra.get("host"):
                xhttp_settings["host"] = extra["host"]
            if extra.get("mode"):
                xhttp_settings["mode"] = extra["mode"]
            if isinstance(extra.get("xhttp_extra"), dict):
                xhttp_settings["extra"] = extra["xhttp_extra"]
            stream_settings["xhttpSettings"] = xhttp_settings
        elif normalized_network == "tcp" and extra.get("header_type") and extra["header_type"] != "none":
            stream_settings["tcpSettings"] = {"header": {"type": extra["header_type"]}}
        if outbound_interface_name:
            stream_settings["sockopt"] = {"interface": outbound_interface_name}
        return stream_settings

    @staticmethod
    def _attach_outbound_interface(
        outbound: dict[str, Any],
        *,
        outbound_interface_name: str | None,
    ) -> dict[str, Any]:
        if not outbound_interface_name:
            return outbound
        stream_settings = dict(outbound.get("streamSettings") or {})
        sockopt = dict(stream_settings.get("sockopt") or {})
        sockopt["interface"] = outbound_interface_name
        stream_settings["sockopt"] = sockopt
        outbound["streamSettings"] = stream_settings
        return outbound
