from __future__ import annotations

from unittest.mock import call, patch

import pytest

from vynex_vpn_client.system_proxy import SystemProxyState, WindowsSystemProxyManager


def test_enable_proxy_rolls_back_registry_state_when_broadcast_fails() -> None:
    manager = WindowsSystemProxyManager()
    previous_state = SystemProxyState(
        proxy_enable=0,
        proxy_server="",
        proxy_override="*.local",
        auto_config_url="https://example.com/proxy.pac",
        auto_detect=1,
    )

    with (
        patch.object(manager, "snapshot", return_value=previous_state),
        patch.object(manager, "_write_state") as write_state,
        patch.object(
            manager,
            "_broadcast_settings_changed",
            side_effect=[OSError("broadcast failed"), None],
        ),
    ):
        with pytest.raises(OSError, match="broadcast failed"):
            manager.enable_proxy(http_port=18080, socks_port=10808)

    target_state = write_state.call_args_list[0].args[0]
    assert target_state.proxy_enable == 1
    assert target_state.proxy_server == "http=127.0.0.1:18080;https=127.0.0.1:18080;socks=127.0.0.1:10808"
    assert target_state.proxy_override == "<local>"
    assert target_state.auto_config_url == ""
    assert target_state.auto_detect == 1
    assert write_state.call_args_list == [call(target_state), call(previous_state)]
