from __future__ import annotations

from typing import Any

from .constants import LOCAL_PROXY_HOST
from .models import LocalProxyCredentials, ServerEntry
from .routing_profiles import RoutingProfile


class SingboxConfigBuilder:
    TUN_INTERFACE_NAME = "xftun"
    TUN_INTERFACE_ADDRESS = "172.19.0.1/30"
    TUN_MTU = 1500

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
        mode_upper = str(mode or "").upper()
        if mode_upper == "TUN":
            return self._build_tun_config(server=server, routing_profile=routing_profile)
        if mode_upper != "PROXY":
            raise ValueError("Поддерживаются только Proxy и TUN режимы.")
        if socks_port is None or http_port is None or socks_credentials is None:
            raise ValueError("Для Proxy режима sing-box нужны локальные SOCKS и HTTP inbounds.")
        return self._build_proxy_config(
            server=server,
            routing_profile=routing_profile,
            socks_port=socks_port,
            http_port=http_port,
            socks_credentials=socks_credentials,
        )

    def _build_proxy_config(
        self,
        *,
        server: ServerEntry,
        routing_profile: RoutingProfile,
        socks_port: int,
        http_port: int,
        socks_credentials: LocalProxyCredentials,
    ) -> dict[str, Any]:
        return {
            "log": {
                "level": "warn",
                "timestamp": True,
            },
            "dns": self._dns_config(),
            "inbounds": [
                self._socks_inbound(
                    socks_port=socks_port,
                    socks_credentials=socks_credentials,
                ),
                self._http_inbound(http_port=http_port),
            ],
            "outbounds": [
                self._build_outbound(server),
                self._direct_outbound(),
                self._block_outbound(),
            ],
            "route": self._route_config(
                routing_profile=routing_profile,
                include_dns_hijack=False,
            ),
        }

    def _build_tun_config(
        self,
        *,
        server: ServerEntry,
        routing_profile: RoutingProfile,
    ) -> dict[str, Any]:
        return {
            "log": {
                "level": "warn",
                "timestamp": True,
            },
            "dns": self._dns_config(),
            "inbounds": [
                self._tun_inbound(),
            ],
            "outbounds": [
                self._build_outbound(server),
                self._direct_outbound(),
                self._block_outbound(),
            ],
            "route": self._route_config(
                routing_profile=routing_profile,
                include_dns_hijack=True,
            ),
        }

    @classmethod
    def _tun_inbound(cls) -> dict[str, Any]:
        return {
            "type": "tun",
            "tag": "tun-in",
            "interface_name": cls.TUN_INTERFACE_NAME,
            "address": [cls.TUN_INTERFACE_ADDRESS],
            "mtu": cls.TUN_MTU,
            "auto_route": True,
            "strict_route": True,
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
        if protocol in {"hy2", "hysteria2"}:
            return self._build_hysteria2_outbound(server)
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

    def _build_hysteria2_outbound(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        password = str(extra.get("password") or "").strip()
        if not password:
            raise ValueError("Для Hysteria2 требуется пароль.")

        outbound: dict[str, Any] = {
            "type": "hysteria2",
            "tag": "proxy",
            "server": server.host,
            "password": password,
            "tls": self._build_hysteria2_tls_config(server),
            "domain_resolver": "bootstrap-dns",
        }
        server_ports = self._normalize_server_ports(extra.get("server_ports"))
        if server_ports:
            outbound["server_ports"] = server_ports
        else:
            outbound["server_port"] = server.port

        network = str(extra.get("network") or "").strip().lower()
        if network in {"tcp", "udp"}:
            outbound["network"] = network

        obfs = self._build_hysteria2_obfs(extra)
        if obfs is not None:
            outbound["obfs"] = obfs

        for source_key, target_key in (
            ("hop_interval", "hop_interval"),
            ("hopInterval", "hop_interval"),
            ("hop_interval_max", "hop_interval_max"),
            ("hopIntervalMax", "hop_interval_max"),
            ("bbr_profile", "bbr_profile"),
            ("bbrProfile", "bbr_profile"),
        ):
            value = str(extra.get(source_key) or "").strip()
            if value and target_key not in outbound:
                outbound[target_key] = value

        for source_key, target_key in (
            ("up_mbps", "up_mbps"),
            ("upmbps", "up_mbps"),
            ("down_mbps", "down_mbps"),
            ("downmbps", "down_mbps"),
        ):
            value = self._optional_int(extra.get(source_key))
            if value is not None and target_key not in outbound:
                outbound[target_key] = value

        brutal_debug = self._optional_bool(extra.get("brutal_debug"))
        if brutal_debug is not None:
            outbound["brutal_debug"] = brutal_debug
        return outbound

    @staticmethod
    def _socks_inbound(
        *,
        socks_port: int,
        socks_credentials: LocalProxyCredentials,
    ) -> dict[str, Any]:
        return {
            "type": "socks",
            "tag": "socks-in",
            "listen": LOCAL_PROXY_HOST,
            "listen_port": socks_port,
            "users": [
                {
                    "username": socks_credentials.username,
                    "password": socks_credentials.password,
                }
            ],
        }

    @staticmethod
    def _http_inbound(*, http_port: int) -> dict[str, Any]:
        return {
            "type": "http",
            "tag": "http-in",
            "listen": LOCAL_PROXY_HOST,
            "listen_port": http_port,
        }

    @staticmethod
    def _dns_config() -> dict[str, Any]:
        return {
            "servers": [
                {
                    "tag": "bootstrap-dns",
                    "type": "udp",
                    "server": "1.1.1.1",
                },
                {
                    "tag": "fallback-dns",
                    "type": "udp",
                    "server": "8.8.8.8",
                },
            ],
            "final": "bootstrap-dns",
        }

    @staticmethod
    def _direct_outbound() -> dict[str, Any]:
        return {
            "type": "direct",
            "tag": "direct",
            "domain_resolver": "bootstrap-dns",
        }

    @staticmethod
    def _block_outbound() -> dict[str, Any]:
        return {"type": "block", "tag": "block"}

    def _build_common_transport_settings(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        settings: dict[str, Any] = {}
        tls_config = self._build_tls_config(server)
        if tls_config:
            settings["tls"] = tls_config
        transport_config = self._build_transport_config(extra)
        if transport_config:
            settings["transport"] = transport_config
        settings["domain_resolver"] = "bootstrap-dns"
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

    def _build_hysteria2_tls_config(self, server: ServerEntry) -> dict[str, Any]:
        extra = server.extra
        tls_config: dict[str, Any] = {
            "enabled": True,
            "server_name": str(extra.get("sni") or server.host),
        }
        insecure = self._optional_bool(extra.get("insecure"))
        if insecure is None:
            insecure = self._optional_bool(extra.get("allow_insecure"))
        if insecure:
            tls_config["insecure"] = True
        alpn = self._normalize_string_list(extra.get("alpn"))
        if alpn:
            tls_config["alpn"] = alpn
        fingerprint = str(extra.get("fingerprint") or extra.get("fp") or "").strip()
        if fingerprint:
            tls_config["utls"] = {
                "enabled": True,
                "fingerprint": fingerprint,
            }
        pin_sha256 = extra.get("pin_sha256")
        if pin_sha256 in (None, ""):
            pin_sha256 = extra.get("pinSHA256")
        if pin_sha256 not in (None, ""):
            if isinstance(pin_sha256, list):
                pins = [str(item).strip() for item in pin_sha256 if str(item).strip()]
            else:
                pins = self._normalize_string_list(pin_sha256)
            if pins:
                tls_config["certificate_public_key_sha256"] = pins
        return tls_config

    @staticmethod
    def _build_hysteria2_obfs(extra: dict[str, Any]) -> dict[str, Any] | None:
        raw_obfs = extra.get("obfs")
        if isinstance(raw_obfs, dict):
            obfs_type = str(raw_obfs.get("type") or "").strip()
            obfs_password = str(raw_obfs.get("password") or "").strip()
            if obfs_type and obfs_password:
                return {"type": obfs_type, "password": obfs_password}
        obfs_type = str(raw_obfs or "").strip()
        obfs_password = str(extra.get("obfs_password") or extra.get("obfs-password") or "").strip()
        if obfs_type and obfs_password:
            return {"type": obfs_type, "password": obfs_password}
        return None

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

    def _route_config(
        self,
        *,
        routing_profile: RoutingProfile,
        include_dns_hijack: bool,
    ) -> dict[str, Any]:
        translated_rules = self._translate_routing_rules(routing_profile)
        route: dict[str, Any] = {
            "rules": [],
            "final": "proxy",
            "auto_detect_interface": True,
            "default_domain_resolver": "bootstrap-dns",
        }
        route["rules"].append({"action": "sniff"})
        if include_dns_hijack:
            route["rules"].append({"protocol": ["dns"], "action": "hijack-dns"})
        route["rules"].extend(translated_rules)
        if any("geoip" in rule or "source_geoip" in rule for rule in translated_rules):
            route["geoip"] = {}
        if any("geosite" in rule for rule in translated_rules):
            route["geosite"] = {}
        return route

    def _translate_routing_rules(self, routing_profile: RoutingProfile) -> list[dict[str, Any]]:
        translated: list[dict[str, Any]] = []
        for rule in routing_profile.rules:
            translated_rule = self._translate_routing_rule(rule)
            if translated_rule is not None:
                translated.append(translated_rule)
        return translated

    def _translate_routing_rule(self, rule: dict[str, Any]) -> dict[str, Any] | None:
        translated: dict[str, Any] = {
            "action": "route",
            "outbound": self._normalize_outbound_tag(rule.get("outboundTag")),
        }
        has_conditions = False

        domains = rule.get("domain")
        if isinstance(domains, list):
            plain_domains: list[str] = []
            domain_suffixes: list[str] = []
            domain_keywords: list[str] = []
            domain_regexes: list[str] = []
            geosites: list[str] = []
            for item in domains:
                value = str(item or "").strip()
                if not value:
                    continue
                if value.startswith("geosite:"):
                    geosites.append(value.removeprefix("geosite:"))
                elif value.startswith("full:"):
                    plain_domains.append(value.removeprefix("full:"))
                elif value.startswith("domain:"):
                    domain_suffixes.append(value.removeprefix("domain:"))
                elif value.startswith("regexp:"):
                    domain_regexes.append(value.removeprefix("regexp:"))
                elif value.startswith("keyword:"):
                    domain_keywords.append(value.removeprefix("keyword:"))
                else:
                    plain_domains.append(value)
            has_conditions = has_conditions or bool(
                plain_domains or domain_suffixes or domain_keywords or domain_regexes or geosites
            )
            if plain_domains:
                translated["domain"] = plain_domains
            if domain_suffixes:
                translated["domain_suffix"] = domain_suffixes
            if domain_keywords:
                translated["domain_keyword"] = domain_keywords
            if domain_regexes:
                translated["domain_regex"] = domain_regexes
            if geosites:
                translated["geosite"] = geosites

        ips = rule.get("ip")
        if isinstance(ips, list):
            ip_cidrs: list[str] = []
            geoips: list[str] = []
            ip_is_private = False
            for item in ips:
                value = str(item or "").strip()
                if not value:
                    continue
                if value == "geoip:private":
                    ip_is_private = True
                elif value.startswith("geoip:"):
                    geoips.append(value.removeprefix("geoip:"))
                else:
                    ip_cidrs.append(value)
            has_conditions = has_conditions or bool(ip_cidrs or geoips or ip_is_private)
            if ip_cidrs:
                translated["ip_cidr"] = ip_cidrs
            if geoips:
                translated["geoip"] = geoips
            if ip_is_private:
                translated["ip_is_private"] = True

        network = rule.get("network")
        if isinstance(network, str):
            networks = [item.strip().lower() for item in network.split(",") if item.strip()]
            if networks:
                translated["network"] = networks
                has_conditions = True

        processes = rule.get("process")
        if isinstance(processes, list):
            process_names = [str(item).strip() for item in processes if str(item).strip()]
            if process_names:
                translated["process_name"] = process_names
                has_conditions = True

        return translated if has_conditions else None

    @staticmethod
    def _normalize_outbound_tag(value: Any) -> str:
        normalized = str(value or "proxy").strip().lower()
        if normalized in {"direct", "proxy", "block"}:
            return normalized
        return "proxy"

    @staticmethod
    def _normalize_server_ports(value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            items = value
        else:
            items = str(value).split(",")
        normalized: list[str] = []
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            if "-" in text:
                start_text, end_text = text.split("-", 1)
                start = int(start_text)
                end = int(end_text)
                if not 1 <= start <= end <= 65535:
                    raise ValueError("Для Hysteria2 указан некорректный диапазон server_ports.")
                normalized.append(f"{start}:{end}")
                continue
            if ":" in text:
                start_text, end_text = text.split(":", 1)
                start = int(start_text)
                end = int(end_text)
                if not 1 <= start <= end <= 65535:
                    raise ValueError("Для Hysteria2 указан некорректный диапазон server_ports.")
                normalized.append(f"{start}:{end}")
                continue
            port = int(text)
            if not 1 <= port <= 65535:
                raise ValueError("Для Hysteria2 указан некорректный порт в server_ports.")
            normalized.append(str(port))
        return normalized

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

    @staticmethod
    def _optional_bool(value: Any) -> bool | None:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return None

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            items = value
        else:
            items = str(value).split(",")
        return [str(item).strip() for item in items if str(item).strip()]
