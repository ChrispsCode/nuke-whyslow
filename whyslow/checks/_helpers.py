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


def ms(value: float) -> str:
    """"0.04 ms" for small costs, "11.1 ms" for bigger ones, "2.7 s" from a second up."""
    if value >= 1000:
        return f"{value / 1000:.1f} s"
    return f"{value:.2f} ms" if value < 1 else f"{value:.1f} ms"


def line_count(code: str) -> str:
    return plural(len(code.strip().splitlines()), "line")


def print_hook_severity(check, cost_ms: float, echo: bool):
    """Severity for Python that runs on every Script Editor print.

    Any listener is at least Medium (the caller only asks when there is one).
    With echo on, ordinary UI actions print too (at least two changes each),
    so the cost counts double and the floor is High. Thresholds are the
    check's severity_steps, in ms per change.
    """
    from whyslow.core.finding import Severity
    effective = cost_ms * (2 if echo else 1)
    severity = max(Severity.MEDIUM, check.severity_for(int(effective)))
    if echo:
        severity = max(severity, Severity.HIGH)
    return severity
