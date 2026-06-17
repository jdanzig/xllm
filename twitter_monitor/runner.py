"""
The monitoring loop for a single topic.

A `MonitorRunner` owns its own Twitter client, query generator and storage
connection, so multiple runners can execute concurrently in separate threads
(one per topic). Shared concerns — the stop signal and the monthly budget — are
injected so all runners cooperate.
"""

import time
import uuid
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import Topic
from .duration import fmt_duration, parse_duration
from .notifier import Notifier
from .query_generator import QueryGenerator
from .storage import Storage
from .summarizer import Summarizer
from .twitter_client import TwitterClient


class MonitorRunner:
    def __init__(
        self,
        topic: Topic,
        db_path: str,
        stop_event,
        console: Console,
        budget: int | None = None,
        resume_session: dict | None = None,
        interactive: bool = True,
    ):
        self.topic = topic
        self.console = console
        self.stop_event = stop_event
        self.budget = budget
        self.resume_session = resume_session
        self.interactive = interactive

        self.twitter = TwitterClient()
        self.generator = QueryGenerator()
        self.storage = Storage(db_path)
        self.notifier = Notifier(topic.webhook) if topic.webhook else None
        self.summarizer = Summarizer() if topic.summary else None

        self.total_displayed = 0

    # -- helpers ------------------------------------------------------------

    def _log(self, msg: str) -> None:
        prefix = f"[dim]\\[{self.topic.label[:24]}][/dim] " if self._multi else ""
        self.console.print(prefix + msg)

    # Set by the orchestrator when more than one topic runs concurrently.
    _multi = False

    def _build_full_query(self, query: str) -> str:
        parts = [f"({query})", "-is:retweet"]
        if self.topic.min_likes > 0:
            parts.append(f"min_faves:{self.topic.min_likes}")
        if self.topic.lang:
            parts.append(f"lang:{self.topic.lang}")
        return " ".join(parts)

    # -- main entry ---------------------------------------------------------

    def run(self) -> None:
        topic = self.topic
        once = topic.once
        interval_secs = 0 if once else parse_duration(topic.interval)
        duration_secs = 0 if once else parse_duration(topic.duration)

        # ---- Resume or start a fresh session ------------------------------
        if self.resume_session:
            session_id = self.resume_session["session_id"]
            query = self.resume_session["query"]
            full_query = self.resume_session["full_query"]
            since_id = self.resume_session.get("since_id")
            started_epoch = _iso_to_epoch(self.resume_session["started_at"])
            self._log(f"[cyan]Resuming session {session_id}[/cyan]")
        else:
            session_id = uuid.uuid4().hex[:12]
            since_id = None
            started_epoch = time.time()

            if topic.query:
                query = topic.query
                self._log(f"[green]Using provided query:[/green] [bold]{query}[/bold]")
            else:
                self._log("[yellow]Generating search query with Claude…[/yellow]")
                query, explanation = self.generator.generate_search_query(topic.description)
                self._log(f"[green]Generated query:[/green] [bold]{query}[/bold]")
                self._log(f"[dim]{explanation}[/dim]")

            full_query = self._build_full_query(query)
            self._log(f"[dim]Full Twitter query: {full_query}[/dim]")

            self.storage.create_session(
                session_id=session_id,
                slug=topic.slug,
                description=topic.description,
                query=query,
                full_query=full_query,
                config=topic.to_dict(),
            )

        if not once:
            self._log(
                f"[cyan]Monitoring for [bold]{fmt_duration(duration_secs)}[/bold]"
                f" — every [bold]{fmt_duration(interval_secs)}[/bold][/cyan]"
            )

        # ---- Loop ---------------------------------------------------------
        check_num = 0
        first_check = True

        while not self.stop_event.is_set():
            # Budget guard (monthly, shared across topics)
            if self.budget is not None:
                used = self.storage.get_quota_usage()
                if used + topic.max_results > self.budget:
                    self._log(
                        f"[yellow]Monthly budget reached "
                        f"({used}/{self.budget} tweet reads) — stopping.[/yellow]"
                    )
                    break

            check_num += 1
            check_time = datetime.now(timezone.utc)
            self._log(
                f"[bold]Check #{check_num}[/bold]  "
                f"{check_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

            # -- Fetch ------------------------------------------------------
            raw_tweets: list[dict] = []
            try:
                raw_tweets = self.twitter.search_recent(
                    query=full_query,
                    max_results=topic.max_results,
                    since_id=since_id,
                )
            except Exception as e:  # noqa: BLE001
                self._log(f"[red]Search error: {e}[/red]")

            if raw_tweets:
                since_id = raw_tweets[0]["id"]
                self.storage.update_since_id(session_id, since_id)
                self.storage.add_quota_usage(len(raw_tweets))

            # -- Cross-session dedup ----------------------------------------
            new_tweets = self.storage.filter_unseen(topic.slug, raw_tweets)
            if len(new_tweets) < len(raw_tweets):
                self._log(
                    f"[dim]{len(raw_tweets) - len(new_tweets)} already-seen "
                    f"tweet(s) skipped[/dim]"
                )

            # -- Relevance filter -------------------------------------------
            display_tweets = new_tweets
            if topic.filter and new_tweets:
                self._log(f"[yellow]Filtering {len(new_tweets)} tweets with Claude…[/yellow]")
                try:
                    display_tweets = self.generator.filter_tweets(
                        topic.description, new_tweets
                    )
                    self._log(
                        f"[dim]{len(display_tweets)}/{len(new_tweets)} passed "
                        f"relevance filter[/dim]"
                    )
                except Exception as e:  # noqa: BLE001
                    self._log(f"[yellow]Relevance filter skipped: {e}[/yellow]")

            self.total_displayed += len(display_tweets)

            # -- Display & persist ------------------------------------------
            self._render(display_tweets)
            self.storage.record_check(
                session_id, check_num, check_time.isoformat(),
                len(raw_tweets), len(display_tweets), display_tweets,
            )

            # -- Notify -----------------------------------------------------
            if self.notifier and display_tweets:
                ok = self.notifier.notify(topic.label, display_tweets)
                self._log("[dim]Webhook sent[/dim]" if ok else "[yellow]Webhook failed[/yellow]")

            # -- Post-first-check refinement (single-topic interactive) -----
            if first_check and topic.refine and self.interactive and not once:
                new_query = self._refine_prompt(query, display_tweets or new_tweets)
                if new_query and new_query != query:
                    query = new_query
                    full_query = self._build_full_query(query)
                    since_id = None  # re-evaluate the window with the new query
                    self._log(f"[green]Refined query:[/green] [bold]{query}[/bold]")
                    self._log(f"[dim]Full Twitter query: {full_query}[/dim]")
            first_check = False

            # -- Continue? --------------------------------------------------
            if once:
                break

            elapsed = time.time() - started_epoch
            remaining = duration_secs - elapsed
            if remaining <= 0:
                self._log(f"[green]Monitoring window complete.[/green]")
                break

            sleep_secs = min(interval_secs, remaining)
            self._log(
                f"[dim]Next check in {fmt_duration(int(sleep_secs))} | "
                f"{fmt_duration(int(remaining))} remaining | "
                f"{self.total_displayed} tweets collected[/dim]"
            )
            # Interruptible sleep
            if self.stop_event.wait(timeout=sleep_secs):
                break

        # ---- Summary ------------------------------------------------------
        summary_text = None
        if self.summarizer:
            self._log("[yellow]Generating end-of-session summary…[/yellow]")
            try:
                tweets = self.storage.get_session_tweets(session_id)
                summary_text = self.summarizer.summarize(topic.description, tweets)
                self.console.print(Panel(
                    summary_text,
                    title=f"Summary — {topic.label}",
                    border_style="green",
                ))
                if self.notifier:
                    self.notifier.notify_text(f"*Summary — {topic.label}*\n{summary_text}")
            except Exception as e:  # noqa: BLE001
                self._log(f"[yellow]Summary failed: {e}[/yellow]")

        self.storage.finish_session(session_id, summary_text)
        self._log(
            f"[bold green]Done.[/bold green] {self.total_displayed} tweets across "
            f"{check_num} check(s). Session [cyan]{session_id}[/cyan]"
        )
        self.storage.close()

    # -- presentation -------------------------------------------------------

    def _render(self, tweets: list[dict]) -> None:
        if not tweets:
            self._log("[dim]No new tweets this check.[/dim]")
            return
        table = Table(show_header=True, header_style="bold cyan", expand=False)
        table.add_column("Author", style="green", no_wrap=True, max_width=20)
        table.add_column("Tweet", max_width=70)
        table.add_column("♥", justify="right", no_wrap=True, width=6)
        table.add_column("RT", justify="right", no_wrap=True, width=5)
        table.add_column("Posted (UTC)", no_wrap=True, width=17)
        for t in tweets[:25]:
            text = t["text"]
            if len(text) > 140:
                text = text[:137] + "…"
            created = (t.get("created_at") or "")[:16].replace("T", " ")
            table.add_row(
                f"@{t['author']}", text,
                str(t.get("likes", 0)), str(t.get("retweets", 0)), created,
            )
        self.console.print(table)
        if len(tweets) > 25:
            self._log(f"[dim]…{len(tweets) - 25} more saved to the database[/dim]")

    def _refine_prompt(self, query: str, sample: list[dict]) -> str | None:
        """Ask the user whether the first results are on-target; refine if not."""
        self.console.print(
            "\n[bold]Are these results on-target?[/bold] "
            "Press [green]Enter[/green] to keep the query, or type feedback "
            "(e.g. 'too much sports, only company news') to refine it:"
        )
        try:
            feedback = input("> ").strip()
        except EOFError:
            return None
        if not feedback:
            return None
        try:
            new_query, explanation = self.generator.refine_query(
                self.topic.description, query, sample, feedback
            )
            self._log(f"[dim]{explanation}[/dim]")
            return new_query
        except Exception as e:  # noqa: BLE001
            self._log(f"[yellow]Refinement failed: {e}[/yellow]")
            return None


def _iso_to_epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return time.time()
