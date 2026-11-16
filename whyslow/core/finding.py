"""
What a check reports. Plain dataclasses holding only strings and numbers, so
a report can be serialised (e.g. sent back from a headless Nuke that scanned
a script on disk) and rendered as text or in the GUI alike.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field


class Severity(enum.IntEnum):
    """Ordered, so the report can sort and compare them."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()

    @classmethod
    def coerce(cls, value: Severity | str | int) -> Severity:
        """Accept a Severity, its name ("high") or its number, for config."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls[value.strip().upper()]
        return cls(int(value))


@dataclass
class FindingItem:
    """One row in the details table: a node, a registered callback, a setting."""

    label: str
    # # Full node name ("Group1.Grade3") when the row is a node, so the GUI can
    # # select it. Empty for rows that aren't nodes.
    node_path: str = ""
    kind: str = ""
    detail: str = ""
    # # Shown in the details code view when the row is selected.
    code: str = ""


@dataclass
class Finding:
    """One check's result when it found something. One card in the report."""

    check_id: str
    title: str
    severity: Severity
    # # 1-2 plain sentences for the card.
    summary: str
    # # Short count for the card's corner, e.g. "214 nodes".
    metric: str = ""
    # # The three details sections.
    whats_happening: str = ""
    why_slow: str = ""
    how_to_fix: str = ""
    items: list[FindingItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["severity"] = self.severity.name
        return data
