#!/usr/bin/env python3
"""
News Intel Pipeline — main entry point.
Loads config, fetches all sources, deduplicates, scores, formats, writes outputs.
"""

import logging
import os
import sys
import json
from datetime import date, timezone, datetime, timedelta

import yaml
from dotenv import load_dotenv

from deduplicator import filter_new, load_seen, save_seen
from fetcher import fetch_arxiv, fetch_iacr, fetch_newsapi, fetch_rss
from formatter import format_alerts, format_daily_digest, format_tweet_suggestions
from scorer import score_items

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs(*dirs: str) -> None:
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("Written: %s", path)


def load_json_list(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def write_json(path: str, data: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Written: %s", path)


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main() -> None:
    load_dotenv()

    newsapi_key = os.getenv("NEWSAPI_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if not anthropic_key or anthropic_key == "your_key_here":
        logger.error("ANTHROPIC_API_KEY not set. Set it in .env and re-run.")
        sys.exit(1)

    config = load_config()
    paths = config.get("data_paths", {})
    seen_path = paths.get("seen", "data/seen.json")
    digests_dir = paths.get("digests", "data/digests")
    tweets_dir = paths.get("tweets", "data/tweets")
    alerts_dir = paths.get("alerts", "data/alerts")
    latest_digest = paths.get("latest_digest", "data/latest-digest.md")
    latest_digest_24h = paths.get("latest_digest_24h", "data/latest-digest-24h.md")
    latest_tweets = paths.get("latest_tweets", "data/latest-tweets.md")
    latest_alerts = paths.get("latest_alerts", "data/latest-alerts.md")

    ensure_dirs("data", digests_dir, tweets_dir, alerts_dir)

    thresholds = config.get("score_thresholds", {})
    min_score = thresholds.get("minimum", 5)
    feed_max_age_hours = int(config.get("feed_max_age_hours", 12))

    anthropic_cfg = config.get("anthropic", {})
    model = anthropic_cfg.get("model", "claude-haiku-4-5")

    keywords = config.get("keywords", {})
    rss_feeds = config.get("rss_feeds", [])
    arxiv_categories = config.get("arxiv_categories", [])
    newsapi_cfg = config.get("newsapi", {}) or {}

    # ------------------------------------------------------------------
    # 1. Fetch
    # ------------------------------------------------------------------
    logger.info("=== Fetching sources ===")
    all_items = []

    newsapi_items = fetch_newsapi(
        newsapi_key,
        keywords,
        from_lookback_hours=newsapi_cfg.get("from_lookback_hours", 48),
        max_article_age_hours=newsapi_cfg.get("max_article_age_hours"),
    )
    all_items.extend(newsapi_items)

    # Avoid duplicate IACR fetch (RSS feeds list already includes IACR)
    non_iacr_feeds = [
        u for u in rss_feeds if "iacr.org" not in u and "arxiv.org" not in u
    ]
    rss_items = fetch_rss(non_iacr_feeds, max_age_hours=feed_max_age_hours)
    all_items.extend(rss_items)

    arxiv_items = fetch_arxiv(arxiv_categories, keywords)
    all_items.extend(arxiv_items)

    iacr_items = fetch_iacr(max_age_hours=feed_max_age_hours)
    all_items.extend(iacr_items)

    logger.info("Total fetched: %d items", len(all_items))

    # ------------------------------------------------------------------
    # 2. Deduplicate
    # ------------------------------------------------------------------
    logger.info("=== Deduplicating ===")
    seen = load_seen(seen_path)
    dedup_cfg = config.get("dedup", {})
    new_items = filter_new(
        all_items,
        seen,
        title_jaccard=dedup_cfg.get("title_jaccard", 0.6),
        content_jaccard=dedup_cfg.get("content_jaccard", 0.35),
        content_overlap=dedup_cfg.get("content_overlap", 0.6),
    )
    logger.info("New items after dedup: %d", len(new_items))

    # ------------------------------------------------------------------
    # 3. Score
    # ------------------------------------------------------------------
    batch_poll_interval = int(anthropic_cfg.get("batch_poll_interval", 30))

    logger.info("=== Scoring with Claude (%s) via Batch API ===", model)
    if new_items:
        scored = score_items(
            new_items,
            anthropic_key,
            model=model,
            min_score=min_score,
            poll_interval=batch_poll_interval,
        )
    else:
        scored = []

    alerts = [i for i in scored if i.get("score", 0) >= thresholds.get("alert", 8)]
    tweet_eligible = [
        i for i in scored
        if i.get("score", 0) >= thresholds.get("tweet_suggestion", 7) and i.get("tweet_angle")
    ]

    # ------------------------------------------------------------------
    # 4. Save seen IDs (only after scoring, so failed items can retry)
    # ------------------------------------------------------------------
    save_seen(new_items, seen, seen_path)

    # ------------------------------------------------------------------
    # 5 & 6. Format and write outputs
    # ------------------------------------------------------------------
    today = date.today()
    date_str = today.strftime("%Y-%m-%d")
    now_utc = datetime.now(timezone.utc)
    run_ts_str = now_utc.strftime("%Y-%m-%d-%H%MZ")
    cutoff_24h = now_utc - timedelta(hours=24)

    logger.info("=== Writing outputs ===")

    # Keep a rolling per-day scored-item ledger so daily digest is cumulative.
    daily_items_path = os.path.join(digests_dir, f"{date_str}-items.json")
    daily_items = load_json_list(daily_items_path)
    if scored:
        scored_with_ts = []
        for item in scored:
            stamped = dict(item)
            stamped["scored_at"] = now_utc.isoformat()
            scored_with_ts.append(stamped)
        daily_items.extend(scored_with_ts)
        write_json(daily_items_path, daily_items)

    latest_digest_md = format_daily_digest(scored, today)
    digest_md = format_daily_digest(daily_items, today)
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_items_path = os.path.join(digests_dir, f"{yesterday_str}-items.json")
    rolling_candidates = load_json_list(yesterday_items_path) + daily_items
    rolling_items: list[dict] = []
    for item in rolling_candidates:
        scored_at = parse_iso_datetime(item.get("scored_at", ""))
        if not scored_at:
            continue
        if scored_at.tzinfo is None:
            scored_at = scored_at.replace(tzinfo=timezone.utc)
        if scored_at >= cutoff_24h:
            rolling_items.append(item)
    rolling_digest_md = format_daily_digest(rolling_items, today)

    digest_path = os.path.join(digests_dir, f"{run_ts_str}-digest.md")
    write_file(digest_path, digest_md)
    write_file(latest_digest, latest_digest_md)
    write_file(latest_digest_24h, rolling_digest_md)

    tweets_md = format_tweet_suggestions(scored)
    tweets_path = os.path.join(tweets_dir, f"{date_str}-tweets.md")
    write_file(tweets_path, tweets_md)
    write_file(latest_tweets, tweets_md)

    alerts_md = format_alerts(scored)
    if alerts:
        alerts_path = os.path.join(alerts_dir, f"{date_str}-alerts.md")
        write_file(alerts_path, alerts_md)
    write_file(latest_alerts, alerts_md)

    # ------------------------------------------------------------------
    # 7. Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("News Intel Pipeline — Run Complete")
    print("=" * 60)
    print(f"  Items fetched (total):      {len(all_items)}")
    print(f"  New items (post-dedup):     {len(new_items)}")
    print(f"  Scored above threshold:     {len(scored)}")
    print(f"  Tweet suggestions (>=7):    {len(tweet_eligible)}")
    print(f"  High-priority alerts (>=8): {len(alerts)}")
    print()
    print(f"  Digest:  {latest_digest}")
    print(f"  Tweets:  {latest_tweets}")
    if alerts:
        print(f"  ALERTS:  {latest_alerts}  <-- review now")
    print("=" * 60)


if __name__ == "__main__":
    main()
