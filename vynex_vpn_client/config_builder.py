from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import LOCAL_PROXY_HOST
from .models import LocalProxyCredentials, ServerEntry
from .routing_profiles import RoutingProfile


class XrayConfigBuilder:
    def build(
        self,
        *,
        server: ServerEntry,
        mode: str,
        routing_profile: RoutingProfile,
        socks_port: int | None = None,
        http_port: int | None = None,
        socks_credentials: LocalProxyCredentials | None = None,
    ) -> dict[str, Any]:
        mode_upper = mode.upper()
        if mode_upper != "PROXY":
            raise ValueError("Поддерживается только Proxy режим.")
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

    def _build_outbound(self, server: ServerEntry) -> dict[str, Any]:
        protocol = server.protocol.lower()
        if protocol == "vless":
            return self._build_vless_outbound(server)
        if protocol == "vmess":
            return self._build_vmess_outbound(server)
        if protocol == "ss":
            return self._build_shadowsocks_outbound(server)
        raise ValueError(f"Неподдерживаемый протокол: {server.protocol}")

    def _build_vless_outbound(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        stream_settings = self._build_stream_settings(
            network=extra.get("network", "tcp"),
            security=extra.get("security", "none"),
            server=server,
            extra=extra,
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

    def _build_vmess_outbound(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        tls_mode = "tls" if str(extra.get("tls", "")).lower() == "tls" else "none"
        stream_settings = self._build_stream_settings(
            network=extra.get("network", "tcp"),
            security=tls_mode,
            server=server,
            extra=extra,
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

    @staticmethod
    def _build_shadowsocks_outbound(server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        return {
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

    def _build_stream_settings(
        self,
        *,
        network: str,
        security: str,
        server: ServerEntry,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_network = (network or "tcp").lower()
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
        elif normalized_network == "tcp" and extra.get("header_type") and extra["header_type"] != "none":
            stream_settings["tcpSettings"] = {"header": {"type": extra["header_type"]}}
        return stream_settings
