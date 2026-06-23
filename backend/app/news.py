from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import html
import json
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import AppSettings


GUARDIAN_SEARCH_URL = "https://content.guardianapis.com/search"
NHS_NEWS_QUERY = 'NHS OR "NHS England" OR "National Health Service"'


@dataclass
class GuardianNewsCache:
    articles: list[dict[str, Any]]
    fetched_at: str | None = None
    error: str | None = None


class GuardianNewsService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._cache = GuardianNewsCache(articles=[])
        self._last_fetch_monotonic = 0.0
        self._lock = threading.Lock()

    def get_payload(self, *, force_refresh: bool = False) -> dict[str, Any]:
        refresh_seconds = max(60, int(self.settings.guardian_news_refresh_seconds))
        cache_age = time.monotonic() - self._last_fetch_monotonic
        if not force_refresh and self._last_fetch_monotonic and cache_age < refresh_seconds:
            return self._response_payload(refresh_seconds)

        with self._lock:
            cache_age = time.monotonic() - self._last_fetch_monotonic
            if not force_refresh and self._last_fetch_monotonic and cache_age < refresh_seconds:
                return self._response_payload(refresh_seconds)
            try:
                self._cache = GuardianNewsCache(
                    articles=self._fetch_articles(),
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    error=None,
                )
                self._last_fetch_monotonic = time.monotonic()
            except Exception as exc:
                self._cache.error = str(exc)
                self._last_fetch_monotonic = time.monotonic()

        return self._response_payload(refresh_seconds)

    def _response_payload(self, refresh_seconds: int) -> dict[str, Any]:
        return {
            "articles": self._cache.articles,
            "last_updated": self._cache.fetched_at,
            "refresh_seconds": refresh_seconds,
            "error": self._cache.error,
        }

    def _fetch_articles(self) -> list[dict[str, Any]]:
        api_key = self.settings.guardian_api_key.strip()
        if not api_key:
            raise RuntimeError("GUARDIAN_API_KEY is not configured")

        params = {
            "api-key": api_key,
            "q": NHS_NEWS_QUERY,
            "from-date": (date.today() - timedelta(days=30)).isoformat(),
            "order-by": "newest",
            "page-size": max(1, min(20, int(self.settings.guardian_news_page_size))),
            "show-fields": "headline,trailText,thumbnail,shortUrl",
            "format": "json",
        }
        request = Request(
            f"{GUARDIAN_SEARCH_URL}?{urlencode(params)}",
            headers={"User-Agent": "HealthcareKnowledgeAgent/0.1"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Guardian API request failed with status {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Guardian API request failed: {exc.reason}") from exc

        results = payload.get("response", {}).get("results", [])
        articles: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            url = str(fields.get("shortUrl") or item.get("webUrl") or "").strip()
            title = str(fields.get("headline") or item.get("webTitle") or "").strip()
            if not title or not url:
                continue
            articles.append(
                {
                    "id": str(item.get("id") or url),
                    "title": title,
                    "section": str(item.get("sectionName") or ""),
                    "published_at": str(item.get("webPublicationDate") or ""),
                    "summary": _clean_text(str(fields.get("trailText") or "")),
                    "url": url,
                    "thumbnail": str(fields.get("thumbnail") or ""),
                }
            )
        return articles


def _clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()
