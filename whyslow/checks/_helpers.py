"""Small shared helpers for checks (not itself a check module)."""

from __future__ import annotations

import collections


def plural(count: int, word: str, plural_word: str | None = None) -> str:
    """"1 node", "3 nodes"."""
    return f"{count} {word if count == 1 else (plural_word or word + 's')}"


def class_breakdown(classes: list[str], limit: int = 6) -> str:
    """"Grade x12, NoOp x3, Blur x1 and 2 more classes"."""
    counts = collections.Counter(classes).most_common()
    text = ", ".join(f"{cls} x{n}" for cls, n in counts[:limit])
    if len(counts) > limit:
        text += f" and {plural(len(counts) - limit, 'more class', 'more classes')}"
    return text


def line_count(code: str) -> str:
    return plural(len(code.strip().splitlines()), "line")
