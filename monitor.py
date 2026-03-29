#!/usr/bin/env python3
"""
Twitter/X Monitoring Service

Feed it an English description of what you want to monitor, and it periodically
searches Twitter/X for matching posts over a configurable time window.

Requirements:
    pip install -r requirements.txt
    cp .env.example .env   # then fill in your API keys

Usage examples:
    # Monitor for 2 hours, checking every 30 minutes (defaults)
    python monitor.py "posts discussing concerns about AI safety regulations"

    # Check once and exit
    python monitor.py "posts about SpaceX launches" --once

    # Custom interval and duration
    python monitor.py "startup funding announcements" --interval 15m --duration 6h

    # Use Claude to filter results for stronger relevance
    python monitor.py "posts criticising electric vehicles" --filter

    # Override the Claude-generated query with your own
    python monitor.py "crypto scams" --query '"rug pull" OR "crypto scam" -giveaway'

    # Broader language scope (default is English only)
    python monitor.py "football results" --lang ""

Environment variables (set in .env or shell):
    TWITTER_BEARER_TOKEN   Twitter/X API Bearer Token (Basic tier or higher required)
    ANTHROPIC_API_KEY      Anthropic API key for Claude
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from twitter_monitor.query_generator import QueryGenerator
from twitter_monitor.twitter_client import TwitterClient

load_dotenv()

console = Console()


# ---------------------------------------------------------------------------
# Duration helpers
# ---------------------------------------------------------------------------

def parse_duration(s: str) -> int:
    """Parse a human duration string (e.g. '30m', '2h', '1d') into seconds."""
    s = s.strip().lower()
    multipliers = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return int(s[:-1]) * mult
    return int(s)  # bare integer → seconds


def fmt_duration(seconds: int) -> str:
    """Format a number of seconds as a concise human-readable string."""
    if seconds >= 86400:
        h = (seconds % 86400) // 3600
        return f"{seconds // 86400}d {h}h" if h else f"{seconds // 86400}d"
    if seconds >= 3600:
        m = (seconds % 3600) // 60
        return f"{seconds // 3600}h {m}m" if m else f"{seconds // 3600}h"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor Twitter/X for posts matching an English description",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("description", help="English description of what to monitor")
    parser.add_argument(
        "--interval", default="30m",
        help="How often to check (e.g. 10m, 1h). Default: 30m",
    )
    parser.add_argument(
        "--duration", default="2h",
        help="Total monitoring window (e.g. 4h, 1d). Default: 2h",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Perform a single check then exit (ignores --interval and --duration)",
    )
    parser.add_argument(
        "--max-results", type=int, default=100,
        help="Maximum tweets to fetch per check (10–100). Default: 100",
    )
    parser.add_argument(
        "--filter", action="store_true",
        help="Use Claude to filter fetched tweets for relevance (costs extra API calls)",
    )
    parser.add_argument(
        "--query",
        help="Override the Claude-generated Twitter search query with your own",
    )
    parser.add_argument(
        "--lang", default="en",
        help="Language filter (ISO 639-1 code, e.g. 'en', 'es'). Pass '' to disable. Default: en",
    )
    parser.add_argument(
        "--output",
        help="JSON output file path. Default: monitor_<timestamp>.json",
    )

    args = parser.parse_args()

    # Validate env vars early
    missing = [v for v in ("TWITTER_BEARER_TOKEN", "ANTHROPIC_API_KEY") if not os.environ.get(v)]
    if missing:
        console.print(f"[red]Missing environment variable(s): {', '.join(missing)}[/red]")
        console.print("Copy [bold].env.example[/bold] to [bold].env[/bold] and fill in your API keys.")
        sys.exit(1)

    interval_secs = 0 if args.once else parse_duration(args.interval)
    duration_secs = 0 if args.once else parse_duration(args.duration)

    if not args.once and interval_secs > duration_secs:
        console.print("[red]--interval cannot be longer than --duration[/red]")
        sys.exit(1)

    output_path = args.output or f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # ---------------------------------------------------------------------------
    # Initialise clients
    # ---------------------------------------------------------------------------
    try:
        twitter = TwitterClient()
        generator = QueryGenerator()
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Generate (or accept) the search query
    # ---------------------------------------------------------------------------
    console.print(Panel.fit(
        f"[bold cyan]Twitter/X Monitoring Service[/bold cyan]\n"
        f"[dim]Monitoring:[/dim] {args.description}",
        border_style="cyan",
    ))

    if args.query:
        query = args.query
        console.print(f"[green]Using provided query:[/green] [bold]{query}[/bold]")
    else:
        console.print("[yellow]Generating search query with Claude…[/yellow]")
        try:
            query, explanation = generator.generate_search_query(args.description)
        except Exception as e:
            console.print(f"[red]Failed to generate query: {e}[/red]")
            sys.exit(1)
        console.print(f"[green]Generated query:[/green] [bold]{query}[/bold]")
        console.print(f"[dim]{explanation}[/dim]")

    # Build the full query sent to Twitter
    parts = [f"({query})", "-is:retweet"]
    if args.lang:
        parts.append(f"lang:{args.lang}")
    full_query = " ".join(parts)
    console.print(f"[dim]Full Twitter query: {full_query}[/dim]")

    if not args.once:
        console.print(
            f"\n[cyan]Monitoring for [bold]{fmt_duration(duration_secs)}[/bold]"
            f" — checking every [bold]{fmt_duration(interval_secs)}[/bold][/cyan]"
        )

    # ---------------------------------------------------------------------------
    # Results structure
    # ---------------------------------------------------------------------------
    results: dict = {
        "description": args.description,
        "query": query,
        "full_query": full_query,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "interval_seconds": interval_secs,
            "duration_seconds": duration_secs,
            "once": args.once,
            "max_results": args.max_results,
            "filter_enabled": args.filter,
            "lang": args.lang,
        },
        "checks": [],
        "total_tweets": 0,
    }

    def save_results() -> None:
        with open(output_path, "w") as fh:
            json.dump(results, fh, indent=2, default=str)

    # ---------------------------------------------------------------------------
    # Graceful shutdown on Ctrl+C
    # ---------------------------------------------------------------------------
    running = [True]

    def _sigint_handler(sig, frame):  # noqa: ANN001
        console.print("\n[yellow]Interrupt received — finishing current cycle and stopping…[/yellow]")
        running[0] = False

    signal.signal(signal.SIGINT, _sigint_handler)

    # ---------------------------------------------------------------------------
    # Monitoring loop
    # ---------------------------------------------------------------------------
    since_id: str | None = None
    check_num = 0
    start_time = time.time()
    total_tweets = 0

    while running[0]:
        check_num += 1
        check_time = datetime.now(timezone.utc)
        console.print(f"\n[bold]Check #{check_num}[/bold]  {check_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        # -- Fetch tweets -------------------------------------------------------
        raw_tweets: list[dict] = []
        try:
            raw_tweets = twitter.search_recent(
                query=full_query,
                max_results=args.max_results,
                since_id=since_id,
            )
        except Exception as e:
            console.print(f"[red]Search error: {e}[/red]")

        # Track the newest ID *before* any filtering so we don't re-fetch
        if raw_tweets:
            since_id = raw_tweets[0]["id"]

        # -- Optional relevance filter ------------------------------------------
        display_tweets = raw_tweets
        filtered_count: int | None = None
        if args.filter and raw_tweets:
            console.print(f"[yellow]Filtering {len(raw_tweets)} tweets with Claude…[/yellow]")
            try:
                display_tweets = generator.filter_tweets(args.description, raw_tweets)
                filtered_count = len(display_tweets)
                console.print(f"[dim]{filtered_count}/{len(raw_tweets)} tweets passed relevance filter[/dim]")
            except Exception as e:
                console.print(f"[yellow]Relevance filter skipped: {e}[/yellow]")
                display_tweets = raw_tweets

        total_tweets += len(display_tweets)

        # -- Display ------------------------------------------------------------
        if display_tweets:
            table = Table(show_header=True, header_style="bold cyan", expand=False)
            table.add_column("Author", style="green", no_wrap=True, max_width=20)
            table.add_column("Tweet", max_width=70)
            table.add_column("♥", justify="right", no_wrap=True, width=6)
            table.add_column("RT", justify="right", no_wrap=True, width=5)
            table.add_column("Posted (UTC)", no_wrap=True, width=17)

            for tweet in display_tweets[:25]:
                text = tweet["text"]
                if len(text) > 140:
                    text = text[:137] + "…"
                created = (tweet.get("created_at") or "")[:16].replace("T", " ")
                table.add_row(
                    f"@{tweet['author']}",
                    text,
                    str(tweet.get("likes", 0)),
                    str(tweet.get("retweets", 0)),
                    created,
                )

            console.print(table)
            if len(display_tweets) > 25:
                console.print(f"[dim]…{len(display_tweets) - 25} more tweets saved to output file[/dim]")
        else:
            console.print("[dim]No new tweets found this check.[/dim]")

        # -- Persist ------------------------------------------------------------
        results["checks"].append({
            "check_number": check_num,
            "checked_at": check_time.isoformat(),
            "raw_count": len(raw_tweets),
            "displayed_count": len(display_tweets),
            "tweets": display_tweets,
        })
        results["total_tweets"] = total_tweets
        save_results()

        # -- Decide whether to continue -----------------------------------------
        if args.once:
            break

        elapsed = time.time() - start_time
        remaining = duration_secs - elapsed

        if remaining <= 0:
            console.print(
                f"\n[green]Monitoring window of {fmt_duration(duration_secs)} complete.[/green]"
            )
            break

        if not running[0]:
            break

        sleep_secs = min(interval_secs, remaining)
        console.print(
            f"[dim]Next check in {fmt_duration(int(sleep_secs))} "
            f"| {fmt_duration(int(remaining))} remaining "
            f"| {total_tweets} total tweets collected[/dim]"
        )

        # Sleep in 1-second ticks so Ctrl+C is responsive
        deadline = time.time() + sleep_secs
        while time.time() < deadline and running[0]:
            time.sleep(min(1.0, deadline - time.time()))

    # ---------------------------------------------------------------------------
    # Final summary
    # ---------------------------------------------------------------------------
    results["ended_at"] = datetime.now(timezone.utc).isoformat()
    results["total_tweets"] = total_tweets
    save_results()

    console.print(
        f"\n[bold green]Done![/bold green] "
        f"{total_tweets} tweets collected across {check_num} check(s). "
        f"Results saved to [cyan]{output_path}[/cyan]"
    )


if __name__ == "__main__":
    main()
