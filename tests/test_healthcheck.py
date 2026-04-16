from __future__ import annotations

from unittest.mock import patch

from vynex_vpn_client.healthcheck import XrayHealthChecker


def test_healthcheck_marks_all_timeouts_as_inconclusive() -> None:
    checker = XrayHealthChecker()

    with patch.object(
        checker,
        "_probe_url",
        return_value=(False, "timeout", None, True),
    ):
        result = checker._probe(attempts=1, timeout=1, request_kwargs={})

    assert result.ok is False
    assert result.inconclusive is True


def test_healthcheck_marks_http_errors_as_conclusive() -> None:
    checker = XrayHealthChecker()

    with patch.object(
        checker,
        "_probe_url",
        return_value=(False, "HTTP 403", None, False),
    ):
        result = checker._probe(attempts=1, timeout=1, request_kwargs={})

    assert result.ok is False
    assert result.inconclusive is False
