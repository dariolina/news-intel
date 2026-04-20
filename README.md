# News Intel Pipeline

Fetches, scores, and digests news relevant to the threat and opportunity landscape: quantum hardware progress, post-quantum cryptography, blockchain PQ migration, tokenized assets, and competitor activity.

Sources: NewsAPI, RSS feeds (CoinDesk, The Block, Ethereum Blog, Solana, arXiv, IACR ePrint, NIST, CISA, Google Security Blog, BIS, and more), and arXiv categories `quant-ph` + `cs.CR`.

---

## Scheduling and delivery

- **Pipeline (`run.py`):** Fetches sources, scores new items, writes `data/latest-*.md`. If any item meets the alert threshold (`score_thresholds.alert` in `config.yaml`, default behavior is score ≥ 8), the same content as `data/latest-alerts.md` is sent to Telegram (plain text, split automatically if longer than Telegram’s message limit).
- **Daily digest :** `send-daily-digest.sh` sends `data/latest-digest-24h.md` to Telegram. Run it from cron separately if you want a daily summary in addition to alert pushes.

See `crontab.example` for sample `crontab` lines. Copy it, replace `/path/to/news-intel`, then merge the lines into your user crontab with `crontab -e`.

---

## Prerequisites

- Python 3.10+
- A [NewsAPI](https://newsapi.org) key (free tier works, but news may be delayed by 24 hours)
- An [OpenAI](https://platform.openai.com) API key (used for scoring relevance)
- A [Telegram](https://core.telegram.org/bots) bot token and target chat ID (for alerts and optional digest script)

---

## Install

```bash
git clone <this-repo>
cd news-intel
python3 -m venv venv
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
OPENAI_API_KEY=your_openai_key_here
OPENAI_MODEL=gpt-4.1-mini
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
YOUR_CONTEXT=your_context_here
```

Telegram credentials are required only if you want alert delivery (and for `send-daily-digest.sh`). If they are missing, the pipeline still runs; high-priority items are only written to disk.

### 2. Telegram

`config.yaml` lists `telegram.bot_token` and `telegram.chat_id` as placeholders; the running process reads **`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the environment** (e.g. via `.env` loaded by `run.py`).

To get your group's chat ID: add `@userinfobot` to your Telegram group, send any message, and it will reply with the group ID.

To create a Telegram bot: message `@BotFather` on Telegram → `/newbot` → follow the prompts → copy the token.

### 3. Keywords and feeds (optional)

Edit `config.yaml` to add or remove keywords, RSS feeds, or adjust score thresholds:

```yaml
score_thresholds:
  minimum: 5        # Items below this are discarded
  tweet_suggestion: 7
  alert: 8          # Items at or above this trigger immediate alerts + Telegram (if configured)
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
3. Scores each new item with OpenAI (0–10 relevance score)
4. Discards items below `score_thresholds.minimum`
5. Writes dated archives and `latest-*` output files
6. Sends Telegram notification when this run produced one or more alert-tier items and Telegram env vars are set

---

## Outputs

| File | Contents |
|------|----------|
| `data/latest-digest.md` | Latest run window grouped by category, sorted by score |
| `data/latest-digest-24h.md` | Rolling last 24 hours (for daily delivery) |
| `data/latest-tweets.md` | Items scoring ≥ 7 with tweet-ready copy angles |
| `data/latest-alerts.md` | Items at or above the alert threshold — high priority |
| `data/digests/YYYY-MM-DD-HHMMZ-digest.md` | Per-run digest archive |
| `data/digests/YYYY-MM-DD-items.json` | Per-day scored item ledger |
| `data/tweets/YYYY-MM-DD-tweets.md` | Dated tweet archive |
| `data/alerts/YYYY-MM-DD-alerts.md` | Dated alerts archive (only written if alerts exist) |

---

## Score Thresholds

| Score | Meaning |
|-------|---------|
| 8–10 | Alert — direct threat or major opportunity; Telegram when configured |
| 7 | Tweet suggestion — strong market signal |
| 4–6 | Digest only — useful context |
| < 4 | Discarded |

---

## Cron quick reference

```bash
# Every 8 hours at 06:00, 14:00, 22:00 local time (pipeline + Telegram alerts when applicable)
0 6,14,22 * * * cd /path/to/news-intel && . venv/bin/activate && python run.py >> /path/to/news-intel/data/run.log 2>&1
```

For a full two-job example (pipeline + daily digest script), see `crontab.example`.

---

## Workspace

Output files (`data/`) and the deduplication ledger (`data/seen.json`) are gitignored. Each run is append-safe — no data is lost on re-run.

The `venv/` directory is also gitignored and should be recreated locally.
