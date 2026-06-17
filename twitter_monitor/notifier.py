"""
Webhook notifications for Slack and Discord.

Detects the platform from the webhook URL and formats the payload accordingly.
Uses only the standard library so no extra dependency is required.
"""

import json
import urllib.error
import urllib.request


def _platform(url: str) -> str:
    if "hooks.slack.com" in url:
        return "slack"
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        return "discord"
    return "generic"


def _format_lines(label: str, tweets: list[dict], limit: int = 10) -> str:
    lines = [f"*{label}* — {len(tweets)} new tweet(s)"]
    for t in tweets[:limit]:
        text = t.get("text", "").replace("\n", " ")
        if len(text) > 200:
            text = text[:197] + "…"
        lines.append(f"• @{t.get('author', '?')} ({t.get('likes', 0)}♥): {text}\n{t.get('url', '')}")
    if len(tweets) > limit:
        lines.append(f"…and {len(tweets) - limit} more")
    return "\n".join(lines)


class Notifier:
    def __init__(self, webhook_url: str):
        self.url = webhook_url
        self.platform = _platform(webhook_url)

    def notify(self, label: str, tweets: list[dict]) -> bool:
        """Send a notification about new tweets. Returns True on success."""
        if not tweets:
            return False

        body = _format_lines(label, tweets)
        if self.platform == "slack":
            payload = {"text": body}
        elif self.platform == "discord":
            # Discord caps message content at 2000 characters
            payload = {"content": body[:1990]}
        else:
            payload = {"text": body, "tweets": tweets[:25]}

        return self._post(payload)

    def notify_text(self, message: str) -> bool:
        """Send an arbitrary text message (used for end-of-session summaries)."""
        if self.platform == "discord":
            payload = {"content": message[:1990]}
        else:
            payload = {"text": message}
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300
        except urllib.error.URLError:
            return False
