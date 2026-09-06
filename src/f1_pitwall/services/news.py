import asyncio
from datetime import UTC, datetime
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.models import NewsFeed
from f1_pitwall.providers.news import RSSProvider
from f1_pitwall.providers.openf1 import name_key


def url_key(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    return urlunsplit(
        ("https", parts.netloc.lower(), parts.path.rstrip("/"), urlencode(sorted(query)), "")
    )


class NewsService:
    def __init__(self, providers: list[RSSProvider]):
        self.providers = providers

    async def get_latest_news(self, limit: int = 20, query: str | None = None) -> NewsFeed:
        async def fetch(provider):
            try:
                return await provider.articles()
            except ProviderError:
                return []  # Failure remains visible in this feed's provider_status.

        groups = await asyncio.gather(*(fetch(p) for p in self.providers))
        articles = sorted(
            (a for group in groups for a in group),
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        unique, urls, titles = [], set(), []
        for article in articles:
            if (
                query
                and query.casefold() not in f"{article.headline} {article.summary or ''}".casefold()
            ):
                continue
            key, title = url_key(article.url), name_key(article.headline)
            if key in urls or any(SequenceMatcher(None, title, t).ratio() >= 0.94 for t in titles):
                continue
            unique.append(article)
            urls.add(key)
            titles.append(title)
            if len(unique) >= limit:
                break
        return NewsFeed(articles=unique, provider_status=[p.http.status for p in self.providers])
