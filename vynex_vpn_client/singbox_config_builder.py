from __future__ import annotations

from typing import Any

from .constants import LOCAL_PROXY_HOST
from .models import LocalProxyCredentials, ServerEntry


class SingboxConfigBuilder:
    TUN_INTERFACE_NAME = "xftun"
    TUN_INTERFACE_ADDRESS = "172.19.0.1/30"
    PROXY_DOMAINS = [
        "ntc.party",
        "rutracker.org",
    ]
    PROXY_DOMAIN_REGEX = [
        r"(^|\.)facebook\.com$",
        r"(^|\.)instagram\.com$",
    ]
    PROXY_PROCESSES = [
        "Telegram.exe",
        "AyuGram.exe",
    ]
    DIRECT_PROCESSES = [
        "chrome.exe",
        "firefox.exe",
        "waterfox.exe",
        "librewolf.exe",
        "msedge.exe",
        "opera.exe",
        "brave.exe",
        "vivaldi.exe",
        "browser.exe",
        "Discord.exe",
        "Vesktop.exe",
        "Spotify.exe",
    ]

    def build_tun(
        self,
        *,
        server: ServerEntry | None = None,
        socks_port: int | None = None,
        socks_credentials: LocalProxyCredentials | None = None,
    ) -> dict[str, Any]:
        if server is not None:
            proxy_outbound = self._build_outbound(server)
            final_outbound = "direct"
        else:
            if socks_port is None or socks_credentials is None:
                raise ValueError("Для TUN режима нужен либо сервер, либо локальный SOCKS backend.")
            proxy_outbound = self._build_local_socks_outbound(
                socks_port=socks_port,
                socks_credentials=socks_credentials,
            )
            final_outbound = "proxy"

        return {
            "log": {
                "level": "warn",
                "timestamp": True,
            },
            "inbounds": [
                self._tun_inbound(),
            ],
            "outbounds": [
                proxy_outbound,
                {
                    "type": "direct",
                    "tag": "direct",
                    "domain_resolver": "bootstrap-dns",
                },
                {"type": "block", "tag": "block"},
            ],
            "dns": {
                "servers": [
                    {
                        "tag": "bootstrap-dns",
                        "type": "udp",
                        "server": "1.1.1.1",
                    },
                    {
                        "tag": "proxy-dns",
                        "type": "tcp",
                        "server": "8.8.8.8",
                        "detour": "proxy",
                    },
                ],
                "final": "proxy-dns",
            },
            "route": {
                "auto_detect_interface": True,
                "default_domain_resolver": "proxy-dns",
                "rules": self._route_rules(),
                "final": final_outbound,
            },
        }

    @classmethod
    def _tun_inbound(cls) -> dict[str, Any]:
        return {
            "type": "tun",
            "tag": "tun-in",
            "interface_name": cls.TUN_INTERFACE_NAME,
            "address": [cls.TUN_INTERFACE_ADDRESS],
            "auto_route": True,
            "strict_route": False,
            "stack": "mixed",
        }

    def _build_outbound(self, server: ServerEntry) -> dict[str, Any]:
        protocol = server.protocol.lower()
        if protocol == "vless":
            return self._build_vless_outbound(server)
        if protocol == "vmess":
            return self._build_vmess_outbound(server)
        if protocol == "trojan":
            return self._build_trojan_outbound(server)
        if protocol == "ss":
            return self._build_shadowsocks_outbound(server)
        raise ValueError(f"Неподдерживаемый протокол для sing-box: {server.protocol}")

    def _build_vless_outbound(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        if not extra.get("id"):
            raise ValueError("Для VLESS в TUN режиме требуется UUID.")
        return {
            "type": "vless",
            "tag": "proxy",
            "server": server.host,
            "server_port": server.port,
            "uuid": extra["id"],
            **({"flow": extra["flow"]} if extra.get("flow") else {}),
            **self._build_common_transport_settings(server),
        }

    def _build_vmess_outbound(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        if not extra.get("id"):
            raise ValueError("Для VMess в TUN режиме требуется UUID.")
        outbound: dict[str, Any] = {
            "type": "vmess",
            "tag": "proxy",
            "server": server.host,
            "server_port": server.port,
            "uuid": extra["id"],
            "security": extra.get("security", "auto"),
            **self._build_common_transport_settings(server),
        }
        alter_id = int(extra.get("alter_id", 0) or 0)
        if alter_id > 0:
            outbound["alter_id"] = alter_id
        return outbound

    def _build_trojan_outbound(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        if not extra.get("password"):
            raise ValueError("Для Trojan в TUN режиме требуется пароль.")
        return {
            "type": "trojan",
            "tag": "proxy",
            "server": server.host,
            "server_port": server.port,
            "password": extra["password"],
            **self._build_common_transport_settings(server),
        }

    @staticmethod
    def _build_shadowsocks_outbound(server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        return {
            "type": "shadowsocks",
            "tag": "proxy",
            "server": server.host,
            "server_port": server.port,
            "method": extra["method"],
            "password": extra["password"],
        }

    @staticmethod
    def _build_local_socks_outbound(
        *,
        socks_port: int,
        socks_credentials: LocalProxyCredentials,
    ) -> dict[str, Any]:
        return {
            "type": "socks",
            "tag": "proxy",
            "server": LOCAL_PROXY_HOST,
            "server_port": socks_port,
            "username": socks_credentials.username,
            "password": socks_credentials.password,
        }

    def _build_common_transport_settings(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        settings: dict[str, Any] = {}
        tls_config = self._build_tls_config(server)
        if tls_config:
            settings["tls"] = tls_config
        transport_config = self._build_transport_config(extra)
        if transport_config:
            settings["transport"] = transport_config
        return settings

    def _build_tls_config(self, server: ServerEntry) -> dict[str, Any] | None:
        extra = server.extra
        security = str(extra.get("security", extra.get("tls", "none")) or "none").lower()
        tls_enabled = security in {"tls", "reality"} or str(extra.get("tls", "")).lower() == "tls"
        if not tls_enabled:
            return None

        tls_config: dict[str, Any] = {
            "enabled": True,
            "server_name": extra.get("sni") or server.host,
        }
        if str(extra.get("allow_insecure", "false")).lower() == "true":
            tls_config["insecure"] = True
        if extra.get("alpn"):
            tls_config["alpn"] = [item.strip() for item in str(extra["alpn"]).split(",") if item.strip()]
        if extra.get("fingerprint"):
            tls_config["utls"] = {
                "enabled": True,
                "fingerprint": extra["fingerprint"],
            }
        if security == "reality":
            if not extra.get("public_key"):
                raise ValueError("Для Reality в TUN режиме требуется public_key.")
            reality: dict[str, Any] = {
                "enabled": True,
                "public_key": extra["public_key"],
                "short_id": extra.get("short_id", ""),
            }
            tls_config["reality"] = reality
        return tls_config

    @staticmethod
    def _build_transport_config(extra: dict[str, Any]) -> dict[str, Any] | None:
        network = str(extra.get("network", "tcp") or "tcp").lower()
        header_type = str(extra.get("header_type", "none") or "none").lower()
        if network == "ws":
            transport: dict[str, Any] = {
                "type": "ws",
                "path": extra.get("path") or "/",
            }
            if extra.get("host"):
                transport["headers"] = {"Host": extra["host"]}
            return transport
        if network == "grpc":
            if extra.get("authority"):
                raise ValueError("gRPC authority не поддерживается в TUN режиме sing-box.")
            service_name = extra.get("service_name") or ""
            if not service_name:
                raise ValueError("Для gRPC в TUN режиме требуется service_name.")
            transport = {
                "type": "grpc",
                "service_name": service_name,
            }
            return transport
        if network == "quic":
            return {
                "type": "quic",
            }
        if network == "httpupgrade":
            transport = {
                "type": "httpupgrade",
                "path": extra.get("path") or "/",
            }
            if extra.get("host"):
                transport["host"] = extra["host"]
            return transport
        if network == "http" or (network == "tcp" and header_type == "http"):
            transport = {
                "type": "http",
                "path": extra.get("path") or "/",
            }
            if extra.get("host"):
                transport["host"] = [item.strip() for item in str(extra["host"]).split(",") if item.strip()]
            return transport
        if network == "tcp" and header_type not in {"", "none"}:
            raise ValueError(f"TCP header type '{header_type}' не поддерживается в TUN режиме sing-box.")
        if network not in {"tcp", "ws", "grpc", "quic", "httpupgrade", "http"}:
            raise ValueError(f"Transport '{network}' не поддерживается в TUN режиме sing-box.")
        return None

    @classmethod
    def _route_rules(cls) -> list[dict[str, Any]]:
        return [
            {
                "action": "sniff",
            },
            {
                "protocol": "dns",
                "action": "hijack-dns",
            },
            {
                "domain": cls.PROXY_DOMAINS,
                "domain_regex": cls.PROXY_DOMAIN_REGEX,
                "outbound": "proxy",
            },
            {
                "process_name": cls.PROXY_PROCESSES,
                "outbound": "proxy",
            },
            {
                "process_name": cls.DIRECT_PROCESSES,
                "outbound": "direct",
            },
        ]
