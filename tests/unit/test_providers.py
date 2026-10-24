import asyncio

import httpx
import pytest

from f1_pitwall.core.config import Settings
from f1_pitwall.core.exceptions import ProviderError
from f1_pitwall.domain.models import Driver
from f1_pitwall.providers.http import ProviderHTTP
from f1_pitwall.providers.jolpica import Jolpica, event
from f1_pitwall.providers.news import RSSProvider
from f1_pitwall.providers.openf1 import UNAUTHORIZED_HINT, OpenF1, OpenF1Auth, Weekend
from f1_pitwall.services.news import NewsService


def driver_row(i=1):
    return {"driverId": f"driver_{i}", "givenName": "Test", "familyName": str(i)}


def constructor_row():
    return {"constructorId": "test_team", "name": "Test Team"}


def response_table(table, key, rows, total=None):
    return {"MRData": {"total": str(total if total is not None else len(rows)), table: {key: rows}}}


async def test_pagination_full_expanded_driver_list():
    offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(
            200,
            json=response_table(
                "DriverTable",
                "Drivers",
                [driver_row(i) for i in range(offset, min(offset + 11, 33))],
                total=33,
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = Jolpica(ProviderHTTP("jolpica", client, Settings()))
        drivers = await provider.drivers(2030)
        assert len(drivers) == 33 and len({d.id for d in drivers}) == 33
        assert offsets == [0, 11, 22]
        assert all(d.number is None for d in drivers)


@pytest.mark.parametrize("status", ["Did not start", "Disqualified", "Engine", "Finished"])
async def test_result_missing_optional_values(status):
    row = {"Driver": driver_row(), "Constructor": constructor_row(), "status": status, "grid": "0"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=response_table("RaceTable", "Races", [{"Results": [row]}])
            )
        )
    ) as client:
        provider = Jolpica(ProviderHTTP("jolpica", client, Settings()))
        result = (await provider.results(2030, 1))[0]
        assert result.status == status and result.time is None and result.position is None
        assert result.grid == 0


async def test_nested_result_pagination():
    def handler(request):
        offset = int(request.url.params["offset"])
        rows = [
            {"Driver": driver_row(i), "Constructor": constructor_row()}
            for i in range(offset, min(offset + 2, 5))
        ]
        return httpx.Response(
            200, json=response_table("RaceTable", "Races", [{"Results": rows}], 5)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await Jolpica(ProviderHTTP("jolpica", client, Settings())).results(2030, 1)
        assert len(rows) == 5 and len({r.driver.id for r in rows}) == 5


async def test_schema_drift_is_observable():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"changed_schema": []}))
    ) as client:
        provider = Jolpica(ProviderHTTP("jolpica", client, Settings()))
        with pytest.raises(ProviderError, match="schema"):
            await provider.drivers(2030)
        assert provider.http.status.status == "degraded"


@pytest.mark.parametrize(
    "body,empty", [({"detail": "No results found."}, True), ({"detail": "Not Found"}, False)]
)
async def test_openf1_no_results_distinct_from_broken_endpoint(body, empty):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json=body))
    ) as client:
        provider = OpenF1(ProviderHTTP("openf1", client, Settings()))
        if empty:
            assert await provider.weekends(2021) == []
            assert provider.http.status.unavailable_resources
            assert provider.http.status.status == "ok"
        else:
            with pytest.raises(ProviderError):
                await provider.weekends(2021)


@pytest.mark.parametrize("code,expected", [(503, 2), (429, 2), (404, 1), (401, 1), (403, 1)])
async def test_retry_only_transient_errors(code, expected):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(code, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ProviderHTTP("test", client, Settings(retries=1))
        with pytest.raises(ProviderError):
            await provider.get("https://test.invalid/data", 10)
        assert count == expected and provider.status.status == "degraded"


async def test_cache_expiry_bounding_and_concurrent_requests():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = ProviderHTTP("test", client, Settings(cache_size=2))
        await asyncio.gather(*(p.get("https://test.invalid/a", 10) for _ in range(5)))
        assert calls == 1
        p.cache["https://test.invalid/a"] = (0, b"expired")
        await p.get("https://test.invalid/a", 10)
        await p.get("https://test.invalid/b", 10)
        await p.get("https://test.invalid/c", 10)
        assert calls == 4 and len(p.cache) == 2


async def test_provider_timeout_is_explicit():
    async def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ProviderHTTP("test", client, Settings(retries=0))
        with pytest.raises(ProviderError, match="ReadTimeout"):
            await provider.get("https://test.invalid/data", 1)
        assert provider.status.status == "degraded"


async def test_news_deduplication_failure_and_filtering():
    rss = b"""<rss><channel><item><title>Driver wins test race</title>
    <link>https://news.example/a?utm_source=feed</link>
    <pubDate>Sun, 06 Sep 2026 08:00:00 GMT</pubDate>
    <description>Short summary</description></item>
    <item><title>Driver wins test race!</title><link>https://news.example/b</link></item>
    </channel></rss>"""

    def handler(request):
        return (
            httpx.Response(503)
            if request.url.host == "bad.example"
            else httpx.Response(200, content=rss)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        providers = [
            RSSProvider(ProviderHTTP(name, client, Settings(retries=0)), f"https://{name}.example")
            for name in ("good", "bad")
        ]
        news = NewsService(providers)
        result = await news.get_latest_news(10)
        assert len(result.articles) == 1
        assert result.articles[0].source == "good"
        assert result.articles[0].published_at is not None
        assert result.provider_status[1].status == "degraded"
        assert not (await news.get_latest_news(10, "unrelated")).articles
        assert not providers[0].http.cache  # Raw RSS/article bodies are never cached.


async def test_news_invalid_xml_not_silent_empty_feed():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<broken"))
    ) as client:
        provider = RSSProvider(ProviderHTTP("test", client, Settings()), "https://test.invalid")
        with pytest.raises(ProviderError):
            await provider.articles()
        assert provider.http.status.status == "degraded"


def test_jolpica_dynamic_schedule_missing_time():
    e = event(
        {
            "season": "2030",
            "round": "1",
            "raceName": "Test GP",
            "date": "2030-06-09",
            "Circuit": {"circuitId": "test", "circuitName": "Test Circuit"},
            "Sprint": {"date": "2030-06-08", "time": "12:00:00Z"},
            "SprintQualifying": {"date": "2030-06-07", "time": "10:00:00Z"},
        }
    )
    assert [s.name for s in e.sessions] == ["Sprint Qualifying", "Sprint", "Race"]
    assert e.sessions[-1].start is None


async def test_openf1_grid_identity_not_number():
    def handler(request):
        if request.url.path.endswith("starting_grid"):
            rows = [{"driver_number": 99, "position": 0}]
        else:
            rows = [{"driver_number": 99, "full_name": "Test Driver", "name_acronym": "TST"}]
        return httpx.Response(200, json=rows)

    from f1_pitwall.domain.models import Session

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = OpenF1(ProviderHTTP("openf1", client, Settings()))
        session = Session(
            type="Race", name="Race", date="2030-01-01", provider_id=1, source="openf1"
        )
        rows = await p.grid(
            session, [Driver(id="stable_id", first_name="Test", last_name="Driver")]
        )
        assert rows[0].driver.id == "stable_id" and rows[0].pit_lane is None
        with pytest.raises(ProviderError):
            await p.grid(
                session, [Driver(id="different", number=99, first_name="Other", last_name="Person")]
            )


def test_openf1_disagreement_and_ambiguous_match(caplog):
    from test_calendar import weekend

    e = weekend()
    replacement = e.sessions[-1].model_copy(
        update={"start": e.sessions[-1].start.replace(minute=30)}
    )
    p = OpenF1(ProviderHTTP("openf1", None, Settings()))
    w = Weekend(id=1, circuit_name="Test Circuit", locality="Test", sessions=[replacement])
    assert p.enrich(e, [w]).warnings
    assert p.enrich(e, [w]).sessions[-1].start == replacement.start
    assert p.enrich(e, [w, w]).sessions == e.sessions
    w.circuit_name = w.locality = "Elsewhere"
    assert p.enrich(e, [w]).warnings
    assert "differs" in caplog.text


async def test_cache_hit_is_not_blocked_by_slow_inflight_request():
    release = asyncio.Event()

    async def handler(request):
        if request.url.path == "/slow":
            await release.wait()
        return httpx.Response(200, content=request.url.path.encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = ProviderHTTP("test", client, Settings())
        assert await p.get("https://test.invalid/fast", 10) == b"/fast"
        slow = asyncio.create_task(p.get("https://test.invalid/slow", 10))
        await asyncio.sleep(0.5)  # The slow request is now in flight.
        cached = await asyncio.wait_for(p.get("https://test.invalid/fast", 10), timeout=0.1)
        assert cached == b"/fast" and not slow.done()
        release.set()
        assert await slow == b"/slow"
        assert not p._inflight


async def test_rss_skips_malformed_items_and_normalizes_dates():
    rss = b"""<rss><channel>
    <item><title>No link here</title></item>
    <item><title>Bad date</title><link>https://news.example/bad</link>
    <pubDate>not a date</pubDate></item>
    <item><title>Unknown zone</title><link>https://news.example/zone</link>
    <pubDate>Sun, 06 Sep 2026 08:00:00 -0000</pubDate></item>
    <item><title>Offset</title><link>https://news.example/offset</link>
    <pubDate>Sun, 06 Sep 2026 10:00:00 +0530</pubDate></item>
    </channel></rss>"""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=rss))
    ) as client:
        news = NewsService([RSSProvider(ProviderHTTP("test", client, Settings()), "https://t")])
        result = await news.get_latest_news(10)
    assert [a.headline for a in result.articles] == ["Unknown zone", "Offset", "Bad date"]
    assert result.articles[0].published_at.utcoffset().total_seconds() == 0
    assert result.articles[1].published_at.hour == 4
    assert result.articles[2].published_at is None
    assert result.provider_status[0].status == "ok"


async def test_openf1_login_sends_bearer_token_and_relogs_after_401():
    logins, seen = 0, []

    def handler(request):
        nonlocal logins
        if request.url.path == "/token":
            logins += 1
            assert b"username=fan" in request.content and b"password=secret" in request.content
            return httpx.Response(
                200,
                json={"access_token": f"t{logins}", "expires_in": "3600", "token_type": "bearer"},
            )
        seen.append(request.headers.get("Authorization"))
        if request.url.path.endswith("/laps") and len(seen) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json=[])

    settings = Settings(openf1_username="fan", openf1_password="secret", retries=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = ProviderHTTP("openf1", client, settings, auth=OpenF1Auth(client, settings))
        provider = OpenF1(http)
        with pytest.raises(ProviderError, match="HTTP 401"):
            await provider.get_laps(1)
        assert await provider.get_stints(1) == []
    assert seen == ["Bearer t1", "Bearer t2"] and logins == 2
    assert "secret" not in repr(settings)


async def test_openf1_unauthorized_without_login_explains_how_to_fix():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401))
    ) as client:
        http = ProviderHTTP(
            "openf1", client, Settings(retries=0), unauthorized_hint=UNAUTHORIZED_HINT
        )
        with pytest.raises(ProviderError, match="F1_OPENF1_USERNAME"):
            await OpenF1(http).get_laps(1)
        assert "F1_OPENF1_USERNAME" in next(iter(http.status.errors.values()))


async def test_openf1_failed_login_is_a_provider_error():
    settings = Settings(openf1_username="fan", openf1_password="wrong")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401))
    ) as client:
        with pytest.raises(ProviderError, match="login failed: HTTP 401"):
            await OpenF1Auth(client, settings).headers()
