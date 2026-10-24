import html
import logging
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from defusedxml import ElementTree

from f1_pitwall.domain.models import NewsArticle
from f1_pitwall.providers.http import ProviderHTTP, normalized

log = logging.getLogger(__name__)


def plain(value: str | None) -> str | None:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip() if value else None


def safe_url(value: str | None) -> str | None:
    return value if value and urlsplit(value).scheme in ("http", "https") else None


def published(value: str | None) -> datetime | None:
    """RFC 2822 date as aware UTC; '-0000' parses naive and garbage is treated as unknown."""
    if not value:
        return None
    try:
        stamp = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


class RSSProvider:
    def __init__(self, http: ProviderHTTP, url: str):
        self.http, self.url = http, url
        self._expires = 0.0
        self._articles: list[NewsArticle] = []

    @normalized
    async def articles(self) -> list[NewsArticle]:
        if self._expires > time.monotonic():
            return [a.model_copy(deep=True) for a in self._articles]
        # Cache only the allowed article metadata, not the raw feed/article bodies.
        raw = await self.http.get(self.url, 0)
        try:
            root = ElementTree.fromstring(raw)
        except Exception as exc:
            # XML parser/security failures are provider failures, not empty feeds.
            raise self.http.fail("rss", f"invalid XML: {type(exc).__name__}") from exc
        if root.tag != "rss":
            raise ValueError("expected RSS feed")
        articles, skipped = [], 0
        for item in root.findall("./channel/item"):
            title = plain(item.findtext("title"))
            url = safe_url(item.findtext("link"))
            if not title or not url:
                # One malformed item must not take the whole feed down.
                skipped += 1
                continue
            timestamp = published(item.findtext("pubDate"))
            image = item.find("{http://search.yahoo.com/mrss/}thumbnail")
            if image is None:
                image = item.find("{http://search.yahoo.com/mrss/}content")
            if image is None:
                image = item.find("enclosure")
                if image is not None and not image.get("type", "").startswith("image/"):
                    image = None
            articles.append(
                NewsArticle(
                    headline=title,
                    url=url,
                    source=self.http.name,
                    published_at=timestamp,
                    summary=(plain(item.findtext("description")) or "")[:600] or None,
                    image_url=safe_url(image.get("url")) if image is not None else None,
                    tags=[plain(c.text) for c in item.findall("category") if plain(c.text)],
                )
            )
        if skipped:
            log.warning("provider=%s skipped_rss_items=%s", self.http.name, skipped)
        self.http.status.errors.pop("rss", None)
        self._articles = articles
        self._expires = time.monotonic() + self.http.settings.short_ttl
        return articles
