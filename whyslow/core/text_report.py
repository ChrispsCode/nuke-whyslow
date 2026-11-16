"""Plain-text rendering of a Report, for the Script Editor and "Copy report"."""

from __future__ import annotations

import textwrap

from whyslow import config
from whyslow.core.finding import Finding
from whyslow.core.runner import STATUS_ERROR, STATUS_PASSED, STATUS_SKIPPED, Report

RULE = "-" * 72


def _wrap(text: str, indent: str = "  ") -> str:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return "\n\n".join(
        textwrap.fill(p, config.TEXT_WRAP, initial_indent=indent, subsequent_indent=indent)
        for p in paragraphs
    )


def format_finding(finding: Finding, max_items: int | None = 20) -> str:
    """One finding in full, as used by the details dialog's Copy button."""
    lines = [f"[{finding.severity.label.upper()}] {finding.title}"
             + (f"  ({finding.metric})" if finding.metric else "")]
    lines.append(_wrap(finding.summary))
    for heading, text in (("What's happening", finding.whats_happening),
                          ("Why it's slow", finding.why_slow),
                          ("How to fix", finding.how_to_fix)):
        if text:
            lines += ["", f"  {heading}:", _wrap(text, "    ")]
    if finding.items:
        shown = finding.items if max_items is None else finding.items[:max_items]
        lines += ["", "  Items:"]
        for item in shown:
            parts = [p for p in (item.kind, item.detail) if p]
            lines.append(f"    - {item.label}" + (f"  [{', '.join(parts)}]" if parts else ""))
        if len(shown) < len(finding.items):
            lines.append(f"    ... and {len(finding.items) - len(shown)} more")
    return "\n".join(lines)


def format_report(report: Report) -> str:
    issues = report.issues
    lines = [
        RULE,
        f"Why Slow? report: {report.script_name}",
        f"Nuke {report.nuke_version} | {report.node_count} nodes | "
        f"{'deep' if report.deep else 'quick'} scan in {report.duration:.2f}s"
        + (" | CANCELLED" if report.cancelled else ""),
        f"{len(issues)} issue(s) found",
        RULE,
    ]
    for result in issues:
        lines += [format_finding(result.finding), ""]

    errors = report.with_status(STATUS_ERROR)
    if errors:
        lines += [RULE, "Checks that failed to run:"]
        for result in errors:
            last_line = result.error.strip().splitlines()[-1] if result.error else ""
            lines.append(f"  ! {result.title}: {last_line}")

    passed = report.with_status(STATUS_PASSED)
    if passed:
        lines += [RULE, "Passed:"]
        lines += [f"  OK {r.title}" for r in passed]

    skipped = report.with_status(STATUS_SKIPPED)
    if skipped:
        lines += ["Skipped:"]
        lines += [f"  -- {r.title}" + (f" ({r.note})" if r.note else "") for r in skipped]

    lines.append(RULE)
    return "\n".join(lines)
