from __future__ import annotations

from dataclasses import dataclass
import ipaddress

from .models import AmneziaWgProfile, RuntimeState
from .utils import (
    get_interface_details,
    get_interface_dns_servers,
    get_interface_ipv4_addresses,
    get_interface_ipv4_route_prefixes,
    is_running_as_admin,
    remove_interface_ipv4_addresses,
    remove_ipv4_route,
    reset_interface_dns_servers,
)


class AmneziaWgNetworkError(RuntimeError):
    pass


class AmneziaWgAdminRequiredError(AmneziaWgNetworkError, PermissionError):
    pass


class AmneziaWgInterfaceConflictError(AmneziaWgNetworkError):
    pass


class AmneziaWgAddressApplyError(AmneziaWgNetworkError):
    pass


class AmneziaWgRouteApplyError(AmneziaWgNetworkError):
    pass


class AmneziaWgDnsApplyError(AmneziaWgNetworkError):
    pass


@dataclass(frozen=True)
class AmneziaWgExpectedNetworkState:
    tunnel_name: str
    interface_addresses: tuple[str, ...]
    dns_required: bool
    dns_servers: tuple[str, ...]
    require_exact_dns_match: bool
    route_prefixes: tuple[str, ...]
    full_tunnel: bool


@dataclass(frozen=True)
class AmneziaWgNetworkSession:
    tunnel_name: str
    interface_name: str
    interface_index: int
    primary_ipv4: str | None
    interface_addresses: tuple[str, ...]
    dns_servers: tuple[str, ...]
    route_prefixes: tuple[str, ...]
    full_tunnel: bool


class AmneziaWgWindowsNetworkIntegration:
    def ensure_prerequisites(self, *, tunnel_name: str) -> None:
        if not is_running_as_admin():
            raise AmneziaWgAdminRequiredError(
                "AmneziaWG TUN режим требует запуска приложения от имени администратора."
            )
        existing = get_interface_details(tunnel_name, allow_link_local=True)
        if existing is None:
            return
        raise AmneziaWgInterfaceConflictError(
            "В Windows уже существует конфликтующий интерфейс AmneziaWG "
            f"'{existing.alias}' (index {existing.index}). Остановите его и повторите попытку."
        )

    def capture_session(self, *, profile: AmneziaWgProfile, tunnel_name: str) -> AmneziaWgNetworkSession:
        expected = self.build_expected_state(profile=profile, tunnel_name=tunnel_name)
        details = get_interface_details(tunnel_name, allow_link_local=True)
        if details is None:
            raise AmneziaWgNetworkError(
                f"Интерфейс AmneziaWG '{tunnel_name}' не найден после запуска backend."
            )

        actual_addresses = get_interface_ipv4_addresses(details.alias, allow_link_local=True)
        missing_addresses = [
            address
            for address in expected.interface_addresses
            if str(ipaddress.ip_interface(address).ip) not in set(actual_addresses)
        ]
        if missing_addresses:
            raise AmneziaWgAddressApplyError(
                "AmneziaWG поднял интерфейс, но Windows не применила ожидаемые IPv4-адреса: "
                + ", ".join(missing_addresses)
            )

        actual_routes = set(get_interface_ipv4_route_prefixes(interface_index=details.index))
        missing_routes = [prefix for prefix in expected.route_prefixes if prefix not in actual_routes]
        if missing_routes:
            route_mode = "full-tunnel" if expected.full_tunnel else "split-tunnel"
            raise AmneziaWgRouteApplyError(
                "AmneziaWG поднял интерфейс, но Windows не применила маршруты AllowedIPs "
                f"для режима {route_mode}: {', '.join(missing_routes)}"
            )

        actual_dns = get_interface_dns_servers(details.alias)
        if expected.dns_required:
            if not actual_dns:
                raise AmneziaWgDnsApplyError(
                    "AmneziaWG поднял интерфейс, но Windows не применила DNS сервера из профиля."
                )
            if expected.dns_servers and expected.require_exact_dns_match:
                missing_dns = [server for server in expected.dns_servers if server not in set(actual_dns)]
                if missing_dns:
                    raise AmneziaWgDnsApplyError(
                        "AmneziaWG поднял интерфейс, но Windows не применила DNS сервера: "
                        + ", ".join(missing_dns)
                    )

        return AmneziaWgNetworkSession(
            tunnel_name=expected.tunnel_name,
            interface_name=details.alias,
            interface_index=details.index,
            primary_ipv4=details.ipv4,
            interface_addresses=expected.interface_addresses,
            dns_servers=expected.dns_servers,
            route_prefixes=expected.route_prefixes,
            full_tunnel=expected.full_tunnel,
        )

    def cleanup_runtime_state(self, state: RuntimeState) -> None:
        if str(state.backend_id or "").lower() != "amneziawg":
            return
        if state.tun_interface_index is not None:
            for prefix in state.tun_route_prefixes:
                remove_ipv4_route(prefix, interface_index=state.tun_interface_index)
        if state.tun_interface_name:
            if state.tun_dns_servers:
                reset_interface_dns_servers(state.tun_interface_name)
            if state.tun_interface_addresses:
                remove_interface_ipv4_addresses(
                    state.tun_interface_name,
                    state.tun_interface_addresses,
                )

    def build_expected_state(self, *, profile: AmneziaWgProfile, tunnel_name: str) -> AmneziaWgExpectedNetworkState:
        interface_addresses = self._ipv4_interface_addresses(profile)
        route_prefixes = self._ipv4_route_prefixes(profile)
        dns_servers, require_exact_dns_match = self._dns_expectation(profile)
        full_tunnel = "0.0.0.0/0" in set(route_prefixes)
        return AmneziaWgExpectedNetworkState(
            tunnel_name=tunnel_name,
            interface_addresses=interface_addresses,
            dns_required=bool(profile.interface.dns),
            dns_servers=dns_servers,
            require_exact_dns_match=require_exact_dns_match,
            route_prefixes=route_prefixes,
            full_tunnel=full_tunnel,
        )

    @staticmethod
    def _ipv4_interface_addresses(profile: AmneziaWgProfile) -> tuple[str, ...]:
        addresses: list[str] = []
        for value in profile.interface.addresses:
            interface = ipaddress.ip_interface(value)
            if interface.version != 4:
                continue
            normalized = str(interface)
            if normalized not in addresses:
                addresses.append(normalized)
        return tuple(addresses)

    @staticmethod
    def _ipv4_route_prefixes(profile: AmneziaWgProfile) -> tuple[str, ...]:
        networks: list[ipaddress.IPv4Network] = []
        for peer in profile.peers:
            for value in peer.allowed_ips:
                network = ipaddress.ip_network(value, strict=False)
                if network.version != 4:
                    continue
                ipv4_network = ipaddress.IPv4Network(str(network), strict=False)
                if ipv4_network not in networks:
                    networks.append(ipv4_network)
        ordered = sorted(networks, key=lambda item: (int(item.network_address), item.prefixlen))
        return tuple(str(network) for network in ordered)

    @staticmethod
    def _dns_expectation(profile: AmneziaWgProfile) -> tuple[tuple[str, ...], bool]:
        servers: list[str] = []
        require_exact_dns_match = True
        for value in profile.interface.dns:
            normalized = str(value).strip()
            if not normalized:
                continue
            try:
                parsed = ipaddress.ip_address(normalized)
            except ValueError:
                require_exact_dns_match = False
                continue
            if parsed.version != 4:
                continue
            if normalized not in servers:
                servers.append(normalized)
        return tuple(servers), require_exact_dns_match


# TODO: Add IPv6 route/DNS verification when the app stores IPv6 tunnel session state explicitly.
