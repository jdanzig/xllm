# xllm — Twitter/X Monitoring Service

Feed it an English description of what you want to watch, and it monitors Twitter/X for matching posts on a configurable schedule.

Claude converts your description into an optimized Twitter search query, polls the API at your chosen interval, and saves results to a JSON file. Optionally, Claude can also filter each batch of results for relevance before saving.

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

# Use Claude to filter results for stronger relevance
python monitor.py "criticism of electric vehicles" --filter

# Override the Claude-generated query with your own Twitter search query
python monitor.py "crypto scams" --query '"rug pull" OR "crypto scam" -giveaway'

# Remove the language filter (default is English only)
python monitor.py "football results" --lang ""
```

### All options

| Flag | Default | Description |
|------|---------|-------------|
| `description` | *(required)* | English description of what to monitor |
| `--interval` | `30m` | How often to check (`10m`, `1h`, `2h`, etc.) |
| `--duration` | `2h` | Total monitoring window (`4h`, `1d`, etc.) |
| `--once` | off | Perform a single check then exit |
| `--max-results` | `100` | Tweets to fetch per check (10–100) |
| `--filter` | off | Use Claude (Haiku) to filter results for relevance |
| `--query` | *(auto)* | Override the Claude-generated query |
| `--lang` | `en` | Language filter (ISO 639-1 code, or `""` to disable) |
| `--output` | *(auto)* | Output JSON file path |

Duration strings accept `s`, `m`, `h`, or `d` suffixes (e.g. `90s`, `45m`, `3h`, `1d`).

## How it works

1. **Query generation** — Claude (Sonnet) reads your English description and produces an optimized Twitter search query using operators like `OR`, quoted phrases, and hashtags.
2. **Periodic search** — tweepy calls the Twitter v2 `search_recent_tweets` endpoint at the chosen interval, using `since_id` to skip already-seen tweets across checks.
3. **Relevance filter** *(optional, `--filter`)* — Claude (Haiku) reads each fetched tweet and discards off-topic results before saving.
4. **Output** — Results are displayed in a table in the terminal and written to a JSON file after every check. The file is always up to date, so interrupting early (Ctrl+C) loses nothing.

## Output format

Results are saved to `monitor_<timestamp>.json` (or the path you specify with `--output`):

```json
{
  "description": "posts about AI safety regulations",
  "query": "\"AI safety\" OR \"AI regulation\" ...",
  "full_query": "(...) -is:retweet lang:en",
  "started_at": "2026-04-22T14:00:00+00:00",
  "checks": [
    {
      "check_number": 1,
      "checked_at": "2026-04-22T14:00:01+00:00",
      "raw_count": 23,
      "displayed_count": 23,
      "tweets": [
        {
          "id": "...",
          "text": "...",
          "author": "username",
          "created_at": "...",
          "likes": 42,
          "retweets": 7,
          "url": "https://x.com/username/status/..."
        }
      ]
    }
  ],
  "total_tweets": 23
}
```

## Notes

- Twitter's `search_recent_tweets` endpoint covers the **last 7 days** only.
- The Basic tier allows up to **10,000 tweet reads/month**. With `--max-results 100` and `--interval 30m` over 2 hours, a single session reads at most ~500 tweets.
- `--filter` adds one Claude Haiku API call per check; at typical tweet volumes the cost is negligible.
