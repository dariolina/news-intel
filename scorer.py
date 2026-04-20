"""
Scores items for relevance using the OpenAI Responses API.
Returns JSON: {score, category, reason, tweet_angle}
Items below score threshold are discarded.
"""

import json
import logging
import os
from typing import Any

import openai
from openai import OpenAI

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
{{"score": <int 0-10>, "category": "<one of the 6 categories>", "reason": "<one sentence>", "tweet_angle": "<one sentence hook for Twitter if score >= 7, else null>"}}

Keep "reason" and "tweet_angle" short (plain text, no line breaks) so the response stays compact.
"""


SCORE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "category": {
            "type": "string",
            "enum": sorted(VALID_CATEGORIES),
        },
        "reason": {"type": "string", "maxLength": 500},
        "tweet_angle": {
            "anyOf": [
                {"type": "string", "maxLength": 280},
                {"type": "null"},
            ],
        },
    },
    "required": ["score", "category", "reason", "tweet_angle"],
    "additionalProperties": False,
}


def _create_message_with_retry(
    client: OpenAI,
    *,
    model: str,
    user_message: str,
    max_output_tokens: int,
    reasoning_effort: str | None,
    structured_output: bool,
    max_retries: int = 5,
    initial_backoff_seconds: int = 2,
) -> Any:
    """
    Create a response with retry/backoff on transient transport/server failures.
    """
    import time

    attempt = 0
    while True:
        try:
            return _responses_create_for_score(
                client,
                model=model,
                user_message=user_message,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
                structured_output=structured_output,
            )
        except (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ) as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            sleep_for = initial_backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Transient OpenAI error while scoring item (%s). "
                "Retrying in %ds (%d/%d).",
                exc.__class__.__name__,
                sleep_for,
                attempt,
                max_retries,
            )
            time.sleep(sleep_for)


def _responses_create_for_score(
    client: OpenAI,
    *,
    model: str,
    user_message: str,
    max_output_tokens: int,
    reasoning_effort: str | None,
    structured_output: bool,
) -> Any:
    """
    Responses API call with structured JSON when supported.
    Falls back if the API rejects optional parameters for this model.
    """
    text: dict[str, Any] = {"verbosity": "low"}
    if structured_output:
        text["format"] = {
            "type": "json_schema",
            "name": "news_intel_score",
            "strict": True,
            "schema": SCORE_JSON_SCHEMA,
        }

    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": user_message,
        "max_output_tokens": max_output_tokens,
        "text": text,
    }
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    try:
        return client.responses.create(**kwargs)
    except openai.BadRequestError:
        if structured_output and reasoning_effort:
            logger.info(
                "Retrying score request without reasoning_effort=%r (model may not support it)",
                reasoning_effort,
            )
            return _responses_create_for_score(
                client,
                model=model,
                user_message=user_message,
                max_output_tokens=max_output_tokens,
                reasoning_effort=None,
                structured_output=True,
            )
        if structured_output:
            logger.info(
                "Retrying score request without structured JSON (model may not support json_schema)"
            )
            return _responses_create_for_score(
                client,
                model=model,
                user_message=user_message,
                max_output_tokens=max_output_tokens,
                reasoning_effort=None,
                structured_output=False,
            )
        raise


def _should_retry_score_response(response: Any, raw: str, parsed: dict[str, Any] | None) -> bool:
    if parsed is not None:
        return False
    if raw.strip():
        return True
    incomplete = getattr(response, "incomplete_details", None)
    reason = getattr(incomplete, "reason", None) if incomplete is not None else None
    if reason == "max_output_tokens":
        return True
    return False


def _extract_response_text(response: Any) -> str:
    """
    Extract model text robustly from Responses API payloads.
    """
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = getattr(response, "output", None) or []
    parts: list[str] = []

    for item in output:
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue

        content = getattr(item, "content", None) or []
        for block in content:
            block_type = getattr(block, "type", None)
            if block_type == "output_text":
                text = getattr(block, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())

    return "\n".join(parts).strip()


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
    model: str = "gpt-4.1-mini",
    min_score: int = 4,
    *,
    max_output_tokens: int = 4096,
    reasoning_effort: str | None = "low",
    structured_output: bool = True,
) -> list[dict[str, Any]]:
    """
    Score all items via the OpenAI Responses API.
    Sends one request per item and parses results.
    Returns enriched items above min_score, sorted by score descending.
    """
    if not items:
        return []

    client = OpenAI(api_key=api_key)

    scored: list[dict[str, Any]] = []
    kept_count = 0
    parse_fail_count = 0
    error_count = 0
    total_items = len(items)

    for idx, item in enumerate(items, start=1):
        logger.info("Scoring item %d/%d", idx, total_items)
        doubled = min(16384, max(1, max_output_tokens) * 2)
        token_budgets = (
            [max_output_tokens, doubled]
            if doubled > max_output_tokens
            else [max_output_tokens]
        )
        message: Any = None
        raw = ""
        parsed: dict[str, Any] | None = None

        for attempt_idx, token_budget in enumerate(token_budgets):
            if attempt_idx > 0:
                logger.warning(
                    "Re-scoring item %s with max_output_tokens=%d (parse failed or empty output)",
                    item.get("id"),
                    token_budget,
                )
            try:
                message = _create_message_with_retry(
                    client,
                    model=model,
                    user_message=_build_user_message(item),
                    max_output_tokens=token_budget,
                    reasoning_effort=reasoning_effort,
                    structured_output=structured_output,
                )
            except openai.APIError as exc:
                error_count += 1
                logger.error(
                    "OpenAI error on item %s: %s",
                    item.get("id"),
                    exc,
                )
                message = None
                break

            if message is None:
                break

            raw = _extract_response_text(message)
            parsed = _parse_score_response(raw)
            if parsed is not None:
                break
            if not _should_retry_score_response(message, raw, parsed):
                break

        if message is None:
            continue

        if parsed is None:
            parse_fail_count += 1
            if not raw.strip():
                logger.warning(
                    "Empty model output for item %s: status=%s incomplete=%s usage=%s",
                    item.get("id"),
                    getattr(message, "status", None),
                    getattr(message, "incomplete_details", None),
                    getattr(message, "usage", None),
                )
            else:
                logger.warning(
                    "Unparseable JSON for item %s (first 240 chars): %r",
                    item.get("id"),
                    raw[:240],
                )
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

    scored.sort(key=lambda x: x["score"], reverse=True)
    logger.info(
        "Scoring complete: %d/%d items above threshold (min=%d)",
        len(scored),
        len(items),
        min_score,
    )
    logger.info(
        "Scoring counters: kept=%d parse_failed=%d errored=%d",
        kept_count,
        parse_fail_count,
        error_count,
    )
    return scored
