from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field
import ipaddress
import json
from typing import Any
import zlib

from .amneziawg import parse_amneziawg_config_text
from .models import (
    AmneziaWgInterface,
    AmneziaWgObfuscationSettings,
    AmneziaWgPeer,
    AmneziaWgProfile,
    ServerEntry,
)
from .xray_import import parse_xray_json_config

_VPN_SCHEME = "vpn://"
_AMNEZIA_API_SIGNATURE = bytes.fromhex("000000ff")
_SUPPORTED_NATIVE_CONFIG_VERSIONS = frozenset({1, 2})
_SUPPORTED_CONTAINER_PROTOCOL_KEYS = ("awg", "wireguard", "xray", "ssxray")
_KNOWN_UNSUPPORTED_CONTAINER_PROTOCOL_KEYS = frozenset({"openvpn", "cloak", "ikev2", "shadowsocks", "sftp", "socks5proxy"})
_AWG_NATIVE_KNOWN_FIELDS = frozenset(
    {
        "config",
        "hostName",
        "port",
        "client_priv_key",
        "client_pub_key",
        "server_pub_key",
        "psk_key",
        "client_ip",
        "allowed_ips",
        "persistent_keep_alive",
        "mtu",
        "clientId",
        "protocol_version",
        "Jc",
        "Jmin",
        "Jmax",
        "S1",
        "S2",
        "S3",
        "S4",
        "H1",
        "H2",
        "H3",
        "H4",
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
    }
)


class VpnPayloadError(ValueError):
    pass


class UnsupportedVpnPayloadVersionError(VpnPayloadError):
    pass


class VpnPayloadDecodeError(VpnPayloadError):
    pass


class VpnPayloadMalformedError(VpnPayloadError):
    pass


class UnsupportedEmbeddedProtocolError(VpnPayloadError):
    pass


class IncompleteVpnConfigError(VpnPayloadError):
    pass


@dataclass
class VpnPayloadConnection:
    connection_id: str
    protocol: str
    importer: str
    source_container: str | None = None
    source_protocol_key: str | None = None
    is_default: bool = False
    status: str = "supported"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VpnPayloadEnvelope:
    scheme: str
    raw_uri: str
    compression: str
    payload_kind: str
    payload_version: int | None = None
    default_connection_id: str | None = None
    raw_config: dict[str, Any] | list[Any] | str | None = None
    notes: list[str] = field(default_factory=list)
    connections: list[VpnPayloadConnection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "raw_uri": self.raw_uri,
            "compression": self.compression,
            "payload_kind": self.payload_kind,
            "payload_version": self.payload_version,
            "default_connection_id": self.default_connection_id,
            "notes": list(self.notes),
            "connections": [connection.to_dict() for connection in self.connections],
            "raw_config": self.raw_config,
        }


def is_vpn_uri(value: str) -> bool:
    return value.strip().lower().startswith(_VPN_SCHEME)


def import_vpn_uri(
    uri: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
) -> ServerEntry:
    normalized = uri.strip()
    if not is_vpn_uri(normalized):
        raise VpnPayloadMalformedError("malformed payload: expected a vpn:// URI.")
    payload = normalized[len(_VPN_SCHEME) :]
    if not payload:
        raise VpnPayloadMalformedError("malformed payload: vpn:// URI does not contain a payload.")

    decoded_bytes, compression = _decode_vpn_payload(payload)
    try:
        decoded_text = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VpnPayloadDecodeError("decode error: payload is not valid UTF-8 text.") from exc

    body = decoded_text.strip()
    if not body:
        raise VpnPayloadMalformedError("malformed payload: decoded payload is empty.")

    try:
        decoded_json = json.loads(body)
    except json.JSONDecodeError:
        decoded_json = None

    if isinstance(decoded_json, dict) and _looks_like_amnezia_container_payload(decoded_json):
        return _import_amnezia_container_payload(
            decoded_json,
            raw_uri=normalized,
            compression=compression,
            source=source,
            subscription_id=subscription_id,
        )

    if isinstance(decoded_json, dict):
        try:
            servers = parse_xray_json_config(
                decoded_json,
                source=source,
                subscription_id=subscription_id,
                strict=True,
            )
        except ValueError as exc:
            raise VpnPayloadMalformedError("malformed payload: unsupported decoded JSON structure.") from exc
        if not servers:
            raise VpnPayloadMalformedError("malformed payload: unsupported decoded JSON structure.")
        server = servers[0]
        server.raw_link = normalized
        server.extra["vpn_payload"] = VpnPayloadEnvelope(
            scheme="vpn",
            raw_uri=normalized,
            compression=compression,
            payload_kind="xray-json",
            connections=[
                VpnPayloadConnection(
                    connection_id="embedded-xray-0",
                    protocol=server.protocol,
                    importer="xray",
                    is_default=True,
                )
            ],
            default_connection_id="embedded-xray-0",
            raw_config=decoded_json,
        ).to_dict()
        return server

    if "[Interface]" in body and "[Peer]" in body:
        return _server_from_awg_text_payload(
            body,
            raw_uri=normalized,
            compression=compression,
            source=source,
            subscription_id=subscription_id,
        )

    raise VpnPayloadMalformedError("malformed payload: unsupported decoded text structure.")


def _decode_vpn_payload(payload: str) -> tuple[bytes, str]:
    try:
        raw_bytes = base64.b64decode(_pad_base64(payload), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise VpnPayloadDecodeError("decode error: payload is not valid base64url.") from exc
    if not raw_bytes:
        raise VpnPayloadMalformedError("malformed payload: decoded payload is empty.")

    if len(raw_bytes) > 4 and raw_bytes.startswith(_AMNEZIA_API_SIGNATURE):
        try:
            return zlib.decompress(raw_bytes[4:]), "amnezia-signed-zlib"
        except zlib.error:
            pass

    qt_uncompressed = _try_qt_uncompress(raw_bytes)
    if qt_uncompressed is not None:
        return qt_uncompressed, "qt-qcompress"
    return raw_bytes, "raw"


def _try_qt_uncompress(payload: bytes) -> bytes | None:
    if len(payload) <= 4:
        return None
    try:
        return zlib.decompress(payload[4:])
    except zlib.error:
        return None


def _pad_base64(value: str) -> str:
    stripped = value.strip()
    return stripped + "=" * (-len(stripped) % 4)


def _looks_like_amnezia_container_payload(data: dict[str, Any]) -> bool:
    return any(
        key in data
        for key in (
            "containers",
            "defaultContainer",
            "config_version",
            "api_key",
            "auth_data",
            "api_config",
            "hostName",
            "userName",
            "password",
        )
    )


def _import_amnezia_container_payload(
    data: dict[str, Any],
    *,
    raw_uri: str,
    compression: str,
    source: str,
    subscription_id: str | None,
) -> ServerEntry:
    payload_version = _parse_payload_version(data.get("config_version"))
    if payload_version is not None and payload_version not in _SUPPORTED_NATIVE_CONFIG_VERSIONS:
        raise UnsupportedVpnPayloadVersionError(f"unsupported vpn payload version: {payload_version}")

    containers = data.get("containers")
    if not isinstance(containers, list) or not containers:
        raise IncompleteVpnConfigError("incomplete config: vpn payload does not contain connection containers.")

    envelope = VpnPayloadEnvelope(
        scheme="vpn",
        raw_uri=raw_uri,
        compression=compression,
        payload_kind="amnezia-container",
        payload_version=payload_version,
        raw_config=data,
    )

    supported_servers: list[tuple[str, ServerEntry]] = []
    default_container = str(data.get("defaultContainer") or "").strip() or None
    for index, container in enumerate(containers):
        if not isinstance(container, dict):
            raise VpnPayloadMalformedError("malformed payload: containers array contains a non-object entry.")
        summary, server = _import_container_connection(
            container,
            index=index,
            top_level=data,
            raw_uri=raw_uri,
            source=source,
            subscription_id=subscription_id,
        )
        envelope.connections.append(summary)
        if server is not None:
            supported_servers.append((summary.connection_id, server))

    if not supported_servers:
        raise UnsupportedEmbeddedProtocolError("unsupported embedded protocol: no supported connection containers were found.")

    selected_id: str | None = None
    selected_server: ServerEntry | None = None
    if default_container:
        for connection_id, server in supported_servers:
            if server.extra.get("vpn_payload_container") == default_container:
                selected_id = connection_id
                selected_server = server
                break
        if selected_server is None:
            # Assumption: if Amnezia marks an unavailable default container, it is
            # still better to import the first supported connection than to reject the key.
            envelope.notes.append(
                f"default container '{default_container}' is unavailable; selected the first supported connection instead"
            )

    if selected_server is None:
        selected_id, selected_server = supported_servers[0]

    for connection in envelope.connections:
        connection.is_default = connection.connection_id == selected_id

    envelope.default_connection_id = selected_id
    selected_server.extra.pop("vpn_payload_container", None)
    selected_server.extra["vpn_payload"] = envelope.to_dict()
    selected_server.raw_link = raw_uri
    return selected_server


def _import_container_connection(
    container: dict[str, Any],
    *,
    index: int,
    top_level: dict[str, Any],
    raw_uri: str,
    source: str,
    subscription_id: str | None,
) -> tuple[VpnPayloadConnection, ServerEntry | None]:
    container_name = str(container.get("container") or "").strip() or f"container-{index + 1}"
    protocol_key = _detect_container_protocol_key(container)
    connection_id = f"{container_name}:{protocol_key or 'unknown'}:{index}"
    if protocol_key is None:
        return (
            VpnPayloadConnection(
                connection_id=connection_id,
                protocol="unknown",
                importer="unsupported",
                source_container=container_name,
                status="unsupported",
                details={"reason": "unknown container protocol", "raw_container": container},
            ),
            None,
        )

    protocol_config = container.get(protocol_key)
    if not isinstance(protocol_config, dict):
        raise VpnPayloadMalformedError(f"malformed payload: container '{container_name}' has invalid '{protocol_key}' config.")
    protocol_details = {
        "raw_container": container,
        "raw_protocol_config": protocol_config,
    }

    if protocol_key in _KNOWN_UNSUPPORTED_CONTAINER_PROTOCOL_KEYS:
        return (
            VpnPayloadConnection(
                connection_id=connection_id,
                protocol=protocol_key,
                importer="unsupported",
                source_container=container_name,
                source_protocol_key=protocol_key,
                status="unsupported",
                details={**protocol_details, "reason": f"container '{protocol_key}' is not supported by this client"},
            ),
            None,
        )

    if protocol_key in {"awg", "wireguard"}:
        server = _server_from_awg_container(
            container_name=container_name,
            protocol_key=protocol_key,
            protocol_config=protocol_config,
            top_level=top_level,
            raw_uri=raw_uri,
            source=source,
            subscription_id=subscription_id,
        )
        server.extra["vpn_payload_container"] = container_name
        return (
            VpnPayloadConnection(
                connection_id=connection_id,
                protocol=server.protocol,
                importer="amneziawg",
                source_container=container_name,
                source_protocol_key=protocol_key,
                details=protocol_details,
            ),
            server,
        )

    if protocol_key in {"xray", "ssxray"}:
        server = _server_from_xray_container(
            container_name=container_name,
            protocol_key=protocol_key,
            protocol_config=protocol_config,
            top_level=top_level,
            raw_uri=raw_uri,
            source=source,
            subscription_id=subscription_id,
        )
        server.extra["vpn_payload_container"] = container_name
        return (
            VpnPayloadConnection(
                connection_id=connection_id,
                protocol=server.protocol,
                importer="xray",
                source_container=container_name,
                source_protocol_key=protocol_key,
                details=protocol_details,
            ),
            server,
        )

    return (
        VpnPayloadConnection(
            connection_id=connection_id,
            protocol=protocol_key,
            importer="unsupported",
            source_container=container_name,
            source_protocol_key=protocol_key,
            status="unsupported",
            details={**protocol_details, "reason": f"container '{protocol_key}' is not supported by this client"},
        ),
        None,
    )


def _detect_container_protocol_key(container: dict[str, Any]) -> str | None:
    for protocol_key in _SUPPORTED_CONTAINER_PROTOCOL_KEYS:
        if protocol_key in container:
            return protocol_key
    for protocol_key in _KNOWN_UNSUPPORTED_CONTAINER_PROTOCOL_KEYS:
        if protocol_key in container:
            return protocol_key
    return None


def _server_from_awg_container(
    *,
    container_name: str,
    protocol_key: str,
    protocol_config: dict[str, Any],
    top_level: dict[str, Any],
    raw_uri: str,
    source: str,
    subscription_id: str | None,
) -> ServerEntry:
    last_config_text = str(protocol_config.get("last_config") or "").strip()
    if not last_config_text:
        raise IncompleteVpnConfigError(f"incomplete config: container '{container_name}' does not contain last_config.")

    try:
        native_payload = json.loads(last_config_text)
    except json.JSONDecodeError:
        native_payload = {"config": last_config_text}
    if not isinstance(native_payload, dict):
        raise VpnPayloadMalformedError(f"malformed payload: container '{container_name}' has invalid AWG payload.")

    profile_name = _resolve_server_name(top_level, protocol_key)
    profile = _build_awg_profile(
        native_payload,
        name=profile_name,
        default_dns=_collect_dns(top_level),
        explicit_protocol_version=protocol_config.get("protocol_version") or native_payload.get("protocol_version"),
    )
    primary_peer = profile.primary_peer
    if not primary_peer.endpoint_host or primary_peer.endpoint_port is None:
        raise IncompleteVpnConfigError(f"incomplete config: container '{container_name}' is missing an endpoint.")
    return ServerEntry.new(
        name=profile_name,
        protocol="wireguard" if protocol_key == "wireguard" else "amneziawg",
        host=primary_peer.endpoint_host,
        port=primary_peer.endpoint_port,
        raw_link=raw_uri,
        extra={},
        source=source,
        subscription_id=subscription_id,
        amneziawg_profile=profile,
    )


def _build_awg_profile(
    native_payload: dict[str, Any],
    *,
    name: str,
    default_dns: list[str],
    explicit_protocol_version: Any,
) -> AmneziaWgProfile:
    raw_config = str(native_payload.get("config") or "").strip()
    if raw_config:
        try:
            parsed_server = parse_amneziawg_config_text(raw_config, name=name)
        except ValueError:
            profile_data = _build_awg_profile_dict_from_native_payload(native_payload, name=name)
        else:
            parsed_profile = parsed_server.amneziawg_profile
            if parsed_profile is None:
                profile_data = _build_awg_profile_dict_from_native_payload(native_payload, name=name)
            else:
                profile_data = parsed_profile.to_dict()
    else:
        profile_data = _build_awg_profile_dict_from_native_payload(native_payload, name=name)

    if default_dns and not profile_data["interface"].get("dns"):
        profile_data["interface"]["dns"] = list(default_dns)
    if explicit_protocol_version is not None:
        profile_data["protocol_version"] = explicit_protocol_version
        profile_data["version_source"] = "explicit"
    profile_data["source_format"] = "vpn://"
    return AmneziaWgProfile.from_dict(profile_data)


def _build_awg_profile_dict_from_native_payload(native_payload: dict[str, Any], *, name: str) -> dict[str, Any]:
    private_key = _require_text(native_payload.get("client_priv_key"), "client_priv_key")
    public_key = _require_text(native_payload.get("server_pub_key"), "server_pub_key")
    host = _require_text(native_payload.get("hostName"), "hostName")
    port = _require_port(native_payload.get("port"), "port")
    addresses = _normalize_awg_interface_addresses(native_payload.get("client_ip"))
    allowed_ips = _normalize_string_list(native_payload.get("allowed_ips"))
    if not addresses:
        raise IncompleteVpnConfigError("incomplete config: embedded AWG payload does not contain client_ip.")
    if not allowed_ips:
        raise IncompleteVpnConfigError("incomplete config: embedded AWG payload does not contain allowed_ips.")

    obfuscation_data = {
        "jc": _optional_int(native_payload.get("Jc")),
        "jmin": _optional_int(native_payload.get("Jmin")),
        "jmax": _optional_int(native_payload.get("Jmax")),
        "s1": _optional_int(native_payload.get("S1")),
        "s2": _optional_int(native_payload.get("S2")),
        "s3": _optional_int(native_payload.get("S3")),
        "s4": _optional_int(native_payload.get("S4")),
        "h1": _optional_text(native_payload.get("H1")),
        "h2": _optional_text(native_payload.get("H2")),
        "h3": _optional_text(native_payload.get("H3")),
        "h4": _optional_text(native_payload.get("H4")),
        "i1": _optional_text(native_payload.get("I1")),
        "i2": _optional_text(native_payload.get("I2")),
        "i3": _optional_text(native_payload.get("I3")),
        "i4": _optional_text(native_payload.get("I4")),
        "i5": _optional_text(native_payload.get("I5")),
    }
    obfuscation_extra = {
        key: value
        for key, value in native_payload.items()
        if key not in _AWG_NATIVE_KNOWN_FIELDS and key.startswith(("J", "S", "H", "I"))
    }
    interface = AmneziaWgInterface(
        private_key=private_key,
        addresses=addresses,
        mtu=_optional_int(native_payload.get("mtu")),
        obfuscation=AmneziaWgObfuscationSettings(
            **obfuscation_data,
            extra_fields=obfuscation_extra,
        ),
    )
    peer = AmneziaWgPeer(
        public_key=public_key,
        preshared_key=_optional_text(native_payload.get("psk_key")),
        endpoint_host=host,
        endpoint_port=port,
        allowed_ips=allowed_ips,
        keepalive=_optional_int(native_payload.get("persistent_keep_alive")),
    )
    return AmneziaWgProfile(
        name=name,
        interface=interface,
        peers=[peer],
        source_format="vpn-json",
        protocol_version=native_payload.get("protocol_version"),
    ).to_dict()


def _server_from_xray_container(
    *,
    container_name: str,
    protocol_key: str,
    protocol_config: dict[str, Any],
    top_level: dict[str, Any],
    raw_uri: str,
    source: str,
    subscription_id: str | None,
) -> ServerEntry:
    last_config_text = str(protocol_config.get("last_config") or "").strip()
    if not last_config_text:
        raise IncompleteVpnConfigError(f"incomplete config: container '{container_name}' does not contain last_config.")
    try:
        servers = parse_xray_json_config(
            last_config_text,
            source=source,
            subscription_id=subscription_id,
            name=_resolve_server_name(top_level, protocol_key),
            strict=True,
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("unsupported embedded protocol:"):
            raise UnsupportedEmbeddedProtocolError(message) from exc
        raise IncompleteVpnConfigError(message) from exc
    if not servers:
        raise UnsupportedEmbeddedProtocolError(
            f"unsupported embedded protocol: container '{container_name}' does not expose a supported Xray outbound."
        )
    server = servers[0]
    if protocol_key == "ssxray" and server.protocol != "ss":
        raise UnsupportedEmbeddedProtocolError(
            f"unsupported embedded protocol: container '{container_name}' is not a Shadowsocks outbound."
        )
    server.raw_link = raw_uri
    return server


def _server_from_awg_text_payload(
    text: str,
    *,
    raw_uri: str,
    compression: str,
    source: str,
    subscription_id: str | None,
) -> ServerEntry:
    server = parse_amneziawg_config_text(text, source=source, subscription_id=subscription_id)
    server.raw_link = raw_uri
    server.extra["vpn_payload"] = VpnPayloadEnvelope(
        scheme="vpn",
        raw_uri=raw_uri,
        compression=compression,
        payload_kind="wireguard-text",
        connections=[
            VpnPayloadConnection(
                connection_id="embedded-awg-0",
                protocol=server.protocol,
                importer="amneziawg",
                is_default=True,
            )
        ],
        default_connection_id="embedded-awg-0",
        raw_config=text,
    ).to_dict()
    return server


def _resolve_server_name(top_level: dict[str, Any], protocol_key: str) -> str:
    base_name = str(top_level.get("description") or top_level.get("name") or top_level.get("hostName") or "").strip()
    if not base_name:
        return _protocol_label(protocol_key)
    supported_containers = [
        container
        for container in top_level.get("containers") or []
        if isinstance(container, dict) and _detect_container_protocol_key(container) in _SUPPORTED_CONTAINER_PROTOCOL_KEYS
    ]
    if len(supported_containers) <= 1:
        return base_name
    return f"{base_name} ({_protocol_label(protocol_key)})"


def _protocol_label(protocol_key: str) -> str:
    labels = {
        "awg": "AmneziaWG",
        "wireguard": "WireGuard",
        "xray": "Xray",
        "ssxray": "Shadowsocks",
    }
    return labels.get(protocol_key, protocol_key)


def _collect_dns(top_level: dict[str, Any]) -> list[str]:
    return [value for value in (str(top_level.get("dns1") or "").strip(), str(top_level.get("dns2") or "").strip()) if value]


def _parse_payload_version(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UnsupportedVpnPayloadVersionError(f"unsupported vpn payload version: {value}") from exc


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def _normalize_awg_interface_addresses(value: Any) -> list[str]:
    normalized: list[str] = []
    for item in _normalize_string_list(value):
        if "/" in item:
            normalized.append(item)
            continue
        try:
            address = ipaddress.ip_address(item)
        except ValueError:
            normalized.append(item)
            continue
        prefix = 32 if address.version == 4 else 128
        normalized.append(f"{item}/{prefix}")
    return normalized


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise IncompleteVpnConfigError(f"incomplete config: missing {field_name}.")
    return text


def _require_port(value: Any, field_name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise IncompleteVpnConfigError(f"incomplete config: invalid {field_name}.") from exc
    if not 1 <= port <= 65535:
        raise IncompleteVpnConfigError(f"incomplete config: invalid {field_name}.")
    return port


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
