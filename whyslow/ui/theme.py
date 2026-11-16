"""Colours and small text helpers shared by the dialogs."""

from __future__ import annotations

import html
import textwrap

from whyslow import config
from whyslow.core.finding import Severity

SEVERITY_COLORS = {
    Severity.INFO: "#5f8fbf",
    Severity.LOW: "#7f9f5f",
    Severity.MEDIUM: "#d9a43b",
    Severity.HIGH: "#e0663a",
    Severity.CRITICAL: "#d93a3a",
}
ERROR_COLOR = "#8a8a8a"
PASSED_COLOR = "#7f9f5f"
MUTED_TEXT = "#9a9a9a"
CARD_BACKGROUND = "rgba(255, 255, 255, 12)"
CARD_BORDER = "#555555"
# # Characters per line in card summaries (the report window is 700px minimum).
CARD_WRAP = 90
# # The report window opens this much taller than its content needs (Harry's
# # GUI pass: the fitted size felt cramped).
SPAWN_HEIGHT_SCALE = 1.5


def wrapped_html(text: str, width: int | None = None) -> str:
    """Escape and hard-wrap text for a non-word-wrapping rich-text QLabel.

    Word-wrapped QLabels clip inside Nuke, so lines are broken by hand.
    Blank lines in the source become paragraph gaps.
    """
    width = width or config.TEXT_WRAP
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "<br><br>".join(
        "<br>".join(html.escape(line) for line in textwrap.wrap(p, width))
        for p in paragraphs
    )


def badge_style(color: str) -> str:
    return (f"background-color: {color}; color: #111; font-weight: bold; "
            "border-radius: 3px; padding: 1px 6px;")
