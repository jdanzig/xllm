#!/usr/bin/env python3
"""
Twitter/X Monitoring Service

Feed it an English description of what you want to monitor, and it periodically
searches Twitter/X for matching posts over a configurable time window. Claude
turns your description into a search query; results are stored in SQLite with
cross-session deduplication.

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

    # Only surface tweets with real engagement
    python monitor.py "criticism of electric vehicles" --min-likes 25

    # Use Claude to filter results, then summarise at the end
    python monitor.py "AI safety debate" --filter --summary

    # Get pinged on Slack/Discord when new matches appear
    python monitor.py "our product name" --webhook https://hooks.slack.com/services/...

    # Interactively refine the query after the first batch of results
    python monitor.py "tesla news" --refine

    # Cap monthly Twitter reads (stops before exceeding the budget)
    python monitor.py "crypto scams" --budget 5000

    # Resume the most recent session where it left off
    python monitor.py --resume

    # Watch several topics at once from a config file
    python monitor.py --config topics.example.yaml

Environment variables (set in .env or shell):
    TWITTER_BEARER_TOKEN   Twitter/X API Bearer Token (Basic tier or higher required)
    ANTHROPIC_API_KEY      Anthropic API key for Claude
"""

import argparse
import os
import signal
import sys
import threading

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from twitter_monitor.config import Topic, load_topics
from twitter_monitor.runner import MonitorRunner
from twitter_monitor.storage import Storage

load_dotenv()
console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor Twitter/X for posts matching an English description",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "description", nargs="?",
        help="English description of what to monitor (omit when using --config or --resume)",
    )
    parser.add_argument("--name", help="Short label for this topic (used in output and storage)")
    parser.add_argument("--interval", default="30m", help="How often to check (e.g. 10m, 1h). Default: 30m")
    parser.add_argument("--duration", default="2h", help="Total monitoring window (e.g. 4h, 1d). Default: 2h")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    parser.add_argument("--max-results", type=int, default=100, help="Tweets to fetch per check (10–100). Default: 100")
    parser.add_argument("--filter", action="store_true", help="Use Claude to filter results for relevance")
    parser.add_argument("--query", help="Override the Claude-generated Twitter search query")
    parser.add_argument("--lang", default="en", help="Language filter (ISO 639-1, e.g. 'en'). Pass '' to disable. Default: en")
    parser.add_argument("--min-likes", type=int, default=0, help="Only surface tweets with at least this many likes")
    parser.add_argument("--webhook", help="Slack or Discord webhook URL for new-tweet alerts")
    parser.add_argument("--summary", action="store_true", help="Generate a Claude brief at the end of the session")
    parser.add_argument("--refine", action="store_true", help="Interactively refine the query after the first check")
    parser.add_argument("--budget", type=int, help="Maximum Twitter tweet-reads allowed this calendar month")
    parser.add_argument("--db", default="monitor.db", help="SQLite database path. Default: monitor.db")
    parser.add_argument("--config", help="YAML/JSON config file describing multiple topics to watch concurrently")
    parser.add_argument("--resume", nargs="?", const="__latest__", help="Resume a session (optionally by id; defaults to the most recent)")
    return parser


def topic_from_args(args) -> Topic:
    return Topic(
        description=args.description,
        name=args.name,
        query=args.query,
        interval=args.interval,
        duration=args.duration,
        once=args.once,
        max_results=args.max_results,
        filter=args.filter,
        lang=args.lang,
        min_likes=args.min_likes,
        webhook=args.webhook,
        summary=args.summary,
        refine=args.refine,
    )


def main() -> None:
    args = build_parser().parse_args()

    missing = [v for v in ("TWITTER_BEARER_TOKEN", "ANTHROPIC_API_KEY") if not os.environ.get(v)]
    if missing:
        console.print(f"[red]Missing environment variable(s): {', '.join(missing)}[/red]")
        console.print("Copy [bold].env.example[/bold] to [bold].env[/bold] and fill in your API keys.")
        sys.exit(1)

    console.print(Panel.fit(
        "[bold cyan]Twitter/X Monitoring Service[/bold cyan]",
        border_style="cyan",
    ))

    stop_event = threading.Event()

    def _sigint(sig, frame):  # noqa: ANN001
        console.print("\n[yellow]Interrupt received — finishing current cycle and stopping…[/yellow]")
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)

    # ---- Resume mode --------------------------------------------------------
    resume_session = None
    if args.resume:
        store = Storage(args.db)
        sid = None if args.resume == "__latest__" else args.resume
        resume_session = store.get_session(sid)
        store.close()
        if not resume_session:
            console.print("[red]No session found to resume.[/red]")
            sys.exit(1)
        import json as _json
        cfg = _json.loads(resume_session["config"])
        topics = [Topic(**cfg)]
        console.print(f"[cyan]Resuming '{resume_session['description']}'[/cyan]")

    # ---- Config (multi-topic) mode -----------------------------------------
    elif args.config:
        try:
            topics = load_topics(args.config)
        except (OSError, ValueError) as e:
            console.print(f"[red]Config error: {e}[/red]")
            sys.exit(1)
        console.print(f"[cyan]Loaded {len(topics)} topic(s) from {args.config}[/cyan]")

    # ---- Single-topic CLI mode ---------------------------------------------
    else:
        if not args.description:
            console.print("[red]Provide a description, or use --config / --resume.[/red]")
            build_parser().print_help()
            sys.exit(1)
        topics = [topic_from_args(args)]

    multi = len(topics) > 1

    def make_runner(topic: Topic) -> MonitorRunner:
        runner = MonitorRunner(
            topic=topic,
            db_path=args.db,
            stop_event=stop_event,
            console=console,
            budget=args.budget,
            resume_session=resume_session,
            interactive=not multi,  # interactive refinement only when single-topic
        )
        runner._multi = multi
        return runner

    # ---- Run ----------------------------------------------------------------
    if multi:
        if any(t.refine for t in topics):
            console.print("[yellow]Note: --refine is disabled in multi-topic mode (non-interactive).[/yellow]")
        threads = []
        for topic in topics:
            runner = make_runner(topic)
            th = threading.Thread(target=_safe_run, args=(runner, console), daemon=True)
            th.start()
            threads.append(th)
        # Keep the main thread alive so the signal handler stays responsive.
        for th in threads:
            while th.is_alive():
                th.join(timeout=0.5)
    else:
        _safe_run(make_runner(topics[0]), console)

    console.print("\n[bold green]All monitoring complete.[/bold green]")


def _safe_run(runner: MonitorRunner, console: Console) -> None:
    try:
        runner.run()
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Runner error ({runner.topic.label}): {e}[/red]")


if __name__ == "__main__":
    main()
