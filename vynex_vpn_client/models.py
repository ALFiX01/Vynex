from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import ipaddress
import re
from typing import Any
from uuid import uuid4

from .amneziawg_capabilities import (
    get_awg_capability_spec,
    normalize_awg_protocol_version,
    parse_awg_header_value,
    resolve_awg_semantics,
)

_HOST_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_string_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_extra_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _coerce_stored_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, int):
        return bool(value)
    return default


def _validate_base64_key(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"В конфиге AWG отсутствует {field_name}.")
    try:
        decoded = base64.b64decode(normalized.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Поле {field_name} в AWG-конфиге содержит некорректный ключ.") from exc
    if len(decoded) != 32:
        raise ValueError(f"Поле {field_name} в AWG-конфиге должно содержать 32-байтовый ключ.")
    return normalized


def _is_valid_hostname(value: str) -> bool:
    candidate = value.strip().rstrip(".")
    if not candidate or len(candidate) > 253:
        return False
    return all(_HOST_LABEL_RE.fullmatch(label) for label in candidate.split("."))


def _validate_host_or_ip(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"В конфиге AWG отсутствует {field_name}.")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        if not _is_valid_hostname(normalized):
            raise ValueError(f"Поле {field_name} в AWG-конфиге содержит некорректный адрес хоста.") from None
    return normalized


def _validate_port(field_name: str, value: int | None) -> int:
    if value is None or not 1 <= int(value) <= 65535:
        raise ValueError(f"Поле {field_name} в AWG-конфиге содержит некорректный порт.")
    return int(value)


def _validate_ip_interface(field_name: str, value: str) -> str:
    normalized = value.strip()
    try:
        ipaddress.ip_interface(normalized)
    except ValueError as exc:
        raise ValueError(f"Поле {field_name} в AWG-конфиге содержит некорректный IP-адрес или префикс.") from exc
    return normalized


def _validate_ip_network(field_name: str, value: str) -> str:
    normalized = value.strip()
    try:
        ipaddress.ip_network(normalized, strict=False)
    except ValueError as exc:
        raise ValueError(f"Поле {field_name} в AWG-конфиге содержит некорректную подсеть.") from exc
    return normalized


@dataclass
class AmneziaWgObfuscationSettings:
    jc: int | None = None
    jmin: int | None = None
    jmax: int | None = None
    s1: int | None = None
    s2: int | None = None
    s3: int | None = None
    s4: int | None = None
    h1: str | None = None
    h2: str | None = None
    h3: str | None = None
    h4: str | None = None
    i1: str | None = None
    i2: str | None = None
    i3: str | None = None
    i4: str | None = None
    i5: str | None = None
    extra_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.extra_fields = _normalize_extra_fields(self.extra_fields)
        self.validate()

    def validate(self) -> None:
        if self.jc is not None and self.jc < 0:
            raise ValueError("Поле Jc в AWG-конфиге не может быть отрицательным.")
        if self.jmin is not None and self.jmin < 0:
            raise ValueError("Поле Jmin в AWG-конфиге не может быть отрицательным.")
        if self.jmax is not None and self.jmax < 0:
            raise ValueError("Поле Jmax в AWG-конфиге не может быть отрицательным.")
        if self.jmin is not None and self.jmax is not None and self.jmin >= self.jmax:
            raise ValueError("В AWG-конфиге значение Jmin должно быть меньше Jmax.")

        for field_name in ("s1", "s2", "s3", "s4"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"Поле {field_name.upper()} в AWG-конфиге не может быть отрицательным.")

        header_ranges = []
        for field_name in ("h1", "h2", "h3", "h4"):
            value = getattr(self, field_name)
            if value is None:
                continue
            header_ranges.append((field_name.upper(), parse_awg_header_value(value)))
        for index, (field_name, current_range) in enumerate(header_ranges):
            for other_field_name, other_range in header_ranges[index + 1 :]:
                if current_range[0] <= other_range[1] and other_range[0] <= current_range[1]:
                    raise ValueError(
                        f"Диапазоны {field_name} и {other_field_name} в AWG-конфиге не должны пересекаться."
                    )

        for field_name in ("i1", "i2", "i3", "i4", "i5"):
            value = getattr(self, field_name)
            if value is not None and not str(value).strip():
                raise ValueError(f"Поле {field_name.upper()} в AWG-конфиге не может быть пустым.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_capability_dict(self) -> dict[str, Any]:
        payload = {
            "jc": self.jc,
            "jmin": self.jmin,
            "jmax": self.jmax,
            "s1": self.s1,
            "s2": self.s2,
            "s3": self.s3,
            "s4": self.s4,
            "h1": self.h1,
            "h2": self.h2,
            "h3": self.h3,
            "h4": self.h4,
            "i1": self.i1,
            "i2": self.i2,
            "i3": self.i3,
            "i4": self.i4,
            "i5": self.i5,
        }
        payload.update(self.extra_fields)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AmneziaWgObfuscationSettings":
        if not data:
            return cls()
        return cls(
            jc=data.get("jc"),
            jmin=data.get("jmin"),
            jmax=data.get("jmax"),
            s1=data.get("s1"),
            s2=data.get("s2"),
            s3=data.get("s3"),
            s4=data.get("s4"),
            h1=data.get("h1"),
            h2=data.get("h2"),
            h3=data.get("h3"),
            h4=data.get("h4"),
            i1=data.get("i1"),
            i2=data.get("i2"),
            i3=data.get("i3"),
            i4=data.get("i4"),
            i5=data.get("i5"),
            extra_fields=_normalize_extra_fields(data.get("extra_fields")),
        )


@dataclass
class AmneziaWgInterface:
    private_key: str
    addresses: list[str]
    dns: list[str] = field(default_factory=list)
    mtu: int | None = None
    listen_port: int | None = None
    obfuscation: AmneziaWgObfuscationSettings = field(default_factory=AmneziaWgObfuscationSettings)
    extra_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.private_key = _validate_base64_key("PrivateKey", self.private_key)
        self.addresses = _normalize_string_list(self.addresses)
        self.dns = _normalize_string_list(self.dns)
        self.extra_fields = _normalize_extra_fields(self.extra_fields)
        if not isinstance(self.obfuscation, AmneziaWgObfuscationSettings):
            self.obfuscation = AmneziaWgObfuscationSettings.from_dict(self.obfuscation)
        self.validate()

    def validate(self) -> None:
        if not self.addresses:
            raise ValueError("В AWG-конфиге отсутствует Address в секции [Interface].")
        self.addresses = [_validate_ip_interface("Address", value) for value in self.addresses]
        self.dns = [_validate_host_or_ip("DNS", value) for value in self.dns]
        if self.mtu is not None and not 1 <= self.mtu <= 65535:
            raise ValueError("Поле MTU в AWG-конфиге должно быть в диапазоне 1..65535.")
        if self.listen_port is not None:
            self.listen_port = _validate_port("ListenPort", self.listen_port)
        self.obfuscation.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AmneziaWgInterface":
        return cls(
            private_key=str(data.get("private_key") or ""),
            addresses=[str(value) for value in data.get("addresses", [])],
            dns=[str(value) for value in data.get("dns", [])],
            mtu=data.get("mtu"),
            listen_port=data.get("listen_port"),
            obfuscation=AmneziaWgObfuscationSettings.from_dict(data.get("obfuscation")),
            extra_fields=_normalize_extra_fields(data.get("extra_fields")),
        )


@dataclass
class AmneziaWgPeer:
    public_key: str
    preshared_key: str | None = None
    endpoint_host: str | None = None
    endpoint_port: int | None = None
    allowed_ips: list[str] = field(default_factory=list)
    keepalive: int | None = None
    extra_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.public_key = _validate_base64_key("PublicKey", self.public_key)
        self.preshared_key = self.preshared_key.strip() if self.preshared_key else None
        if self.preshared_key is not None:
            self.preshared_key = _validate_base64_key("PresharedKey", self.preshared_key)
        self.endpoint_host = self.endpoint_host.strip() if self.endpoint_host else None
        self.allowed_ips = _normalize_string_list(self.allowed_ips)
        self.extra_fields = _normalize_extra_fields(self.extra_fields)
        self.validate()

    def validate(self) -> None:
        if not self.allowed_ips:
            raise ValueError("В AWG-конфиге отсутствует AllowedIPs в секции [Peer].")
        self.allowed_ips = [_validate_ip_network("AllowedIPs", value) for value in self.allowed_ips]
        self.endpoint_host = _validate_host_or_ip("Endpoint", self.endpoint_host or "")
        self.endpoint_port = _validate_port("Endpoint", self.endpoint_port)
        if self.keepalive is not None and not 0 <= self.keepalive <= 65535:
            raise ValueError("Поле PersistentKeepalive в AWG-конфиге должно быть в диапазоне 0..65535.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AmneziaWgPeer":
        return cls(
            public_key=str(data.get("public_key") or ""),
            preshared_key=data.get("preshared_key"),
            endpoint_host=data.get("endpoint_host"),
            endpoint_port=data.get("endpoint_port"),
            allowed_ips=[str(value) for value in data.get("allowed_ips", [])],
            keepalive=data.get("keepalive"),
            extra_fields=_normalize_extra_fields(data.get("extra_fields")),
        )


@dataclass
class AmneziaWgProfile:
    name: str
    interface: AmneziaWgInterface
    peers: list[AmneziaWgPeer]
    source_format: str = "conf"
    protocol_version: str | None = None
    version_source: str = "inferred"
    feature_flags: list[str] = field(default_factory=list)
    compatibility_flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extra_sections: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        if not isinstance(self.interface, AmneziaWgInterface):
            self.interface = AmneziaWgInterface.from_dict(self.interface)
        self.peers = [
            peer if isinstance(peer, AmneziaWgPeer) else AmneziaWgPeer.from_dict(peer)
            for peer in self.peers
        ]
        self.protocol_version = normalize_awg_protocol_version(self.protocol_version)
        self.version_source = str(self.version_source or "inferred").strip() or "inferred"
        self.feature_flags = _normalize_string_list(self.feature_flags)
        self.compatibility_flags = _normalize_string_list(self.compatibility_flags)
        self.warnings = _normalize_string_list(self.warnings)
        self.extra_sections = [dict(section) for section in self.extra_sections]
        self.validate()

    def validate(self) -> None:
        if not self.name:
            raise ValueError("AWG-профиль должен содержать имя.")
        if not self.peers:
            raise ValueError("В AWG-конфиге должна присутствовать хотя бы одна секция [Peer].")
        self.interface.validate()
        for peer in self.peers:
            peer.validate()
        semantics = resolve_awg_semantics(
            explicit_protocol_version=self.protocol_version,
            obfuscation_fields=self.interface.obfuscation.to_capability_dict(),
            has_unmapped_fields=self.has_unmapped_fields,
        )
        self.protocol_version = semantics.protocol_version
        self.version_source = semantics.version_source
        self.feature_flags = list(semantics.feature_flags)
        self.compatibility_flags = list(semantics.compatibility_flags)
        self.warnings = list(semantics.warnings)

    @property
    def capability_spec(self):
        return get_awg_capability_spec(self.protocol_version or "legacy")

    @property
    def has_unmapped_fields(self) -> bool:
        if self.interface.extra_fields or self.interface.obfuscation.extra_fields or self.extra_sections:
            return True
        return any(peer.extra_fields for peer in self.peers)

    @property
    def primary_peer(self) -> AmneziaWgPeer:
        return self.peers[0]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AmneziaWgProfile":
        return cls(
            name=str(data.get("name") or ""),
            interface=AmneziaWgInterface.from_dict(data.get("interface") or {}),
            peers=[AmneziaWgPeer.from_dict(item) for item in data.get("peers", [])],
            source_format=str(data.get("source_format") or "conf"),
            protocol_version=data.get("protocol_version"),
            version_source=str(data.get("version_source") or "inferred"),
            feature_flags=[str(value) for value in data.get("feature_flags", [])],
            compatibility_flags=[str(value) for value in data.get("compatibility_flags", [])],
            warnings=[str(value) for value in data.get("warnings", [])],
            extra_sections=[dict(section) for section in data.get("extra_sections", [])],
        )


@dataclass
class ServerEntry:
    id: str
    name: str
    protocol: str
    host: str
    port: int
    raw_link: str
    source: str = "manual"
    subscription_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)
    amneziawg_profile: AmneziaWgProfile | None = None

    @classmethod
    def new(
        cls,
        *,
        name: str,
        protocol: str,
        host: str,
        port: int,
        raw_link: str,
        extra: dict[str, Any] | None = None,
        source: str = "manual",
        subscription_id: str | None = None,
        amneziawg_profile: AmneziaWgProfile | None = None,
    ) -> "ServerEntry":
        return cls(
            id=str(uuid4()),
            name=name,
            protocol=protocol,
            host=host,
            port=port,
            raw_link=raw_link,
            source=source,
            subscription_id=subscription_id,
            extra=extra or {},
            amneziawg_profile=amneziawg_profile,
        )

    @classmethod
    def new_amneziawg(
        cls,
        *,
        name: str,
        profile: AmneziaWgProfile,
        raw_link: str = "",
        extra: dict[str, Any] | None = None,
        source: str = "manual",
        subscription_id: str | None = None,
    ) -> "ServerEntry":
        primary_peer = profile.primary_peer
        return cls.new(
            name=name,
            protocol="amneziawg",
            host=primary_peer.endpoint_host or "",
            port=primary_peer.endpoint_port or 0,
            raw_link=raw_link,
            extra=extra,
            source=source,
            subscription_id=subscription_id,
            amneziawg_profile=profile,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerEntry":
        profile_data = data.get("amneziawg_profile")
        profile = (
            profile_data
            if isinstance(profile_data, AmneziaWgProfile)
            else AmneziaWgProfile.from_dict(profile_data)
            if isinstance(profile_data, dict)
            else None
        )
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            protocol=str(data.get("protocol") or ""),
            host=str(data.get("host") or ""),
            port=int(data.get("port") or 0),
            raw_link=str(data.get("raw_link") or ""),
            source=str(data.get("source") or "manual"),
            subscription_id=data.get("subscription_id"),
            created_at=str(data.get("created_at") or utc_now_iso()),
            extra=_normalize_extra_fields(data.get("extra")),
            amneziawg_profile=profile,
        )

    @property
    def identity_token(self) -> str:
        if self.amneziawg_profile is not None:
            return self.amneziawg_profile.primary_peer.public_key
        return str(self.extra.get("id") or "") or str(self.extra.get("password") or "")

    @property
    def is_amneziawg(self) -> bool:
        return self.amneziawg_profile is not None


@dataclass
class SubscriptionEntry:
    id: str
    url: str
    title: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    server_ids: list[str] = field(default_factory=list)
    auto_update: bool = True
    last_error: str | None = None
    last_error_at: str | None = None

    @classmethod
    def new(cls, *, url: str, title: str) -> "SubscriptionEntry":
        return cls(id=str(uuid4()), url=url, title=title)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubscriptionEntry":
        return cls(**data)


@dataclass
class RuntimeState:
    pid: int | None = None
    helper_pid: int | None = None
    backend_id: str | None = None
    mode: str | None = None
    server_id: str | None = None
    started_at: str | None = None
    system_proxy_enabled: bool = False
    previous_system_proxy: dict[str, Any] | None = None
    routing_profile_id: str | None = None
    routing_profile_name: str | None = None
    tun_interface_name: str | None = None
    tun_interface_index: int | None = None
    tun_interface_ipv4: str | None = None
    tun_interface_addresses: list[str] = field(default_factory=list)
    tun_dns_servers: list[str] = field(default_factory=list)
    tun_route_prefixes: list[str] = field(default_factory=list)
    outbound_interface_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeState":
        return cls(
            pid=data.get("pid"),
            helper_pid=data.get("helper_pid"),
            backend_id=data.get("backend_id"),
            mode=data.get("mode"),
            server_id=data.get("server_id"),
            started_at=data.get("started_at"),
            system_proxy_enabled=_coerce_stored_bool(data.get("system_proxy_enabled"), default=False),
            previous_system_proxy=data.get("previous_system_proxy"),
            routing_profile_id=data.get("routing_profile_id"),
            routing_profile_name=data.get("routing_profile_name"),
            tun_interface_name=data.get("tun_interface_name"),
            tun_interface_index=data.get("tun_interface_index"),
            tun_interface_ipv4=data.get("tun_interface_ipv4"),
            tun_interface_addresses=[
                str(address)
                for address in data.get("tun_interface_addresses", [])
                if str(address).strip()
            ],
            tun_dns_servers=[
                str(server)
                for server in data.get("tun_dns_servers", [])
                if str(server).strip()
            ],
            tun_route_prefixes=[
                str(prefix)
                for prefix in data.get("tun_route_prefixes", [])
                if str(prefix).strip()
            ],
            outbound_interface_name=data.get("outbound_interface_name"),
        )

    @property
    def is_running(self) -> bool:
        return self.pid is not None


@dataclass(frozen=True)
class LocalProxyCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class ProxyRuntimeSession:
    socks_port: int
    http_port: int
    socks_credentials: LocalProxyCredentials


@dataclass
class AppSettings:
    active_routing_profile_id: str = "default"
    set_system_proxy: bool = True
    connection_mode: str = "PROXY"
    auto_update_subscriptions_on_startup: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        return cls(
            active_routing_profile_id=data.get("active_routing_profile_id", "default"),
            set_system_proxy=_coerce_stored_bool(data.get("set_system_proxy"), default=True),
            connection_mode=str(data.get("connection_mode", "PROXY") or "PROXY"),
            auto_update_subscriptions_on_startup=_coerce_stored_bool(
                data.get("auto_update_subscriptions_on_startup"),
                default=False,
            ),
        )
