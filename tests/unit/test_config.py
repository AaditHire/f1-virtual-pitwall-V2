import pytest

from f1_pitwall.core.config import Settings


def test_news_feeds_env_is_parsed_as_json(monkeypatch):
    monkeypatch.setenv("F1_NEWS_FEEDS", '{"Test": "https://example.com/rss"}')
    monkeypatch.setenv("F1_TIMEOUT", "5")
    settings = Settings.from_env()
    assert settings.news_feeds == {"Test": "https://example.com/rss"}
    assert settings.timeout == 5


def test_malformed_news_feeds_env_explains_the_expected_format(monkeypatch):
    monkeypatch.setenv("F1_NEWS_FEEDS", "{'Test': https://example.com/rss}")
    with pytest.raises(ValueError, match="F1_NEWS_FEEDS must be a JSON object"):
        Settings.from_env()
