import asyncio
import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import wraps

import httpx
from pydantic import ValidationError

from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.models import DataSourceStatus

log = logging.getLogger(__name__)


class ProviderHTTP:
    """Bounded TTL cache, per-provider serialization/rate pacing, transient GET retries."""

    def __init__(self, name: str, client: httpx.AsyncClient, settings: Settings):
        self.name, self.client, self.settings = name, client, settings
        self.status = DataSourceStatus(provider=name)
        self.cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    def fail(self, resource: str, message: str) -> ProviderError:
        self.status.checked_at = datetime.now(UTC)
        self.status.errors[resource] = message
        # Keep observability memory bounded too.
        if len(self.status.errors) > self.settings.cache_size:
            self.status.errors.pop(next(iter(self.status.errors)))
        self.status.status = "degraded"
        log.warning("provider=%s resource=%s error=%s", self.name, resource, message)
        return ProviderError(self.name, message)

    async def get(
        self, url: str, ttl: float, *, empty_on_no_results: bool = False, **params
    ) -> bytes:
        key = str(httpx.URL(url, params=params))
        async with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic():
                self.cache.move_to_end(key)
                return cached[1]
            for attempt in range(self.settings.retries + 1):
                await asyncio.sleep(max(0, 0.35 - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                try:
                    response = await self.client.get(
                        url, params=params, timeout=self.settings.timeout
                    )
                    no_results = False
                    if empty_on_no_results and response.status_code == 404:
                        try:
                            no_results = response.json() == {"detail": "No results found."}
                        except ValueError:
                            pass
                    if not no_results:
                        response.raise_for_status()
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                    retry = not isinstance(exc, httpx.HTTPStatusError) or (
                        exc.response.status_code in (408, 429, 500, 502, 503, 504)
                    )
                    if not retry or attempt == self.settings.retries:
                        message = (
                            f"HTTP {exc.response.status_code}"
                            if isinstance(exc, httpx.HTTPStatusError)
                            else type(exc).__name__
                        )
                        raise self.fail(key, message) from exc
                    delay = 0.5 * 2**attempt
                    if isinstance(exc, httpx.HTTPStatusError):
                        header = exc.response.headers.get("Retry-After")
                        if header:
                            try:
                                delay = float(header)
                            except ValueError:
                                try:
                                    delay = (
                                        parsedate_to_datetime(header) - datetime.now(UTC)
                                    ).total_seconds()
                                except (ValueError, TypeError):
                                    pass
                            if delay > 10:
                                raise self.fail(key, "rate limited; retry later") from exc
                    log.info("provider=%s retry=%s", self.name, attempt + 1)
                    await asyncio.sleep(max(0, min(delay, 10)))
                    continue
                except httpx.HTTPError as exc:
                    raise self.fail(key, type(exc).__name__) from exc
                now = datetime.now(UTC)
                self.status.checked_at = self.status.last_success_at = now
                self.status.errors.pop(key, None)
                self.status.status = "degraded" if self.status.errors else "ok"
                if no_results:
                    self.status.unavailable_resources[key] = "No results found"
                    if len(self.status.unavailable_resources) > self.settings.cache_size:
                        self.status.unavailable_resources.pop(
                            next(iter(self.status.unavailable_resources))
                        )
                else:
                    self.status.unavailable_resources.pop(key, None)
                content = b"[]" if no_results else response.content
                if ttl > 0:
                    self.cache[key] = (time.monotonic() + ttl, content)
                    self.cache.move_to_end(key)
                while len(self.cache) > self.settings.cache_size:
                    self.cache.popitem(last=False)
                return content
        raise AssertionError("unreachable")


def normalized(method):
    """Turn provider schema drift into a visible, clean upstream error."""

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        try:
            result = await method(self, *args, **kwargs)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            self.http.cache.clear()
            raise self.http.fail(
                method.__name__, f"invalid provider schema: {type(exc).__name__}"
            ) from exc
        self.http.status.errors.pop(method.__name__, None)
        self.http.status.status = "degraded" if self.http.status.errors else "ok"
        return result

    return wrapped
