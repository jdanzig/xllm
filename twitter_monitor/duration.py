"""Helpers for parsing and formatting human-readable durations."""


def parse_duration(s: str) -> int:
    """Parse a human duration string (e.g. '30m', '2h', '1d') into seconds."""
    s = str(s).strip().lower()
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
