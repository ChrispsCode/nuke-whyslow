"""Small shared helpers for checks (not itself a check module)."""

from __future__ import annotations

import collections
import inspect


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


def wait_text(entry) -> str:
    """"; waited 2.3 s on socket.socket.recv x3 (2.3 s)" for an entry that blocked, else ""."""
    if not entry.waits:
        return ""
    return f"; waited {ms(entry.wait_seconds * 1000)}: {entry.wait_summary()}"


def wait_lines(entry) -> str:
    """Every blocking call the entry made, for the code view."""
    if not entry.waits:
        return ""
    lines = ["# Waited on disk / network / other processes:"]
    for label, (calls, seconds) in sorted(entry.waits.items(), key=lambda item: -item[1][1]):
        lines.append(f"#   {label:32} x{calls:<5} {ms(seconds * 1000)}")
    return "\n".join(lines) + "\n\n"


def source_of(entry) -> str:
    """The entry function's source, read from its file (best effort)."""
    from whyslow import config
    try:
        with open(entry.filename) as handle:
            lines = handle.readlines()
        return "".join(inspect.getblock(lines[entry.line - 1:]))[: config.MAX_CODE_CHARS]
    except Exception:
        return "# Source not available."


def entry_item(entry, detail: str):
    """A details row for a probe entry point, with what it waited on."""
    from whyslow import config
    from whyslow.core.finding import FindingItem
    dedicated = entry.tool in config.DEDICATED_TOOLS
    return FindingItem(
        label=entry.label, kind=entry.tool + (" (has its own card)" if dedicated else ""),
        detail=detail + wait_text(entry),
        code=f"# {entry.location}\n# tool: {entry.tool}\n\n{wait_lines(entry)}{source_of(entry)}",
    )


def waits_note(entries) -> str:
    """" 2.1 s of it was spent waiting on the disk or network (socket.socket.recv x3 ...)." or ""."""
    waited = [e for e in entries if e.waits]
    if not waited:
        return ""
    total = sum(e.wait_seconds for e in waited) * 1000
    worst = max(waited, key=lambda e: e.wait_seconds)
    return (f" {ms(total)} of it was spent waiting on the disk, network or other processes "
            f"(worst: {worst.label}, {worst.wait_summary(top=2)}).")
