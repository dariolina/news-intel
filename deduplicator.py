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

# Broader set for title+snippet similarity — filters common headline filler
# so that domain-specific terms drive the comparison.
CONTENT_STOPWORDS = TITLE_STOPWORDS | {
    "about", "after", "all", "also", "are", "back", "be", "been",
    "before", "between", "both", "but", "by", "can", "come", "could",
    "did", "do", "does", "each", "even", "every", "find", "finds",
    "first", "get", "gets", "going", "got", "had", "has", "have",
    "her", "here", "his", "how", "if", "is", "it", "its", "just",
    "know", "last", "like", "look", "make", "makes", "many", "may",
    "might", "more", "most", "much", "must", "new", "nor", "not",
    "now", "only", "other", "our", "out", "over", "own", "per",
    "put", "say", "says", "said", "see", "set", "she", "should",
    "show", "shows", "some", "still", "such", "take", "than", "that",
    "their", "them", "then", "there", "these", "they", "this", "too",
    "two", "use", "used", "using", "very", "want", "was", "way",
    "well", "were", "what", "when", "which", "while", "who", "why",
    "will", "work", "would", "year", "years", "you", "your",
}

MIN_CONTENT_TOKENS = 5


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) >= 3 and w not in TITLE_STOPWORDS}


def _content_tokens(title: str, snippet: str) -> set[str]:
    """Tokenize title + snippet for richer same-story detection."""
    text = f"{title or ''} {snippet or ''}"
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in CONTENT_STOPWORDS}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap_coefficient(a: set[str], b: set[str]) -> float:
    """Fraction of the smaller set contained in the larger — handles asymmetric coverage."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _content_is_similar(
    a: set[str],
    b: set[str],
    jaccard_threshold: float,
    overlap_threshold: float,
) -> bool:
    """True if either Jaccard or Overlap Coefficient exceeds its threshold."""
    return (
        _jaccard_similarity(a, b) >= jaccard_threshold
        or _overlap_coefficient(a, b) >= overlap_threshold
    )


def _parse_seen_signatures(
    seen: set[str], prefix: str = "sig:"
) -> list[set[str]]:
    signatures: list[set[str]] = []
    for entry in seen:
        if entry.startswith(prefix):
            tokens = set(entry[len(prefix) :].split())
            if tokens:
                signatures.append(tokens)
    return signatures


def _signature_entry(tokens: set[str], prefix: str = "sig") -> str:
    return f"{prefix}:{' '.join(sorted(tokens))}"


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


def filter_new(
    items: list[dict[str, Any]],
    seen: set[str],
    title_jaccard: float = 0.6,
    content_jaccard: float = 0.35,
    content_overlap: float = 0.6,
) -> list[dict[str, Any]]:
    """Return only items not seen before by ID, title, or content similarity."""
    new_items: list[dict[str, Any]] = []
    seen_title_sigs = _parse_seen_signatures(seen, "sig:")
    seen_content_sigs = _parse_seen_signatures(seen, "csig:")
    kept_title_sigs: list[set[str]] = []
    kept_content_sigs: list[set[str]] = []

    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            continue

        title = item.get("title", "")
        snippet = item.get("snippet", "")
        title_tok = _title_tokens(title)
        content_tok = _content_tokens(title, snippet)

        if title_tok:
            if any(
                _jaccard_similarity(title_tok, s) >= title_jaccard
                for s in seen_title_sigs
            ):
                continue
            if any(
                _jaccard_similarity(title_tok, s) >= title_jaccard
                for s in kept_title_sigs
            ):
                continue

        if len(content_tok) >= MIN_CONTENT_TOKENS:
            if any(
                _content_is_similar(
                    content_tok, s, content_jaccard, content_overlap
                )
                for s in seen_content_sigs
            ):
                continue
            if any(
                _content_is_similar(
                    content_tok, s, content_jaccard, content_overlap
                )
                for s in kept_content_sigs
            ):
                continue

        new_items.append(item)
        if title_tok:
            kept_title_sigs.append(title_tok)
        if content_tok:
            kept_content_sigs.append(content_tok)

    logger.info(
        "Deduplication: %d total, %d new, %d skipped",
        len(items),
        len(new_items),
        len(items) - len(new_items),
    )
    return new_items


def save_seen(items: list[dict[str, Any]], seen: set[str], path: str) -> None:
    """Append new item IDs, title signatures, and content signatures to seen store."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_ids = {item["id"] for item in items if item.get("id")}
    new_title_sigs = {
        _signature_entry(tokens, "sig")
        for tokens in (_title_tokens(item.get("title", "")) for item in items)
        if tokens
    }
    new_content_sigs = {
        _signature_entry(tokens, "csig")
        for tokens in (
            _content_tokens(item.get("title", ""), item.get("snippet", ""))
            for item in items
        )
        if tokens
    }
    updated = seen | new_ids | new_title_sigs | new_content_sigs
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(updated), f, indent=2)
        logger.info("seen.json updated: %d total entries stored.", len(updated))
    except OSError as exc:
        logger.error("Failed to write seen.json: %s", exc)
