from __future__ import annotations

import time
from unittest.mock import Mock
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


def test_healthcheck_returns_without_waiting_for_slowest_endpoint() -> None:
    checker = XrayHealthChecker()

    def probe(url: str, timeout: int, request_kwargs: dict) -> tuple[bool, str, str | None, bool]:
        del timeout, request_kwargs
        if "cloudflare" in url:
            time.sleep(0.3)
            return False, "slow timeout", None, True
        if "google" in url:
            return True, "ok", url, False
        return False, "HTTP 503", None, False

    started_at = time.perf_counter()
    with patch.object(checker, "_probe_url", side_effect=probe):
        result = checker._probe(attempts=1, timeout=1, request_kwargs={})
    elapsed = time.perf_counter() - started_at

    assert result.ok is True
    assert result.checked_url is not None
    assert elapsed < 0.2


def test_healthcheck_reuses_session_within_same_thread() -> None:
    checker = XrayHealthChecker()
    session = Mock()
    session.get.return_value = Mock(ok=True)

    with patch("vynex_vpn_client.healthcheck.requests.Session", return_value=session) as session_factory:
        checker._probe_url("https://example.com/one", 1, {})
        checker._probe_url("https://example.com/two", 1, {})

    assert session_factory.call_count == 1
    assert session.get.call_count == 2
