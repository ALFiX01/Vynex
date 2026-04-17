from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from .amneziawg_process_manager import AmneziaWgProcessManager
from .amneziawg_runtime import AmneziaWgRuntimeArtifacts, AmneziaWgRuntimeBuilder
from .config_builder import XrayConfigBuilder
from .core import SingboxInstaller, XrayInstaller
from .models import ProxyRuntimeSession, ServerEntry
from .process_manager import SingboxProcessManager, State, XrayProcessManager
from .routing_profiles import RoutingProfile
from .singbox_config_builder import SingboxConfigBuilder

AMNEZIAWG_PROTOCOLS = frozenset(
    {
        "awg",
        "amneziawg",
        "amnezia-wg",
        "amnesiawg",
        "wireguard",
    }
)
SINGBOX_PROTOCOLS = frozenset({"hy2", "hysteria2"})


class BackendProcessController(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def state(self) -> State: ...

    def start(self, config: dict[str, Any]) -> int: ...

    def stop(self, pid: int | None = None) -> None: ...

    def restart(self, config: dict[str, Any]) -> int: ...

    def is_running(self, pid: int | None) -> bool: ...

    def status(self) -> State: ...

    def ensure_no_running_instances(self, *, exclude_pid: int | None = None) -> None: ...

    def read_recent_output(self, limit: int = 15) -> str: ...

    def collect_output(self, limit: int = 50) -> Any: ...


@dataclass(frozen=True)
class BackendConnectionProfile:
    server: ServerEntry
    mode: str
    routing_profile: RoutingProfile

    @property
    def normalized_mode(self) -> str:
        return str(self.mode or "").upper()


@dataclass(frozen=True)
class BackendRuntimeRequest:
    profile: BackendConnectionProfile
    proxy_session: ProxyRuntimeSession | None = None
    outbound_interface_name: str | None = None


class BaseVpnBackend(ABC):
    backend_id = "base"
    engine_name = "vpn"
    engine_title = "VPN"
    tun_interface_name: str | None = None
    tun_route_prefixes: tuple[str, ...] = ()
    supports_crash_recovery = False

    @property
    @abstractmethod
    def process_controller(self) -> BackendProcessController | None:
        raise NotImplementedError

    @abstractmethod
    def supports_connection(self, profile: BackendConnectionProfile) -> bool:
        raise NotImplementedError

    @abstractmethod
    def ensure_runtime_ready(self, profile: BackendConnectionProfile) -> None:
        raise NotImplementedError

    @abstractmethod
    def build_runtime_config(self, request: BackendRuntimeRequest) -> dict[str, Any]:
        raise NotImplementedError

    def connection_mode_label(self, mode: str) -> str:
        return (
            "TUN (игры и весь трафик)"
            if str(mode or "").upper() == "TUN"
            else "PROXY (браузер и приложения)"
        )


class XrayBackend(BaseVpnBackend):
    backend_id = "xray"
    engine_name = "xray"
    engine_title = "Xray"
    tun_interface_name = XrayConfigBuilder.TUN_INTERFACE_NAME
    tun_route_prefixes = XrayConfigBuilder.TUN_ROUTE_PREFIXES
    supports_crash_recovery = True

    def __init__(
        self,
        *,
        installer: XrayInstaller | None,
        config_builder: XrayConfigBuilder,
        process_manager: XrayProcessManager,
    ) -> None:
        self.installer = installer
        self.config_builder = config_builder
        self._process_manager = process_manager

    @property
    def process_controller(self) -> BackendProcessController:
        return self._process_manager

    def supports_connection(self, profile: BackendConnectionProfile) -> bool:
        protocol = profile.server.protocol.lower()
        return protocol not in AMNEZIAWG_PROTOCOLS and protocol not in SINGBOX_PROTOCOLS

    def ensure_runtime_ready(self, profile: BackendConnectionProfile) -> None:
        if self.installer is None:
            raise RuntimeError("Xray backend не инициализирован: отсутствует installer.")
        if profile.normalized_mode == "TUN":
            self.installer.ensure_xray_tun_runtime()
            return
        self.installer.ensure_xray()

    def build_runtime_config(self, request: BackendRuntimeRequest) -> dict[str, Any]:
        profile = request.profile
        if profile.normalized_mode == "TUN":
            return self.config_builder.build(
                server=profile.server,
                mode=profile.mode,
                routing_profile=profile.routing_profile,
                outbound_interface_name=request.outbound_interface_name,
            )
        if request.proxy_session is None:
            raise ValueError("Для Proxy режима Xray backend нужен runtime-сеанс локального proxy.")
        return self.config_builder.build(
            server=profile.server,
            mode=profile.mode,
            routing_profile=profile.routing_profile,
            socks_port=request.proxy_session.socks_port,
            http_port=request.proxy_session.http_port,
            socks_credentials=request.proxy_session.socks_credentials,
        )


class SingboxBackend(BaseVpnBackend):
    backend_id = "singbox"
    engine_name = "sing-box"
    engine_title = "sing-box"
    tun_interface_name = SingboxConfigBuilder.TUN_INTERFACE_NAME
    tun_route_prefixes = ()

    def __init__(
        self,
        *,
        installer: SingboxInstaller | None,
        config_builder: SingboxConfigBuilder,
        process_manager: SingboxProcessManager,
    ) -> None:
        self.installer = installer
        self.config_builder = config_builder
        self._process_manager = process_manager

    @property
    def process_controller(self) -> BackendProcessController:
        return self._process_manager

    def supports_connection(self, profile: BackendConnectionProfile) -> bool:
        return profile.server.protocol.lower() in SINGBOX_PROTOCOLS

    def ensure_runtime_ready(self, profile: BackendConnectionProfile) -> None:
        if self.installer is None:
            raise RuntimeError("sing-box backend не инициализирован: отсутствует installer.")
        self.installer.ensure_singbox()

    def build_runtime_config(self, request: BackendRuntimeRequest) -> dict[str, Any]:
        profile = request.profile
        if profile.normalized_mode == "TUN":
            return self.config_builder.build(
                server=profile.server,
                mode=profile.mode,
                routing_profile=profile.routing_profile,
            )
        if request.proxy_session is None:
            raise ValueError("Для Proxy режима sing-box backend нужен runtime-сеанс локального proxy.")
        return self.config_builder.build(
            server=profile.server,
            mode=profile.mode,
            routing_profile=profile.routing_profile,
            socks_port=request.proxy_session.socks_port,
            http_port=request.proxy_session.http_port,
            socks_credentials=request.proxy_session.socks_credentials,
        )


class AmneziaWgBackend(BaseVpnBackend):
    backend_id = "amneziawg"
    engine_name = "amneziawg"
    engine_title = "AmneziaWG"

    def __init__(
        self,
        *,
        installer: XrayInstaller | None = None,
        runtime_builder: AmneziaWgRuntimeBuilder | None = None,
        process_manager: AmneziaWgProcessManager | None = None,
    ) -> None:
        self.installer = installer
        self.runtime_builder = runtime_builder or AmneziaWgRuntimeBuilder()
        self._process_manager = process_manager or AmneziaWgProcessManager()

    @property
    def process_controller(self) -> BackendProcessController:
        return self._process_manager

    def supports_connection(self, profile: BackendConnectionProfile) -> bool:
        return profile.server.protocol.lower() in AMNEZIAWG_PROTOCOLS

    def ensure_runtime_ready(self, profile: BackendConnectionProfile) -> None:
        self._require_awg_profile(profile)
        if profile.normalized_mode != "TUN":
            raise NotImplementedError("AmneziaWG backend пока поддерживает только режим TUN.")
        if self.installer is not None:
            self.installer.ensure_amneziawg_runtime()

    def build_runtime(self, profile: BackendConnectionProfile) -> AmneziaWgRuntimeArtifacts:
        return self.runtime_builder.build_runtime(self._require_awg_profile(profile))

    def cleanup_runtime(self, artifacts: AmneziaWgRuntimeArtifacts | None) -> None:
        self.runtime_builder.cleanup_runtime(artifacts)

    def build_runtime_config(self, request: BackendRuntimeRequest) -> dict[str, Any]:
        profile = request.profile
        if profile.normalized_mode != "TUN":
            raise NotImplementedError("AmneziaWG backend пока поддерживает только режим TUN.")
        artifacts = self.build_runtime(profile)
        awg_profile = self._require_awg_profile(profile)
        return {
            "backend_id": self.backend_id,
            "protocol_version": awg_profile.protocol_version,
            "feature_flags": list(awg_profile.feature_flags),
            "compatibility_flags": list(awg_profile.compatibility_flags),
            "warnings": list(awg_profile.warnings),
            "config_format": artifacts.config_format,
            "launch_input_kind": artifacts.launch_input_kind,
            "runtime_dir": str(artifacts.runtime_dir),
            "config_path": str(artifacts.config_path),
            "tunnel_name": artifacts.tunnel_name,
            "startup_timeout": 12.0,
            "stop_timeout": 5.0,
            "require_interface_ready": True,
        }

    @staticmethod
    def _require_awg_profile(profile: BackendConnectionProfile):
        awg_profile = profile.server.amneziawg_profile
        if awg_profile is None:
            raise ValueError("Для AmneziaWG backend требуется заполненный amneziawg_profile.")
        return awg_profile


def select_backend(
    backends: dict[str, BaseVpnBackend],
    profile: BackendConnectionProfile,
    *,
    default_backend_id: str = "xray",
) -> BaseVpnBackend:
    for backend in backends.values():
        if backend.supports_connection(profile):
            return backend
    return backends[default_backend_id]
