from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .constants import HEALTHCHECK_ATTEMPTS, HEALTHCHECK_TIMEOUT, HEALTHCHECK_URLS, LOCAL_PROXY_HOST


@dataclass
class HealthcheckResult:
    ok: bool
    message: str
    checked_url: str | None = None


class XrayHealthChecker:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "Vynex-Client/1.0"})

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
        errors: list[str] = []
        for attempt in range(1, attempts + 1):
            for url in HEALTHCHECK_URLS:
                try:
                    response = self.session.get(
                        url,
                        timeout=timeout,
                        allow_redirects=True,
                        **request_kwargs,
                    )
                    if response.ok:
                        return HealthcheckResult(
                            ok=True,
                            message=f"Health-check успешен: {url}",
                            checked_url=url,
                        )
                    errors.append(f"{url}: HTTP {response.status_code}")
                except requests.RequestException as exc:
                    errors.append(f"{url}: {exc}")
            if attempt < attempts:
                time.sleep(min(attempt, 2))
        message = " | ".join(errors[-3:]) if errors else "Сетевой запрос не был выполнен."
        return HealthcheckResult(ok=False, message=message)
