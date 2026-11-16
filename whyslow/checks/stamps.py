"""
Stamps (Anchors and Wired Stamps), split by which build made them.

- Original: vanilla Stamps, or the custom build before its Phase 4. Every
  node keeps knobChanged on all the time, and Wired Stamps have a Python
  autolabel. Wired Stamps are numerous, so this adds up.
- Custom ("Juicy Stamps", the user-facing name): Harry's build (identified by the `panel_hook` knob). knobChanged
  is only armed while the panel is open, and Wired Stamps have no autolabel.
  Anchors keep theirs by design.

Severity counts only Stamps in the main Node Graph; ones inside groups are
listed but not counted, matching the other node checks.
"""

from __future__ import annotations

import collections
import re

from whyslow import config
from whyslow.checks._helpers import plural
from whyslow.core.check import CATEGORY_SCRIPT, Check, register
from whyslow.core.finding import FindingItem, Severity

_VERSION_RE = re.compile(r"Stamps\s+(.+?)</big>")


def _code(node, knob_name: str) -> str:
    knob = node.knob(knob_name)
    value = knob.value() if knob is not None else ""
    return value if value and value.strip() else ""


def _version(node) -> str:
    """The version shown on the node's version knob, e.g. "v1.2"."""
    knob = node.knob("version")
    match = _VERSION_RE.search(knob.value() or "") if knob is not None else None
    return match.group(1) if match else "unknown version"


class _StampInfo:
    """What the checks need to know about one Stamp node."""

    def __init__(self, node, ctx):
        self.node = node
        self.path = node.fullName()
        self.is_anchor = node[config.STAMP_IDENTIFIER_KNOB].value() == "anchor"
        self.kind = "Anchor" if self.is_anchor else "Wired Stamp"
        self.is_custom = node.knob(config.STAMP_CUSTOM_KNOB) is not None
        self.top_level = ctx.is_top_level(node)
        self.version = _version(node)
        self.knob_changed = _code(node, "knobChanged")
        self.autolabel = _code(node, "autolabel")

    def item(self) -> FindingItem:
        flags = [self.version]
        if self.knob_changed:
            flags.append("knobChanged set")
        if self.autolabel:
            flags.append("autolabel")
        if not self.top_level:
            flags.append("inside a group")
        code = []
        if self.knob_changed:
            code += ["# knobChanged", self.knob_changed, ""]
        if self.autolabel:
            code += ["# autolabel", self.autolabel]
        return FindingItem(label=self.path, node_path=self.path,
                           kind=f"{self.kind} ({self.node.Class()})",
                           detail=", ".join(flags), code="\n".join(code))


def _stamps(ctx, custom: bool) -> list[_StampInfo]:
    """Stamps made by one build. Both checks share one pass over the nodes."""
    def collect():
        found = [_StampInfo(node, ctx) for node in ctx.all_nodes if ctx.is_stamp(node)]
        return sorted(found, key=lambda s: (not s.top_level, not s.is_anchor, s.path))
    return [s for s in ctx.shared("stamps", collect) if s.is_custom == custom]


def _counts(stamps: list[_StampInfo]) -> str:
    anchors = sum(1 for s in stamps if s.is_anchor)
    return (f"{plural(anchors, 'Anchor')} and "
            f"{plural(len(stamps) - anchors, 'Wired Stamp')}")


def _group_note(stamps: list[_StampInfo]) -> str:
    buried = sum(1 for s in stamps if not s.top_level)
    if not buried:
        return ""
    return (f" {plural(buried, 'Stamp')} inside groups "
            f"{'is' if buried == 1 else 'are'} listed but not counted toward the severity.")


@register
class OriginalStamps(Check):
    id = "stamps_original"
    title = "Stamps"
    category = CATEGORY_SCRIPT
    description = "Looks for Stamps made by vanilla Stamps (or older Juicy Stamps builds), which keep knobChanged on all the time."
    order = 25
    # # Any original Stamps are at least Low, even if all are inside groups (count 0).
    severity_steps = ((0, Severity.LOW), (11, Severity.MEDIUM), (31, Severity.HIGH))

    def run(self, ctx):
        stamps = _stamps(ctx, custom=False)
        if not stamps:
            return None
        top_level = [s for s in stamps if s.top_level]
        with_kc = sum(1 for s in stamps if s.knob_changed)
        with_al = sum(1 for s in stamps if s.autolabel)
        versions = collections.Counter(s.version for s in stamps).most_common()
        return self.finding(
            severity=self.severity_for(len(top_level)),
            metric=plural(len(stamps), "Stamp"),
            summary=(f"{_counts(stamps)} from the original Stamps keep knobChanged on all the "
                     "time, and Wired Stamps also build their label with Python on every redraw."),
            whats_happening=(
                "These Stamps have no panel_hook knob, so they were made by vanilla Stamps or by "
                "Juicy Stamps before its on-demand knobChanged. "
                f"Versions: {', '.join(f'{v} x{n}' for v, n in versions)}. "
                f"{with_kc} of them have knobChanged set and {with_al} have a Python autolabel."
                + _group_note(stamps)),
            why_slow=("Each one runs Stamps' knobChanged Python on every knob change on it, "
                      "including selecting, moving and connecting, and each autolabel runs every "
                      "time the Node Graph draws the node. Scripts often have dozens of Wired "
                      "Stamps, so the cost adds up quickly."),
            how_to_fix=("Use Juicy Stamps: its Stamps only arm knobChanged while their "
                        "panel is open, and its Wired Stamps are named after their title instead "
                        "of using an autolabel. Stamps made before it keep the old behaviour until "
                        "they're recreated (an updater for them is planned in Stamps, but not "
                        "written yet)."),
            items=[s.item() for s in stamps],
        )


@register
class CustomStamps(Check):
    id = "stamps_custom"
    title = "Juicy Stamps"
    category = CATEGORY_SCRIPT
    description = "Looks for Stamps made by Juicy Stamps (cheap: knobChanged only while the panel is open)."
    order = 26
    severity_steps = ((1, Severity.LOW),)

    def run(self, ctx):
        stamps = _stamps(ctx, custom=True)
        if not stamps:
            return None
        anchors_al = sum(1 for s in stamps if s.is_anchor and s.autolabel)
        wired_al = sum(1 for s in stamps if not s.is_anchor and s.autolabel)
        armed = sum(1 for s in stamps if s.knob_changed)

        notes = [f"{_counts(stamps)} made by Juicy Stamps."]
        if anchors_al:
            notes.append(
                f"{plural(anchors_al, 'Anchor')} {'has' if anchors_al == 1 else 'have'} a Python "
                "autolabel. This is by design: Anchor names must stay unique for reconnecting, so "
                "they can't be named after their title. It reads one knob, only for visible "
                "Anchors, and there's one Anchor per stream.")
        if armed:
            notes.append(
                f"{plural(armed, 'Stamp')} currently {'has' if armed == 1 else 'have'} knobChanged "
                "set. That's expected right after a script is opened or pasted (onCreate re-arms "
                "it, for compatibility with vanilla) or while a Stamp's panel is open. Each clears "
                "it the first time it's touched with its panel closed.")
        if wired_al:
            notes.append(
                f"{plural(wired_al, 'Wired Stamp')} still {'has' if wired_al == 1 else 'have'} an "
                "autolabel, so they were made before Juicy Stamps stopped adding them.")

        return self.finding(
            severity=self.severity_for(len(stamps)),
            metric=plural(len(stamps), "Stamp"),
            summary=(f"{_counts(stamps)} from Juicy Stamps. They only run knobChanged "
                     "while their panel is open, so they're cheap. Listed for completeness."),
            whats_happening="\n\n".join(notes) + _group_note(stamps),
            why_slow=("Very little. With their panels closed, Juicy Stamps run no knobChanged "
                      "code, and only Anchors keep an autolabel."),
            how_to_fix="Nothing needed.",
            items=[s.item() for s in stamps],
        )
