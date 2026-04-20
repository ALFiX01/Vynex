from __future__ import annotations

from unittest.mock import Mock, patch

import psutil

from vynex_vpn_client.utils import _powershell_utf8_command
from vynex_vpn_client.utils import RunningProcessDetails, terminate_running_processes


def test_powershell_utf8_command_forces_utf8_io() -> None:
    command = _powershell_utf8_command("Get-NetAdapter | ConvertTo-Json -Compress")

    assert "[Console]::InputEncoding = [System.Text.Encoding]::UTF8" in command
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in command
    assert "$OutputEncoding = [System.Text.Encoding]::UTF8" in command
    assert command.endswith("Get-NetAdapter | ConvertTo-Json -Compress")


def test_terminate_running_processes_returns_failed_process_when_wait_access_is_denied() -> None:
    process_info = RunningProcessDetails(pid=12552, name="winws2.exe")
    process = Mock()
    process.pid = process_info.pid
    process.name.return_value = process_info.name
    process.wait.side_effect = psutil.AccessDenied(pid=process_info.pid, name=process_info.name)

    with (
        patch("vynex_vpn_client.utils.psutil.Process", return_value=process),
        patch("vynex_vpn_client.utils.time.sleep"),
    ):
        failed = terminate_running_processes([process_info], timeout=0.0, kill_timeout=0.0)

    assert failed == [process_info]
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
