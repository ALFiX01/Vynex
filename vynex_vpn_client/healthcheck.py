from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading

import requests

from .constants import HEALTHCHECK_ATTEMPTS, HEALTHCHECK_TIMEOUT, HEALTHCHECK_URLS, LOCAL_PROXY_HOST


@dataclass
class HealthcheckResult:
    ok: bool
    message: str
    checked_url: str | None = None
    inconclusive: bool = False


class XrayHealthChecker:
    def __init__(self) -> None:
        self._headers = {"User-Agent": "Vynex-Client/1.0"}
        self._thread_local = threading.local()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, len(HEALTHCHECK_URLS)),
            thread_name_prefix="vynex-healthcheck",
        )

    def verify_proxy(
        self,
        *,
        http_port: int,
        attempts: int = HEALTHCHECK_ATTEMPTS,
        timeout: int = HEALTHCHECK_TIMEOUT,
    ) -> HealthcheckResult:
        proxies = {
            "http": f"http://{LOCAL_PROXY_HOST}:{http_port}",
            "https": f"http://{LOCAL_PROXY_HOST}:{http_port}",
        }
        return self._probe(
            attempts=attempts,
            timeout=timeout,
            request_kwargs={"proxies": proxies},
        )

    def verify_direct(
        self,
        *,
        attempts: int = HEALTHCHECK_ATTEMPTS,
        timeout: int = HEALTHCHECK_TIMEOUT,
    ) -> HealthcheckResult:
        return self._probe(
            attempts=attempts,
            timeout=timeout,
            request_kwargs={},
        )

    def _probe(
        self,
        *,
        attempts: int,
        timeout: int,
        request_kwargs: dict,
    ) -> HealthcheckResult:
        return asyncio.run(
            self._probe_async(
                attempts=attempts,
                timeout=timeout,
                request_kwargs=request_kwargs,
            )
        )

    async def _probe_async(
        self,
        *,
        attempts: int,
        timeout: int,
        request_kwargs: dict,
    ) -> HealthcheckResult:
        errors: list[str] = []
        all_failures_are_timeouts = True
        loop = asyncio.get_running_loop()
        for attempt in range(1, attempts + 1):
            tasks = [
                loop.run_in_executor(
                    self._executor,
                    self._probe_url,
                    url,
                    timeout,
                    request_kwargs,
                )
                for url in HEALTHCHECK_URLS
            ]
            try:
                for task in asyncio.as_completed(tasks):
                    ok, message, checked_url, is_timeout = await task
                    if ok:
                        return HealthcheckResult(ok=True, message=message, checked_url=checked_url)
                    errors.append(message)
                    if not is_timeout:
                        all_failures_are_timeouts = False
            finally:
                for task in tasks:
                    task.cancel()
            if attempt < attempts:
                await asyncio.sleep(min(attempt, 2))
        message = " | ".join(errors[-3:]) if errors else "Сетевой запрос не был выполнен."
        return HealthcheckResult(
            ok=False,
            message=message,
            inconclusive=bool(errors) and all_failures_are_timeouts,
        )

    def _probe_url(self, url: str, timeout: int, request_kwargs: dict) -> tuple[bool, str, str | None, bool]:
        session = self._session()
        try:
            response = session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                **request_kwargs,
            )
            if response.ok:
                return True, f"Health-check успешен: {url}", url, False
            return False, f"{url}: HTTP {response.status_code}", None, False
        except requests.Timeout as exc:
            return False, f"{url}: {exc}", None, True
        except requests.RequestException as exc:
            return False, f"{url}: {exc}", None, False

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.headers.update(self._headers)
            self._thread_local.session = session
        return session
