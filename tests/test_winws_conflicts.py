from __future__ import annotations

from unittest.mock import Mock, patch

from vynex_vpn_client.app import VynexVpnApp
from vynex_vpn_client.models import ServerEntry
from vynex_vpn_client.utils import RunningProcessDetails


def _make_app() -> VynexVpnApp:
    app = object.__new__(VynexVpnApp)
    app.console = Mock()
    app.console.width = 120
    app._render_screen = Mock()
    app._pause = Mock()
    return app


def _confirm_prompt(answer: bool | None) -> Mock:
    prompt = Mock()
    prompt.ask.return_value = answer
    return prompt


def _winws_processes() -> list[RunningProcessDetails]:
    return [
        RunningProcessDetails(pid=101, name="Winws.exe"),
        RunningProcessDetails(pid=202, name="Winws2.exe"),
    ]


def test_ensure_winws_conflicts_resolved_returns_true_when_no_processes_found() -> None:
    app = _make_app()

    with (
        patch("vynex_vpn_client.app.list_running_processes_by_names", return_value=[]),
        patch("vynex_vpn_client.app.questionary.confirm") as confirm,
        patch("vynex_vpn_client.app.terminate_running_processes") as terminate_processes,
    ):
        assert app._ensure_winws_conflicts_resolved() is True

    confirm.assert_not_called()
    terminate_processes.assert_not_called()
    app.console.print.assert_not_called()
    app._pause.assert_not_called()


def test_ensure_winws_conflicts_resolved_terminates_processes_after_confirmation() -> None:
    app = _make_app()
    conflicts = _winws_processes()

    with (
        patch("vynex_vpn_client.app.list_running_processes_by_names", return_value=conflicts),
        patch("vynex_vpn_client.app.questionary.confirm", return_value=_confirm_prompt(True)),
        patch("vynex_vpn_client.app.terminate_running_processes", return_value=[]) as terminate_processes,
    ):
        assert app._ensure_winws_conflicts_resolved() is True

    terminate_processes.assert_called_once_with(conflicts)
    app._pause.assert_not_called()


def test_ensure_winws_conflicts_resolved_cancels_connection_when_user_declines() -> None:
    app = _make_app()
    conflicts = _winws_processes()

    with (
        patch("vynex_vpn_client.app.list_running_processes_by_names", return_value=conflicts),
        patch("vynex_vpn_client.app.questionary.confirm", return_value=_confirm_prompt(False)),
        patch("vynex_vpn_client.app.terminate_running_processes") as terminate_processes,
    ):
        assert app._ensure_winws_conflicts_resolved() is False

    terminate_processes.assert_not_called()
    app._pause.assert_called_once()


def test_ensure_winws_conflicts_resolved_raises_when_termination_fails() -> None:
    app = _make_app()
    conflicts = _winws_processes()

    with (
        patch("vynex_vpn_client.app.list_running_processes_by_names", return_value=conflicts),
        patch("vynex_vpn_client.app.questionary.confirm", return_value=_confirm_prompt(True)),
        patch("vynex_vpn_client.app.terminate_running_processes", return_value=[conflicts[0]]),
    ):
        try:
            app._ensure_winws_conflicts_resolved()
        except RuntimeError as exc:
            message = str(exc)
            assert "Winws.exe" in message
            assert "PID 101" in message
        else:
            raise AssertionError("Expected RuntimeError when Winws.exe cannot be terminated")


def test_connect_flow_stops_before_runtime_setup_when_winws_conflict_is_not_resolved() -> None:
    app = _make_app()
    server = ServerEntry.new(
        name="Test server",
        protocol="vmess",
        host="example.com",
        port=443,
        raw_link="vmess://test",
    )
    selection_prompt = Mock()
    selection_prompt.ask.return_value = server.id
    app.storage = Mock()
    app.storage.load_servers.return_value = [server]
    app._select = Mock(return_value=selection_prompt)
    app._validated_settings = Mock()
    app._ensure_winws_conflicts_resolved = Mock(return_value=False)

    app.connect_flow()

    app._ensure_winws_conflicts_resolved.assert_called_once_with()
    app._validated_settings.assert_not_called()

