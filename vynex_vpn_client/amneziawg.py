from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    AmneziaWgInterface,
    AmneziaWgObfuscationSettings,
    AmneziaWgPeer,
    AmneziaWgProfile,
    ServerEntry,
)

_AMNEZIA_HINT_FIELDS = frozenset(
    {
        "jc",
        "jmin",
        "jmax",
        "s1",
        "s2",
        "s3",
        "s4",
        "h1",
        "h2",
        "h3",
        "h4",
        "i1",
        "i2",
        "i3",
        "i4",
        "i5",
        "j1",
        "j2",
        "j3",
        "itime",
    }
)


@dataclass
class _ParsedSection:
    name: str
    fields: list[tuple[str, str]] = field(default_factory=list)

    @property
    def normalized_name(self) -> str:
        return self.name.strip().lower()


def parse_amneziawg_config_text(
    text: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
    name: str | None = None,
) -> ServerEntry:
    sections = _parse_sections(text)
    profile = _build_profile(
        sections,
        name=name,
        source_format="conf-text",
        require_awg_hint=False,
    )
    return ServerEntry.new_amneziawg(
        name=profile.name,
        profile=profile,
        source=source,
        subscription_id=subscription_id,
    )


def parse_amneziawg_config_file(
    path: str | Path,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
    name: str | None = None,
) -> ServerEntry:
    file_path = Path(path).expanduser()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Не удалось прочитать AWG-конфиг из файла: {file_path}") from exc
    sections = _parse_sections(text)
    profile = _build_profile(
        sections,
        name=name or file_path.stem,
        source_format="conf-file",
        require_awg_hint=False,
    )
    return ServerEntry.new_amneziawg(
        name=profile.name,
        profile=profile,
        source=source,
        subscription_id=subscription_id,
    )


def try_parse_amneziawg_config_text(
    text: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
    name: str | None = None,
    require_awg_hint: bool = True,
) -> ServerEntry | None:
    if not _is_potential_awg_payload(text):
        return None
    sections = _parse_sections(text)
    if not _looks_like_awg_config(sections, require_awg_hint=require_awg_hint):
        return None
    profile = _build_profile(
        sections,
        name=name,
        source_format="conf-text",
        require_awg_hint=require_awg_hint,
    )
    return ServerEntry.new_amneziawg(
        name=profile.name,
        profile=profile,
        source=source,
        subscription_id=subscription_id,
    )


def try_parse_amneziawg_config_file(
    path: str | Path,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
    name: str | None = None,
) -> ServerEntry | None:
    file_path = Path(path).expanduser()
    if file_path.suffix.lower() != ".conf" or not file_path.is_file():
        return None
    return parse_amneziawg_config_file(
        file_path,
        source=source,
        subscription_id=subscription_id,
        name=name,
    )


def is_probable_amneziawg_config(text: str) -> bool:
    if not _is_potential_awg_payload(text):
        return False
    return _looks_like_awg_config(_parse_sections(text), require_awg_hint=True)


def _build_profile(
    sections: list[_ParsedSection],
    *,
    name: str | None,
    source_format: str,
    require_awg_hint: bool,
) -> AmneziaWgProfile:
    if not _looks_like_awg_config(sections, require_awg_hint=require_awg_hint):
        raise ValueError("Текст не похож на AmneziaWG-конфиг.")

    interface_sections = [section for section in sections if section.normalized_name == "interface"]
    if len(interface_sections) != 1:
        raise ValueError("В AWG-конфиге должна быть ровно одна секция [Interface].")

    peer_sections = [section for section in sections if section.normalized_name == "peer"]
    if not peer_sections:
        raise ValueError("В AWG-конфиге отсутствует секция [Peer].")

    profile_peers = [_build_peer(section) for section in peer_sections]
    resolved_name = str(name or "").strip() or _default_profile_name(profile_peers)
    extra_sections = [
        {
            "name": section.name,
            "fields": _pack_fields(section.fields),
        }
        for section in sections
        if section.normalized_name not in {"interface", "peer"}
    ]
    return AmneziaWgProfile(
        name=resolved_name,
        interface=_build_interface(interface_sections[0]),
        peers=profile_peers,
        source_format=source_format,
        extra_sections=extra_sections,
    )


def _build_interface(section: _ParsedSection) -> AmneziaWgInterface:
    fields = _pack_fields(section.fields)
    obfuscation_extra: dict[str, Any] = {}
    interface_extra: dict[str, Any] = {}

    known_interface_fields = {
        "privatekey",
        "address",
        "dns",
        "mtu",
        "listenport",
        *tuple(_AMNEZIA_HINT_FIELDS),
    }

    for key, value in fields.items():
        normalized_key = key.lower()
        if normalized_key in {"j1", "j2", "j3", "itime"}:
            obfuscation_extra[key] = value
        elif normalized_key not in known_interface_fields:
            interface_extra[key] = value

    return AmneziaWgInterface(
        private_key=_required_scalar(fields, "PrivateKey"),
        addresses=_csv_values(fields, "Address"),
        dns=_csv_values(fields, "DNS"),
        mtu=_optional_int(fields, "MTU"),
        listen_port=_optional_int(fields, "ListenPort"),
        obfuscation=AmneziaWgObfuscationSettings(
            jc=_optional_int(fields, "Jc"),
            jmin=_optional_int(fields, "Jmin"),
            jmax=_optional_int(fields, "Jmax"),
            s1=_optional_int(fields, "S1"),
            s2=_optional_int(fields, "S2"),
            s3=_optional_int(fields, "S3"),
            s4=_optional_int(fields, "S4"),
            h1=_optional_text(fields, "H1"),
            h2=_optional_text(fields, "H2"),
            h3=_optional_text(fields, "H3"),
            h4=_optional_text(fields, "H4"),
            i1=_optional_text(fields, "I1"),
            i2=_optional_text(fields, "I2"),
            i3=_optional_text(fields, "I3"),
            i4=_optional_text(fields, "I4"),
            i5=_optional_text(fields, "I5"),
            extra_fields=obfuscation_extra,
        ),
        extra_fields=interface_extra,
    )


def _build_peer(section: _ParsedSection) -> AmneziaWgPeer:
    fields = _pack_fields(section.fields)
    endpoint_host, endpoint_port = _parse_endpoint(_required_scalar(fields, "Endpoint"))
    peer_extra = {
        key: value
        for key, value in fields.items()
        if key.lower()
        not in {"publickey", "presharedkey", "allowedips", "endpoint", "persistentkeepalive"}
    }
    return AmneziaWgPeer(
        public_key=_required_scalar(fields, "PublicKey"),
        preshared_key=_optional_text(fields, "PresharedKey"),
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        allowed_ips=_csv_values(fields, "AllowedIPs"),
        keepalive=_optional_int(fields, "PersistentKeepalive"),
        extra_fields=peer_extra,
    )


def _default_profile_name(peers: list[AmneziaWgPeer]) -> str:
    primary_peer = peers[0]
    return f"{primary_peer.endpoint_host}:{primary_peer.endpoint_port}"


def _looks_like_awg_config(
    sections: list[_ParsedSection],
    *,
    require_awg_hint: bool,
) -> bool:
    if not sections:
        return False
    section_names = [section.normalized_name for section in sections]
    if "interface" not in section_names or "peer" not in section_names:
        return False
    if not require_awg_hint:
        return True
    return any(key.lower() in _AMNEZIA_HINT_FIELDS for section in sections for key, _ in section.fields)


def _is_potential_awg_payload(text: str) -> bool:
    normalized = text.strip().lower()
    return "[interface]" in normalized or "[peer]" in normalized


def _parse_sections(text: str) -> list[_ParsedSection]:
    sections: list[_ParsedSection] = []
    current: _ParsedSection | None = None
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = _strip_inline_comment(raw_line).strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = _ParsedSection(name=stripped[1:-1].strip())
            sections.append(current)
            continue
        if "=" not in stripped:
            raise ValueError(f"Некорректная строка AWG-конфига ({line_number}): ожидается key = value.")
        if current is None:
            raise ValueError(f"Параметр вне секции AWG-конфига ({line_number}).")
        key, value = stripped.split("=", 1)
        current.fields.append((key.strip(), value.strip()))
    return sections


def _strip_inline_comment(line: str) -> str:
    in_single_quotes = False
    in_double_quotes = False
    for index, char in enumerate(line):
        if char == "'" and not in_double_quotes:
            in_single_quotes = not in_single_quotes
            continue
        if char == '"' and not in_single_quotes:
            in_double_quotes = not in_double_quotes
            continue
        if char not in {"#", ";"} or in_single_quotes or in_double_quotes:
            continue
        if index == 0 or line[index - 1].isspace():
            return line[:index].rstrip()
    return line.rstrip()


def _pack_fields(fields: list[tuple[str, str]]) -> dict[str, Any]:
    packed: dict[str, Any] = {}
    for key, value in fields:
        existing = packed.get(key)
        if existing is None:
            packed[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            packed[key] = [existing, value]
    return packed


def _lookup_field(fields: dict[str, Any], key: str) -> Any:
    for existing_key, value in fields.items():
        if existing_key.lower() == key.lower():
            return value
    return None


def _required_scalar(fields: dict[str, Any], key: str) -> str:
    value = _lookup_field(fields, key)
    if value is None:
        raise ValueError(f"В AWG-конфиге отсутствует обязательное поле {key}.")
    if isinstance(value, list):
        return str(value[-1]).strip()
    return str(value).strip()


def _optional_text(fields: dict[str, Any], key: str) -> str | None:
    value = _lookup_field(fields, key)
    if value is None:
        return None
    if isinstance(value, list):
        value = value[-1]
    text = str(value).strip()
    return text or None


def _optional_int(fields: dict[str, Any], key: str) -> int | None:
    text = _optional_text(fields, key)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Поле {key} в AWG-конфиге должно быть целым числом.") from exc


def _csv_values(fields: dict[str, Any], key: str) -> list[str]:
    value = _lookup_field(fields, key)
    if value is None:
        return []
    chunks = value if isinstance(value, list) else [value]
    items: list[str] = []
    for chunk in chunks:
        items.extend(part.strip() for part in str(chunk).split(",") if part.strip())
    return items


def _parse_endpoint(value: str) -> tuple[str, int]:
    endpoint = value.strip()
    if not endpoint:
        raise ValueError("В AWG-конфиге отсутствует Endpoint в секции [Peer].")
    if endpoint.startswith("["):
        end_index = endpoint.find("]")
        if end_index <= 0 or end_index + 1 >= len(endpoint) or endpoint[end_index + 1] != ":":
            raise ValueError("Поле Endpoint в AWG-конфиге должно иметь формат [IPv6]:port.")
        host = endpoint[1:end_index].strip()
        port_text = endpoint[end_index + 2 :].strip()
    else:
        if endpoint.count(":") != 1:
            raise ValueError("Поле Endpoint в AWG-конфиге должно иметь формат host:port.")
        host, port_text = (part.strip() for part in endpoint.rsplit(":", 1))
    if not host or not port_text:
        raise ValueError("Поле Endpoint в AWG-конфиге должно содержать host и port.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("Поле Endpoint в AWG-конфиге содержит некорректный порт.") from exc
    return host, port
