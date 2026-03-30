"""
Formats scored items into markdown digests, tweet suggestions, and alerts.
All output is readable plain markdown without requiring a renderer.
"""

from datetime import date
from typing import Any

CATEGORY_LABELS = {
    "quantum_hardware": "Quantum Hardware",
    "pq_cryptography": "PQ Cryptography",
    "pq_migration_blockchain": "PQ Migration — Blockchain",
    "tokenization_stablecoin": "Tokenization & Stablecoin",
    "regulatory": "Regulatory",
    "competitor_activity": "Competitor Activity",
}

CATEGORY_ORDER = [
    "pq_migration_blockchain",
    "competitor_activity",
    "pq_cryptography",
    "quantum_hardware",
    "tokenization_stablecoin",
    "regulatory",
]


def _score_bar(score: int) -> str:
    filled = round(score / 2)
    return "█" * filled + "░" * (5 - filled)


def format_daily_digest(scored_items: list[dict[str, Any]], digest_date: date) -> str:
    lines: list[str] = []
    date_str = digest_date.strftime("%B %d, %Y")
    lines.append(f"# News Intel Digest — {date_str}")
    lines.append("")

    if not scored_items:
        lines.append("No new items today.")
        return "\n".join(lines)

    lines.append(
        f"{len(scored_items)} items scored above threshold across "
        f"{len({i['category'] for i in scored_items})} categories."
    )
    lines.append("")

    # Group by category
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in scored_items:
        cat = item.get("category", "pq_cryptography")
        by_category.setdefault(cat, []).append(item)

    # Emit in priority order, unknown categories at end
    ordered_cats = [c for c in CATEGORY_ORDER if c in by_category]
    remaining = [c for c in by_category if c not in CATEGORY_ORDER]
    for cat in ordered_cats + remaining:
        label = CATEGORY_LABELS.get(cat, cat)
        items = sorted(by_category[cat], key=lambda x: x["score"], reverse=True)
        lines.append(f"## {label}")
        lines.append("")
        for item in items:
            score = item.get("score", 0)
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            source = item.get("source", "")
            published = item.get("published", "")[:10]  # date portion only
            reason = item.get("reason", "")

            lines.append(f"**{title}**")
            lines.append(
                f"Score: {score}/10 {_score_bar(score)}  |  "
                f"Source: {source}  |  {published}"
            )
            if reason:
                lines.append(f"_{reason}_")
            if url:
                lines.append(f"<{url}>")
            lines.append("")

    return "\n".join(lines)


def format_tweet_suggestions(scored_items: list[dict[str, Any]]) -> str:
    eligible = [
        item for item in scored_items
        if item.get("score", 0) >= 7 and item.get("tweet_angle")
    ]

    lines: list[str] = []
    lines.append("# News Intel Tweet Suggestions")
    lines.append("")

    if not eligible:
        lines.append("No items met the tweet threshold (score >= 7 with tweet angle) today.")
        return "\n".join(lines)

    lines.append(
        f"{len(eligible)} items ready for Twitter. "
        "Each block is a ready-to-use prompt — edit before posting."
    )
    lines.append("")

    for item in sorted(eligible, key=lambda x: x["score"], reverse=True):
        score = item.get("score", 0)
        title = item.get("title", "")
        url = item.get("url", "")
        tweet_angle = item.get("tweet_angle", "")
        source = item.get("source", "")
        cat = CATEGORY_LABELS.get(item.get("category", ""), item.get("category", ""))

        lines.append(f"### Score {score}/10 — {cat}")
        lines.append(f"**Source:** {title} ({source})")
        if url:
            lines.append(f"**Link:** <{url}>")
        lines.append(f"**Angle:** {tweet_angle}")
        lines.append("")
        lines.append("```")
        lines.append(f"{tweet_angle}")
        lines.append("")
        lines.append(f"{url}")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def format_alerts(scored_items: list[dict[str, Any]]) -> str:
    alerts = [item for item in scored_items if item.get("score", 0) >= 8]

    lines: list[str] = []
    lines.append("# URGENT: News Intel High-Priority Alerts")
    lines.append("")

    if not alerts:
        lines.append("No high-priority alerts.")
        return "\n".join(lines)

    lines.append(f"{len(alerts)} item(s) scored 8 or above — review immediately.")
    lines.append("")

    for item in sorted(alerts, key=lambda x: x["score"], reverse=True):
        score = item.get("score", 0)
        title = item.get("title", "(no title)")
        url = item.get("url", "")
        source = item.get("source", "")
        published = item.get("published", "")[:10]
        reason = item.get("reason", "")
        cat = CATEGORY_LABELS.get(item.get("category", ""), item.get("category", ""))
        tweet_angle = item.get("tweet_angle")

        lines.append(f"## [{score}/10] {title}")
        lines.append(f"**Category:** {cat}  |  **Source:** {source}  |  {published}")
        if reason:
            lines.append(f"**Why it matters:** {reason}")
        if tweet_angle:
            lines.append(f"**Tweet angle:** {tweet_angle}")
        if url:
            lines.append(f"**Link:** <{url}>")
        lines.append("")

    return "\n".join(lines)
