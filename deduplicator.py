"""
Deduplication using a local JSON store at data/seen.json.
Robust to missing file — creates it on first save.
"""

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "up",
    "with",
}


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) >= 3 and w not in TITLE_STOPWORDS}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _parse_seen_signatures(seen: set[str]) -> list[set[str]]:
    signatures: list[set[str]] = []
    for entry in seen:
        if entry.startswith("sig:"):
            tokens = set(entry[4:].split())
            if tokens:
                signatures.append(tokens)
    return signatures


def _signature_entry(tokens: set[str]) -> str:
    return f"sig:{' '.join(sorted(tokens))}"


def load_seen(path: str) -> set[str]:
    """Load seen IDs from JSON store. Returns empty set if file absent or corrupt."""
    if not os.path.exists(path):
        logger.info("seen.json not found at %s; starting fresh.", path)
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        if isinstance(data, dict):
            return set(data.get("seen", []))
        logger.warning("Unexpected seen.json format; starting fresh.")
        return set()
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load seen.json: %s; starting fresh.", exc)
        return set()


def filter_new(items: list[dict[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
    """Return only items not seen before by ID or title similarity."""
    new_items: list[dict[str, Any]] = []
    seen_signatures = _parse_seen_signatures(seen)
    kept_signatures: list[set[str]] = []

    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            continue

        tokens = _title_tokens(item.get("title", ""))
        if tokens:
            # Cross-run de-dup using persisted title signatures.
            if any(_jaccard_similarity(tokens, s) >= 0.6 for s in seen_signatures):
                continue
            # In-run cross-source de-dup (same story from multiple outlets).
            if any(_jaccard_similarity(tokens, s) >= 0.6 for s in kept_signatures):
                continue

        new_items.append(item)
        if tokens:
            kept_signatures.append(tokens)

    logger.info(
        "Deduplication: %d total, %d new, %d skipped",
        len(items),
        len(new_items),
        len(items) - len(new_items),
    )
    return new_items


def save_seen(items: list[dict[str, Any]], seen: set[str], path: str) -> None:
    """Append new item IDs and title signatures to seen store."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_ids = {item["id"] for item in items if item.get("id")}
    new_signatures = {
        _signature_entry(tokens)
        for tokens in (_title_tokens(item.get("title", "")) for item in items)
        if tokens
    }
    updated = seen | new_ids | new_signatures
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(updated), f, indent=2)
        logger.info("seen.json updated: %d total IDs stored.", len(updated))
    except OSError as exc:
        logger.error("Failed to write seen.json: %s", exc)
