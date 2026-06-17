# xllm — Twitter/X Monitoring Service

Feed it an English description of what you want to watch, and it monitors Twitter/X for matching posts on a configurable schedule.

Claude converts your description into an optimized Twitter search query, polls the API at your chosen interval, and stores results in SQLite with cross-session deduplication. It can also filter results for relevance, alert you over Slack/Discord, summarize what it found, refine the query interactively, watch many topics at once, resume where it left off, and stay within a monthly read budget.

## Requirements

- Python 3.10+
- [Twitter/X Developer account](https://developer.twitter.com/en/portal/dashboard) — **Basic tier or higher** (free tier does not include search)
- [Anthropic API key](https://console.anthropic.com/)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in TWITTER_BEARER_TOKEN and ANTHROPIC_API_KEY
```

## Usage

```bash
# Monitor for 2 hours, checking every 30 minutes (defaults)
python monitor.py "posts discussing concerns about AI safety regulations"

# Check once and exit
python monitor.py "posts about SpaceX launches" --once

# Custom interval and duration
python monitor.py "startup funding announcements" --interval 15m --duration 6h

# Only surface tweets with real engagement
python monitor.py "criticism of electric vehicles" --min-likes 25

# Use Claude to filter results, then summarize at the end
python monitor.py "the AI safety debate" --filter --summary

# Get pinged on Slack or Discord when new matches appear
python monitor.py "our product name" --webhook https://hooks.slack.com/services/...

# Interactively refine the query after the first batch of results
python monitor.py "tesla news" --refine

# Cap monthly Twitter reads (stops before exceeding the budget)
python monitor.py "crypto scams" --budget 5000

# Resume the most recent session where it left off
python monitor.py --resume

# Watch several topics at once from a config file
python monitor.py --config topics.example.yaml
```

### All options

| Flag | Default | Description |
|------|---------|-------------|
| `description` | *(required\*)* | English description of what to monitor (\*omit when using `--config`/`--resume`) |
| `--name` | *(auto)* | Short label for the topic, used in output and storage |
| `--interval` | `30m` | How often to check (`10m`, `1h`, `2h`, etc.) |
| `--duration` | `2h` | Total monitoring window (`4h`, `1d`, etc.) |
| `--once` | off | Perform a single check then exit |
| `--max-results` | `100` | Tweets to fetch per check (10–100) |
| `--filter` | off | Use Claude (Haiku) to filter results for relevance |
| `--query` | *(auto)* | Override the Claude-generated query |
| `--lang` | `en` | Language filter (ISO 639-1 code, or `""` to disable) |
| `--min-likes` | `0` | Only surface tweets with at least this many likes (via `min_faves:`) |
| `--webhook` | *(none)* | Slack or Discord webhook URL for new-tweet alerts |
| `--summary` | off | Generate a Claude brief at the end of the session |
| `--refine` | off | Interactively refine the query after the first check |
| `--budget` | *(none)* | Maximum Twitter tweet-reads allowed this calendar month |
| `--db` | `monitor.db` | SQLite database path |
| `--config` | *(none)* | YAML/JSON config of multiple topics to watch concurrently |
| `--resume` | *(none)* | Resume a session (optionally by id; defaults to the most recent) |

Duration strings accept `s`, `m`, `h`, or `d` suffixes (e.g. `90s`, `45m`, `3h`, `1d`).

## How it works

1. **Query generation** — Claude (Sonnet) reads your description and produces an optimized Twitter search query using operators like `OR`, quoted phrases, and hashtags.
2. **Periodic search** — tweepy calls the Twitter v2 `search_recent_tweets` endpoint at the chosen interval, using `since_id` to skip already-seen tweets within a session.
3. **Cross-session dedup** — every tweet id is recorded in SQLite, so restarting a monitor never re-surfaces a tweet you've already seen.
4. **Relevance filter** *(optional, `--filter`)* — Claude (Haiku) reads each fetched tweet and discards off-topic results before saving.
5. **Alerting** *(optional, `--webhook`)* — new matches are pushed to Slack or Discord (platform auto-detected from the URL).
6. **Refinement** *(optional, `--refine`)* — after the first check you can give feedback and Claude rewrites the query before continuing.
7. **Summary** *(optional, `--summary`)* — at the end, Claude writes a brief of themes, notable accounts, sentiment, and what's gaining traction.
8. **Budget** *(optional, `--budget`)* — monthly tweet-read usage is tracked in SQLite and the run stops before exceeding your cap.

## Multi-topic config

Watch several things at once with a YAML (or JSON) file. `defaults` apply to every topic and can be overridden per topic. Each topic runs in its own thread against a shared database (so dedup and budget are coordinated).

```yaml
defaults:
  interval: 20m
  duration: 6h
  lang: en

topics:
  - description: "people reacting to our new product launch"
    name: launch
    min_likes: 10
    filter: true
    summary: true
  - description: "discussion of competing products in our space"
    name: competitors
    webhook: https://hooks.slack.com/services/XXX/YYY/ZZZ
  - description: "crypto rug pulls and scams"
    name: scams
    query: '"rug pull" OR "crypto scam" -giveaway'
```

```bash
python monitor.py --config topics.example.yaml
```

See [`topics.example.yaml`](topics.example.yaml) for a complete example.

## Storage

Results are stored in a SQLite database (`monitor.db` by default) with these tables:

- **sessions** — one row per run: description, query, config, `since_id`, status, and end-of-session summary. Used for resuming.
- **checks** — metadata for each poll (time, raw vs. displayed counts).
- **tweets** — every tweet collected, keyed by `(tweet_id, session_id)`.
- **seen_tweets** — every tweet id ever surfaced, for cross-session deduplication.
- **quota** — monthly tweet-read totals, for `--budget` enforcement.

Inspect it with any SQLite client, e.g.:

```bash
sqlite3 monitor.db "SELECT author, likes, text FROM tweets ORDER BY likes DESC LIMIT 10;"
```

## Notes

- Twitter's `search_recent_tweets` endpoint covers the **last 7 days** only.
- The Basic tier allows up to **10,000 tweet reads/month**. Use `--budget` to stay under your cap.
- `--filter` and `--summary` add Claude API calls; at typical tweet volumes the cost is negligible.
- `--refine` is interactive and therefore only available in single-topic mode (it's skipped automatically when running `--config`).
