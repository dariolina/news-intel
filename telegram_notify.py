"""
Send plain-text messages to Telegram via Bot API (used for high-priority alerts and daily digest).
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Stay under Telegram's 4096-byte limit; use a safe character budget.
DEFAULT_CHUNK_SIZE = 4000

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def chunk_text(text: str, max_len: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    if max_len < 1:
        raise ValueError("max_len must be >= 1")
    if not text:
        return []
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def send_plain_text(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    timeout_s: float = 30.0,
    session: requests.Session | None = None,
) -> None:
    """
    POST text to Telegram. Splits into multiple messages if needed.
    Raises requests.HTTPError or ValueError on failure.
    """
    if not bot_token or not chat_id:
        raise ValueError("bot_token and chat_id are required")
    if not text.strip():
        return

    sess = session or requests.Session()
    url = TELEGRAM_SEND_URL.format(token=bot_token)
    chunks = chunk_text(text, chunk_size)
    for i, chunk in enumerate(chunks):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
        }
        resp = sess.post(url, json=payload, timeout=timeout_s)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            logger.error(
                "Telegram API HTTP error: %s — %s",
                resp.status_code,
                resp.text[:500],
            )
            raise
        body = resp.json()
        if not body.get("ok"):
            desc = body.get("description", body)
            logger.error("Telegram API error: %s", desc)
            raise RuntimeError(f"Telegram sendMessage failed: {desc}")
        if len(chunks) > 1:
            logger.info("Telegram alert part %d/%d sent", i + 1, len(chunks))
    logger.info("Telegram alert sent (%d part(s))", len(chunks))
