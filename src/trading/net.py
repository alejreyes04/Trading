"""Cliente HTTP compartido con reintentos, backoff exponencial y límite de ritmo."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RateLimiter:
    """Limita a `rate` peticiones por segundo (token bucket simple, thread-safe)."""

    def __init__(self, rate: float):
        self.interval = 1.0 / rate
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


class HttpClient:
    def __init__(self, base_url: str = "", rate: float = 10.0, timeout: float = 30.0,
                 headers: dict[str, str] | None = None, max_retries: int = 4):
        self._client = httpx.Client(base_url=base_url, timeout=timeout,
                                    headers={"User-Agent": "trading-research/0.1", **(headers or {})},
                                    follow_redirects=True)
        self._limiter = RateLimiter(rate)
        self.max_retries = max_retries
        self.calls = 0

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                resp = self._client.get(url, params=params)
                self.calls += 1
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("Error de red en %s (%s); reintento en %.0fs", url, exc, delay)
            else:
                if resp.status_code not in RETRY_STATUS:
                    resp.raise_for_status()
                    return resp
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
                log.warning("HTTP %s en %s; reintento en %.0fs", resp.status_code, url, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise RuntimeError("inalcanzable")

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(url, params).json()

    def close(self) -> None:
        self._client.close()
