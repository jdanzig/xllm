"""
Topic configuration for the monitoring service.

A `Topic` describes one thing to watch. Topics can be created from CLI
arguments (single-topic mode) or loaded from a YAML/JSON file (multi-topic
mode), in which case each topic may override the global defaults.
"""

import json
import re
from dataclasses import dataclass, field, asdict


@dataclass
class Topic:
    description: str
    name: str | None = None
    query: str | None = None          # explicit Twitter query override
    interval: str = "30m"
    duration: str = "2h"
    once: bool = False
    max_results: int = 100
    filter: bool = False
    lang: str = "en"
    min_likes: int = 0
    webhook: str | None = None
    summary: bool = False
    refine: bool = False

    @property
    def slug(self) -> str:
        """A stable, filesystem/db-friendly identifier for this topic."""
        base = self.name or self.description
        slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
        return slug[:60] or "topic"

    @property
    def label(self) -> str:
        return self.name or self.description

    def to_dict(self) -> dict:
        return asdict(self)


# Keys a per-topic block in a config file may set.
_TOPIC_KEYS = {
    "description", "name", "query", "interval", "duration", "once",
    "max_results", "filter", "lang", "min_likes", "webhook", "summary", "refine",
}


def load_topics(path: str) -> list[Topic]:
    """
    Load topics from a YAML or JSON config file.

    Structure:
        defaults:            # optional, applied to every topic
          interval: 20m
          duration: 6h
          lang: en
        topics:
          - description: "posts about our product launch"
            name: launch
            min_likes: 10
          - description: "competitor mentions"
            webhook: https://hooks.slack.com/...
    """
    with open(path) as fh:
        raw = fh.read()

    data = _parse(raw, path)

    if not isinstance(data, dict) or "topics" not in data:
        raise ValueError("Config file must contain a top-level 'topics' list")

    defaults = data.get("defaults", {}) or {}
    topics: list[Topic] = []
    for entry in data["topics"]:
        if isinstance(entry, str):
            entry = {"description": entry}
        merged = {**defaults, **entry}
        unknown = set(merged) - _TOPIC_KEYS
        if unknown:
            raise ValueError(f"Unknown config key(s): {', '.join(sorted(unknown))}")
        if "description" not in merged:
            raise ValueError("Each topic must have a 'description'")
        topics.append(Topic(**merged))

    if not topics:
        raise ValueError("Config file contains no topics")
    return topics


def _parse(raw: str, path: str):
    """Parse YAML if PyYAML is available, otherwise fall back to JSON."""
    if path.endswith((".json",)):
        return json.loads(raw)
    try:
        import yaml  # type: ignore
        return yaml.safe_load(raw)
    except ImportError:
        # PyYAML not installed — try JSON as a fallback so .yml still works
        # for the JSON-compatible subset.
        return json.loads(raw)
