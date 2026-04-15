from __future__ import annotations

from vynex_vpn_client.utils import _powershell_utf8_command


def test_powershell_utf8_command_forces_utf8_io() -> None:
    command = _powershell_utf8_command("Get-NetAdapter | ConvertTo-Json -Compress")

    assert "[Console]::InputEncoding = [System.Text.Encoding]::UTF8" in command
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in command
    assert "$OutputEncoding = [System.Text.Encoding]::UTF8" in command
    assert command.endswith("Get-NetAdapter | ConvertTo-Json -Compress")
