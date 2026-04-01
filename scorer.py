"""
Scores items for relevance using Claude via the Message Batches API (50% cost reduction).
Returns JSON: {score, category, reason, tweet_angle}
Items below score threshold are discarded.
"""

import json
import logging
import os
import time
from typing import Any

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "quantum_hardware",
    "pq_cryptography",
    "pq_migration_blockchain",
    "tokenization_stablecoin",
    "regulatory",
    "competitor_activity",
}

YOUR_CONTEXT = os.getenv(
    "YOUR_CONTEXT",
    """\
    a post-quantum cryptography startup \
building quantum-resistant stablecoin and tokenized asset infrastructure. \
Your job: score news items for strategic relevance to startup.
""",
)
YOUR_CONTEXT = YOUR_CONTEXT.replace("\\n", "\n")


SYSTEM_PROMPT = f"""\
You are an intelligence analyst for 
{YOUR_CONTEXT.strip()}

Relevance categories:
- quantum_hardware: advances in quantum computers that threaten current cryptography
- pq_cryptography: new PQ algorithms, IACR papers, NIST standards activity
- pq_migration_blockchain: Ethereum/Solana/Bitcoin PQ upgrade discussions or implementations
- tokenization_stablecoin: tokenized treasuries, RWA, stablecoin infrastructure developments
- regulatory: crypto/stablecoin/digital asset regulation that affects the market
- competitor_activity: companies building PQ crypto for blockchain or PQ stablecoin infra

Scoring rubric (0–10):
10 — Direct threat or opportunity: quantum milestone, NIST announcement, major blockchain PQ move
8–9 — High signal: credible PQ migration proposal, large tokenization deal, competitive launch
6–7 — Relevant: useful market intelligence, adjacent research, noteworthy regulatory movement
4–5 — Weak signal: tangentially related, general crypto/quantum news with minor relevance
0–3 — Noise: unrelated, clickbait, or duplicate theme

If the title or snippet clearly concerns post-quantum cryptography, quantum-resistant schemes, or PQ/blockchain migration (including IACR ePrints and arXiv cs.CR), score at least 6 unless it is clearly unrelated spam.

Return ONLY valid JSON, no markdown, no prose:
{{"score": <int 0-10>, "category": "<one of the 6 categories>", "reason": "<one sentence>", "tweet_angle": "<one sentence hook for Twitter if score >= 6, else null>"}}
"""


def _build_user_message(item: dict[str, Any]) -> str:
    title = item.get("title", "").strip()
    snippet = item.get("snippet", "").strip()
    source = item.get("source", "").strip()
    return f"Source: {source}\nTitle: {title}\nSnippet: {snippet}"


def _parse_score_response(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Score response is not valid JSON: %r", text[:200])
        return None

    score = data.get("score")
    if not isinstance(score, (int, float)):
        logger.warning("Score missing or non-numeric in response: %r", data)
        return None

    category = data.get("category", "pq_cryptography")
    if category not in VALID_CATEGORIES:
        category = "pq_cryptography"

    return {
        "score": int(score),
        "category": category,
        "reason": str(data.get("reason") or ""),
        "tweet_angle": data.get("tweet_angle") or None,
    }


def score_items(
    items: list[dict[str, Any]],
    api_key: str,
    model: str = "claude-haiku-4-5",
    min_score: int = 4,
    poll_interval: int = 30,
) -> list[dict[str, Any]]:
    """
    Score all items via the Anthropic Message Batches API (50% cheaper, async).
    Submits a single batch, polls until complete, then parses results.
    Returns enriched items above min_score, sorted by score descending.
    """
    if not items:
        return []

    client = anthropic.Anthropic(api_key=api_key)

    item_map: dict[str, dict[str, Any]] = {}
    requests: list[Request] = []
    for i, item in enumerate(items):
        custom_id = f"item-{i}"
        item_map[custom_id] = item
        requests.append(
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(
                    model=model,
                    max_tokens=256,
                    system=SYSTEM_PROMPT,
                    messages=[
                        {"role": "user", "content": _build_user_message(item)},
                    ],
                ),
            )
        )

    logger.info("Submitting batch of %d items to Anthropic...", len(requests))
    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id
    logger.info("Batch created: %s", batch_id)

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        logger.info(
            "Batch %s — processing=%d succeeded=%d errored=%d expired=%d canceled=%d",
            batch_id,
            counts.processing,
            counts.succeeded,
            counts.errored,
            counts.expired,
            counts.canceled,
        )
        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval)

    scored: list[dict[str, Any]] = []
    kept_count = 0
    parse_fail_count = 0
    error_count = 0
    expired_count = 0

    for result in client.messages.batches.results(batch_id):
        item = item_map.get(result.custom_id)
        if item is None:
            logger.warning("Unknown custom_id in batch result: %s", result.custom_id)
            continue

        if result.result.type == "succeeded":
            message = result.result.message
            raw = message.content[0].text if message.content else ""
            parsed = _parse_score_response(raw)

            if parsed is None:
                parse_fail_count += 1
                logger.warning(
                    "Skipping item %s: unparseable score response.",
                    item.get("id"),
                )
                continue

            if parsed["score"] < min_score:
                logger.debug(
                    "Discarded (score %d < %d): %s",
                    parsed["score"],
                    min_score,
                    item.get("title", "")[:60],
                )
                continue

            scored.append({**item, **parsed})
            kept_count += 1

        elif result.result.type == "errored":
            error_count += 1
            logger.error(
                "Batch error on item %s: %s",
                item.get("id"),
                result.result.error,
            )
        elif result.result.type == "expired":
            expired_count += 1
            logger.warning("Request expired for item %s", item.get("id"))
        elif result.result.type == "canceled":
            logger.warning("Request canceled for item %s", item.get("id"))

    scored.sort(key=lambda x: x["score"], reverse=True)
    logger.info(
        "Scoring complete: %d/%d items above threshold (min=%d)",
        len(scored),
        len(items),
        min_score,
    )
    logger.info(
        "Batch counters: kept=%d parse_failed=%d errored=%d expired=%d",
        kept_count,
        parse_fail_count,
        error_count,
        expired_count,
    )
    return scored
