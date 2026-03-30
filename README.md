# News Intel Pipeline

Fetches, scores, and digests news relevant to the threat and opportunity landscape: quantum hardware progress, post-quantum cryptography, blockchain PQ migration, tokenized assets, and competitor activity.

Sources: NewsAPI, RSS feeds (CoinDesk, The Block, Ethereum Blog, Solana, arXiv, IACR ePrint, NIST, CISA, Google Security Blog, BIS, and more), and arXiv categories `quant-ph` + `cs.CR`.

---

## How It Fits Into the OpenClaw Workflow

This pipeline is designed to run as a scheduled job managed by [OpenClaw](https://openclaw.ai) — a personal AI gateway. The OpenClaw agent monitors the pipeline on a 6-hour cadence, verifies outputs, and delivers formatted digests and alerts to a Telegram group.

The integration works as follows:

- **Every 6 hours:** OpenClaw triggers `python run.py`, verifies the output files exist and are current, and checks `latest-alerts.md` for high-priority items (score ≥ 8). If alerts are present, they are sent immediately to the configured Telegram group.
- **Every 24 hours:** OpenClaw sends a rolling digest (`latest-digest-24h.md`) to the Telegram group.
- **Failure escalation:** If the pipeline fails twice in a row or output files are missing post-run, OpenClaw escalates to the operator.

The cron schedule, alert thresholds, and delivery channel are configured in `AGENTS.md` at the workspace root (separate from this repo). This repo contains only the pipeline code and its config.

---

## Prerequisites

- Python 3.10+
- A [NewsAPI](https://newsapi.org) key (free tier works, but news may be delayed by 24 hours)
- An [Anthropic](https://console.anthropic.com) API key (used for scoring relevance)
- OpenClaw installed and configured with a Telegram bot (for automated delivery)

---

## Install

```bash
git clone <this-repo>
cd eternax-intel
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Configure

### 1. API keys

```bash
cp .env.example .env
```

Edit `.env`:

```env
NEWSAPI_KEY=your_newsapi_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

### 2. Telegram delivery

Open `config.yaml` and set your Telegram bot token and target chat ID:

```yaml
telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: YOUR_CHAT_ID   # Use negative ID for groups, e.g. -5204706806
```

To get your group's chat ID: add `@userinfobot` to your Telegram group, send any message, and it will reply with the group ID.

To create a Telegram bot: message `@BotFather` on Telegram → `/newbot` → follow the prompts → copy the token.

### 3. Keywords and feeds (optional)

Edit `config.yaml` to add or remove keywords, RSS feeds, or adjust score thresholds:

```yaml
score_thresholds:
  minimum: 5        # Items below this are discarded
  tweet_suggestion: 7
  alert: 8          # Items at or above this trigger immediate alerts
```

---

## Run Manually

```bash
source venv/bin/activate
python run.py
```

The pipeline:
1. Fetches all sources (NewsAPI, RSS, arXiv, IACR ePrint)
2. Deduplicates against `data/seen.json` (items are never re-scored once seen)
3. Scores each new item with Claude Haiku (0–10 relevance score)
4. Discards items below `score_thresholds.minimum`
5. Writes dated archives and `latest-*` output files

---

## Outputs

| File | Contents |
|------|----------|
| `data/latest-digest.md` | Latest run window grouped by category, sorted by score |
| `data/latest-digest-24h.md` | Rolling last 24 hours (for daily delivery) |
| `data/latest-tweets.md` | Items scoring ≥ 7 with tweet-ready copy angles |
| `data/latest-alerts.md` | Items scoring ≥ 8 — high priority |
| `data/digests/YYYY-MM-DD-HHMMZ-digest.md` | Per-run digest archive |
| `data/digests/YYYY-MM-DD-items.json` | Per-day scored item ledger |
| `data/tweets/YYYY-MM-DD-tweets.md` | Dated tweet archive |
| `data/alerts/YYYY-MM-DD-alerts.md` | Dated alerts archive (only written if alerts exist) |

---

## Score Thresholds

| Score | Meaning |
|-------|---------|
| 8–10 | Alert — direct threat or major opportunity; deliver immediately |
| 7 | Tweet suggestion — strong market signal |
| 4–6 | Digest only — useful context |
| < 4 | Discarded |

---

## Automated Scheduling with OpenClaw

OpenClaw manages the run schedule via its built-in cron system. If you are running OpenClaw, add the following cron jobs via `openclaw cron add` or the OpenClaw Control UI:

**6-hour pipeline run:**
```json
{
  "name": "news-intel-6h-run",
  "schedule": { "kind": "every", "everyMs": 21600000 },
  "payload": {
    "kind": "agentTurn",
    "message": "Run the News Intel pipeline: activate venv, run python run.py in news-intel/, verify outputs, send alerts if any items score >= 8 to the configured Telegram group."
  }
}
```

**Daily 24h digest:**
```json
{
  "name": "news-intel-daily-digest",
  "schedule": { "kind": "cron", "expr": "0 7 * * *", "tz": "UTC" },
  "payload": {
    "kind": "agentTurn",
    "message": "Send the News Intel daily digest (data/latest-digest-24h.md) to the configured Telegram group."
  }
}
```

**Without OpenClaw** — use a standard cron job:
```bash
# Every 6 hours: fetch, score, write outputs
0 */6 * * * cd /path/to/news-intel && source venv/bin/activate && python run.py >> data/run.log 2>&1
```

For Telegram delivery without OpenClaw, the pipeline writes `latest-alerts.md` and `latest-digest-24h.md` — you can wrap `run.py` in a shell script that reads those files and POSTs to the Telegram Bot API directly.

---

## Workspace

Output files (`data/`) and the deduplication ledger (`data/seen.json`) are gitignored. Each run is append-safe — no data is lost on re-run.

The `venv/` directory is also gitignored and should be recreated locally.
