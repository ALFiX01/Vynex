from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .amneziawg_capabilities import requires_runtime_not_implemented_error
from .constants import AMNEZIAWG_RUNTIME_DIR
from .models import AmneziaWgObfuscationSettings, AmneziaWgProfile

_MAX_TUNNEL_NAME_LENGTH = 32
_TUNNEL_NAME_SANITIZER = re.compile(r"[^A-Za-z0-9_=+.-]+")
_SENSITIVE_FIELD_NAMES = frozenset({"privatekey", "presharedkey"})
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)


@dataclass(frozen=True)
class AmneziaWgRuntimeArtifacts:
    backend_id: str
    config_format: str
    launch_input_kind: str
    runtime_dir: Path
    config_path: Path
    tunnel_name: str

    @property
    def files(self) -> tuple[Path, ...]:
        return (self.config_path,)

    def to_debug_dict(self) -> dict[str, Any]:
        config_preview = None
        try:
            if self.config_path.exists():
                config_preview = mask_sensitive_config_text(self.config_path.read_text(encoding="utf-8"))
        except OSError:
            config_preview = None
        return {
            "backend_id": self.backend_id,
            "config_format": self.config_format,
            "launch_input_kind": self.launch_input_kind,
            "runtime_dir": str(self.runtime_dir),
            "config_path": str(self.config_path),
            "tunnel_name": self.tunnel_name,
            "files": [str(path) for path in self.files],
            "config_preview": config_preview,
        }


class AmneziaWgRuntimeBuilder:
    def __init__(self, runtime_root: Path | None = None) -> None:
        self.runtime_root = Path(runtime_root) if runtime_root is not None else AMNEZIAWG_RUNTIME_DIR

    def build_runtime(self, profile: AmneziaWgProfile) -> AmneziaWgRuntimeArtifacts:
        profile.validate()
        runtime_root = self._ensure_runtime_root()
        runtime_dir = Path(tempfile.mkdtemp(prefix="awg-runtime-", dir=str(runtime_root)))
        tunnel_name = _sanitize_tunnel_name(profile.name)
        config_path = runtime_dir / f"{tunnel_name}.conf"
        config_text = _build_wg_quick_config(profile)
        try:
            config_path.write_text(config_text, encoding="utf-8")
        except OSError:
            shutil.rmtree(runtime_dir, ignore_errors=True)
            raise
        _tighten_runtime_file_permissions(config_path)
        return AmneziaWgRuntimeArtifacts(
            backend_id="amneziawg",
            config_format="wg-quick-conf",
            launch_input_kind="conf_path",
            runtime_dir=runtime_dir,
            config_path=config_path,
            tunnel_name=tunnel_name,
        )

    def cleanup_runtime(self, artifacts: AmneziaWgRuntimeArtifacts | None) -> None:
        if artifacts is None:
            return
        shutil.rmtree(artifacts.runtime_dir, ignore_errors=True)

    def _ensure_runtime_root(self) -> Path:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        return self.runtime_root


_DEFAULT_RUNTIME_BUILDER = AmneziaWgRuntimeBuilder()


def build_runtime(profile: AmneziaWgProfile) -> AmneziaWgRuntimeArtifacts:
    return _DEFAULT_RUNTIME_BUILDER.build_runtime(profile)


def cleanup_runtime(artifacts: AmneziaWgRuntimeArtifacts | None) -> None:
    _DEFAULT_RUNTIME_BUILDER.cleanup_runtime(artifacts)


def mask_sensitive_config_text(text: str) -> str:
    masked_lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if "=" not in stripped:
            masked_lines.append(raw_line)
            continue
        key, value = stripped.split("=", 1)
        if key.strip().lower() not in _SENSITIVE_FIELD_NAMES:
            masked_lines.append(raw_line)
            continue
        masked_value = _mask_secret_value(value.strip())
        masked_lines.append(f"{key.strip()} = {masked_value}")
    return "\n".join(masked_lines)


def _mask_secret_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return "<empty>"
    if len(normalized) <= 8:
        return "***"
    return f"{normalized[:4]}...{normalized[-4:]}"


def _sanitize_tunnel_name(name: str) -> str:
    candidate = _TUNNEL_NAME_SANITIZER.sub("-", str(name or "").strip())
    candidate = candidate.strip(" .-")
    if not candidate:
        candidate = "awg"
    if candidate.upper() in _RESERVED_WINDOWS_NAMES:
        candidate = f"{candidate}_awg"
    candidate = candidate[:_MAX_TUNNEL_NAME_LENGTH].rstrip(" .")
    if not candidate:
        return "awg"
    if candidate.upper() in _RESERVED_WINDOWS_NAMES:
        reserved_safe = f"{candidate[:_MAX_TUNNEL_NAME_LENGTH - 4]}_awg".rstrip(" .")
        return reserved_safe or "awg"
    return candidate


def _build_wg_quick_config(profile: AmneziaWgProfile) -> str:
    runtime_error = requires_runtime_not_implemented_error(
        profile.protocol_version or "legacy",
        profile.interface.obfuscation.to_capability_dict(),
    )
    if runtime_error is not None:
        raise NotImplementedError(runtime_error)

    interface = profile.interface
    capability_spec = profile.capability_spec
    lines = [
        "[Interface]",
        f"PrivateKey = {interface.private_key}",
    ]
    _append_optional_int(lines, "ListenPort", interface.listen_port)
    _append_obfuscation_settings(lines, interface.obfuscation, profile_version=capability_spec.protocol_version)
    lines.append(f"Address = {', '.join(interface.addresses)}")
    if interface.dns:
        lines.append(f"DNS = {', '.join(interface.dns)}")
    _append_optional_int(lines, "MTU", interface.mtu)

    # TODO: Surface profile.warnings in the connection flow before backend launch.
    # The parser now preserves version/capability warnings, but the UI still does not show them proactively.

    # TODO: Map additional AWG-specific fields only after confirming backend support.
    # The current amneziawg-windows parser rejects unknown keys, so extra fields are omitted on purpose.
    for peer in profile.peers:
        lines.extend(
            (
                "",
                "[Peer]",
                f"PublicKey = {peer.public_key}",
            )
        )
        if peer.preshared_key:
            lines.append(f"PresharedKey = {peer.preshared_key}")
        lines.append(f"AllowedIPs = {', '.join(peer.allowed_ips)}")
        lines.append(f"Endpoint = {_format_endpoint(peer.endpoint_host or '', peer.endpoint_port or 0)}")
        _append_optional_int(lines, "PersistentKeepalive", peer.keepalive)
    return "\n".join(lines) + "\n"


def _append_obfuscation_settings(
    lines: list[str],
    settings: AmneziaWgObfuscationSettings,
    *,
    profile_version: str,
) -> None:
    _append_optional_int(lines, "Jc", settings.jc)
    _append_optional_int(lines, "Jmin", settings.jmin)
    _append_optional_int(lines, "Jmax", settings.jmax)
    _append_optional_int(lines, "S1", settings.s1)
    _append_optional_int(lines, "S2", settings.s2)
    if profile_version == "2.0":
        _append_optional_int(lines, "S3", settings.s3)
        _append_optional_int(lines, "S4", settings.s4)
    _append_optional_text(lines, "H1", settings.h1)
    _append_optional_text(lines, "H2", settings.h2)
    _append_optional_text(lines, "H3", settings.h3)
    _append_optional_text(lines, "H4", settings.h4)
    for key in ("i1", "i2", "i3", "i4", "i5"):
        _append_optional_text(lines, key.upper(), getattr(settings, key))


def _append_optional_int(lines: list[str], key: str, value: int | None) -> None:
    if value is None:
        return
    lines.append(f"{key} = {value}")


def _append_optional_text(lines: list[str], key: str, value: str | None) -> None:
    if value is None:
        return
    normalized = str(value).strip()
    if not normalized:
        return
    lines.append(f"{key} = {normalized}")


def _format_endpoint(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _tighten_runtime_file_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass
