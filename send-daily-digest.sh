#!/usr/bin/env bash
# send-daily-digest.sh
# Reads latest-digest-24h.md and sends it to the EternaX News Intel Telegram group.
# No model needed — pure shell.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIGEST_FILE="$SCRIPT_DIR/data/latest-digest-24h.md"

# Load .env if present so secrets are not hardcoded.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/.env"
  set +a
fi

: "${TELEGRAM_BOT_TOKEN:?ERROR: TELEGRAM_BOT_TOKEN is not set (check .env)}"
: "${TELEGRAM_CHAT_ID:?ERROR: TELEGRAM_CHAT_ID is not set (check .env)}"

TG_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"

if [[ ! -f "$DIGEST_FILE" ]]; then
  echo "ERROR: $DIGEST_FILE not found." >&2
  exit 1
fi

CONTENT="$(cat "$DIGEST_FILE")"

if [[ -z "$CONTENT" ]]; then
  echo "ERROR: $DIGEST_FILE is empty." >&2
  exit 1
fi

# Telegram has a 4096-char limit per message. Split if needed.
MAX=4000
TOTAL=${#CONTENT}
OFFSET=0

while [[ $OFFSET -lt $TOTAL ]]; do
  CHUNK="${CONTENT:$OFFSET:$MAX}"
  OFFSET=$((OFFSET + MAX))

  RESPONSE="$(curl -s -X POST "$TG_URL" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg chat "$TELEGRAM_CHAT_ID" --arg text "$CHUNK" \
      '{chat_id: $chat, text: $text}')")"

  if ! jq -e '.ok == true' > /dev/null <<< "$RESPONSE"; then
    ERR_CODE="$(jq -r '.error_code // "unknown"' <<< "$RESPONSE")"
    ERR_DESC="$(jq -r '.description // "No description from Telegram API"' <<< "$RESPONSE")"
    echo "ERROR: Telegram API call failed (code: $ERR_CODE, description: $ERR_DESC)." >&2
    echo "DEBUG: Telegram raw response: $RESPONSE" >&2
    exit 1
  fi
done

echo "Digest sent successfully ($TOTAL chars)."
