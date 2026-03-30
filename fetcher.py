"""
Fetchers for arXiv, IACR ePrint, NewsAPI, and RSS feeds.
All return items with schema: {id, title, url, source, published, snippet}
Errors are caught and logged; a failed source never crashes the full run.
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests

logger = logging.getLogger(__name__)

NEWSAPI_BASE = "https://newsapi.org/v2/everything"
ARXIV_API_BASE = "https://export.arxiv.org/api/query"
IACR_RSS = "https://eprint.iacr.org/rss/rss.xml"


def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _truncate(text: str, max_chars: int = 400) -> str:
    if not text:
        return ""
    text = text.strip()
    return text[:max_chars] + "…" if len(text) > max_chars else text


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_recent(published: str, max_age_hours: int) -> bool:
    """Return True if published timestamp is within max_age_hours."""
    dt = _parse_iso_datetime(published)
    if dt is None:
        # Keep undated items rather than dropping potentially relevant signals.
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    return dt >= cutoff


def fetch_newsapi(
    api_key: str,
    keywords: dict[str, list[str]],
    from_lookback_hours: int = 48,
    max_article_age_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Hit NewsAPI /v2/everything for all keywords, return deduplicated items.

    Free-tier NewsAPI often delays articles ~24h; use a wider ``from_lookback_hours`` for the API
    ``from`` parameter than ``max_article_age_hours`` (``publishedAt`` filter) so results are not empty.
    """
    if not api_key or api_key == "your_key_here":
        logger.warning("NewsAPI key not set; skipping.")
        return []

    flat_keywords = []
    for terms in keywords.values():
        flat_keywords.extend(terms)

    published_max_hours = (
        max_article_age_hours
        if max_article_age_hours is not None
        else from_lookback_hours
    )

    # Chunk keywords into groups of 5 to avoid overly long query strings
    chunk_size = 5
    seen_urls: set[str] = set()
    items: list[dict[str, Any]] = []
    from_dt = datetime.now(timezone.utc) - timedelta(hours=from_lookback_hours)
    from_param = from_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for i in range(0, len(flat_keywords), chunk_size):
        chunk = flat_keywords[i : i + chunk_size]
        query = " OR ".join(f'"{kw}"' for kw in chunk)
        params = {
            "q": query,
            "apiKey": api_key,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 100,
            "from": from_param,
        }
        try:
            resp = requests.get(NEWSAPI_BASE, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for article in data.get("articles", []):
                url = article.get("url", "")
                if not url or url in seen_urls:
                    continue
                published = article.get("publishedAt") or ""
                if not _is_recent(published, published_max_hours):
                    continue
                seen_urls.add(url)
                items.append(
                    {
                        "id": _make_id(url),
                        "title": article.get("title") or "",
                        "url": url,
                        "source": article.get("source", {}).get("name") or "NewsAPI",
                        "published": published,
                        "snippet": _truncate(
                            article.get("description") or article.get("content") or ""
                        ),
                    }
                )
        except Exception as exc:
            logger.error("NewsAPI fetch failed for chunk %d: %s", i, exc)

        time.sleep(0.2)  # stay well within rate limits

    logger.info("NewsAPI: fetched %d items", len(items))
    return items


def fetch_rss(feed_urls: list[str], max_age_hours: int = 12) -> list[dict[str, Any]]:
    """Fetch all RSS feeds and return items in the standard schema."""
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for url in feed_urls:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                logger.warning("RSS parse error for %s: %s", url, feed.bozo_exception)
                continue
            source = feed.feed.get("title") or url
            for entry in feed.entries:
                link = entry.get("link") or entry.get("id") or ""
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                published = ""
                if entry.get("published_parsed"):
                    try:
                        published = datetime(
                            *entry.published_parsed[:6], tzinfo=timezone.utc
                        ).isoformat()
                    except Exception:
                        published = entry.get("published", "")
                else:
                    published = entry.get("published", "")
                if not _is_recent(published, max_age_hours):
                    continue

                snippet = _truncate(
                    entry.get("summary")
                    or entry.get("description")
                    or entry.get("content", [{}])[0].get("value", "")
                )

                items.append(
                    {
                        "id": _make_id(link),
                        "title": entry.get("title") or "",
                        "url": link,
                        "source": source,
                        "published": published,
                        "snippet": snippet,
                    }
                )
        except Exception as exc:
            logger.error("RSS fetch failed for %s: %s", url, exc)

    logger.info("RSS: fetched %d items across %d feeds", len(items), len(feed_urls))
    return items


def fetch_arxiv(
    categories: list[str],
    keywords: dict[str, list[str]],
    max_age_hours: int = 12,
) -> list[dict[str, Any]]:
    """Query arXiv API for recent papers matching keyword groups."""
    flat_keywords = []
    for terms in keywords.values():
        flat_keywords.extend(terms)

    # Build search query: category filter + keyword OR terms
    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    kw_query = " OR ".join(f'ti:"{kw}" OR abs:"{kw}"' for kw in flat_keywords[:20])
    search_query = f"({cat_query}) AND ({kw_query})"

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": 50,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    items: list[dict[str, Any]] = []
    try:
        resp = requests.get(ARXIV_API_BASE, params=params, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            link = entry.get("id") or entry.get("link") or ""
            if not link:
                continue
            published = ""
            if entry.get("published_parsed"):
                try:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).isoformat()
                except Exception:
                    published = entry.get("published", "")
            if not _is_recent(published, max_age_hours):
                continue

            authors = ", ".join(
                a.get("name", "") for a in entry.get("authors", [])[:3]
            )
            snippet = _truncate(entry.get("summary") or "")
            if authors:
                snippet = f"Authors: {authors}. {snippet}"

            items.append(
                {
                    "id": _make_id(link),
                    "title": entry.get("title") or "",
                    "url": link,
                    "source": "arXiv",
                    "published": published,
                    "snippet": snippet,
                }
            )
    except Exception as exc:
        logger.error("arXiv fetch failed: %s", exc)

    logger.info("arXiv: fetched %d items", len(items))
    return items


def fetch_iacr(max_age_hours: int = 12) -> list[dict[str, Any]]:
    """Fetch IACR ePrint RSS feed."""
    items: list[dict[str, Any]] = []
    try:
        feed = feedparser.parse(IACR_RSS)
        if feed.bozo and not feed.entries:
            logger.warning("IACR RSS parse error: %s", feed.bozo_exception)
            return []

        for entry in feed.entries:
            link = entry.get("link") or entry.get("id") or ""
            if not link:
                continue

            published = ""
            if entry.get("published_parsed"):
                try:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).isoformat()
                except Exception:
                    published = entry.get("published", "")
            if not _is_recent(published, max_age_hours):
                continue

            authors = entry.get("author") or ""
            snippet = _truncate(entry.get("summary") or entry.get("description") or "")
            if authors:
                snippet = f"Authors: {authors}. {snippet}"

            items.append(
                {
                    "id": _make_id(link),
                    "title": entry.get("title") or "",
                    "url": link,
                    "source": "IACR ePrint",
                    "published": published,
                    "snippet": snippet,
                }
            )
    except Exception as exc:
        logger.error("IACR fetch failed: %s", exc)

    logger.info("IACR: fetched %d items", len(items))
    return items
