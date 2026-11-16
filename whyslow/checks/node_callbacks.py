"""
Python stored on nodes: the knobChanged and autolabel knobs.

Top-level nodes are the real cost, since they're the ones touched in normal
comping. Callbacks buried inside groups (a deliberate trick to keep them
quiet) get a separate low-severity check. Stamps are left out of all of
these: they get their own section.
"""

from __future__ import annotations

from whyslow.checks._helpers import class_breakdown, line_count, plural
from whyslow.core.check import CATEGORY_SCRIPT, Check, register
from whyslow.core.finding import FindingItem, Severity


def _nodes_with_code(ctx, knob_name: str, top_level: bool) -> list[tuple[object, str]]:
    """(node, code) for every non-Stamp node whose `knob_name` knob holds code."""
    found = []
    for node in ctx.all_nodes:
        if ctx.is_top_level(node) != top_level:
            continue
        knob = node.knob(knob_name)
        if knob is None:
            continue
        code = knob.value()
        if not code or not code.strip() or ctx.is_stamp(node):
            continue
        found.append((node, code))
    return sorted(found, key=lambda pair: pair[0].fullName())


def _item(node, code: str, kind: str) -> FindingItem:
    return FindingItem(label=node.fullName(), node_path=node.fullName(), kind=node.Class(),
                       detail=f"{kind}, {line_count(code)}", code=code)


@register
class TopLevelKnobChanged(Check):
    id = "node_knobchanged"
    title = "Nodes with knobChanged callbacks"
    category = CATEGORY_SCRIPT
    description = "Looks for nodes in the main Node Graph whose knobChanged knob holds Python."
    order = 10
    severity_steps = ((1, Severity.MEDIUM), (3, Severity.HIGH))

    def run(self, ctx):
        hits = _nodes_with_code(ctx, "knobChanged", top_level=True)
        if not hits:
            return None
        count = len(hits)
        return self.finding(
            severity=self.severity_for(count),
            metric=plural(count, "node"),
            summary=(f"{plural(count, 'node')} in the main Node Graph "
                     f"{'runs' if count == 1 else 'run'} Python every time one of "
                     f"{'its' if count == 1 else 'their'} knobs changes, "
                     "including when selected, moved or connected."),
            whats_happening=(f"These nodes have code in their knobChanged knob. "
                             f"By class: {class_breakdown([n.Class() for n, _ in hits])}."),
            why_slow=("Nuke runs a node's knobChanged code for every knob change on that node, "
                      "not only the ones the code cares about. Selecting, dragging and "
                      "connecting nodes all change knobs, so ordinary work in the Node Graph "
                      "keeps calling into Python. Selecting or moving many of these nodes at "
                      "once runs the code once per node."),
            how_to_fix=("Remove the knobChanged code if it's no longer needed. If it is needed, "
                        "arm it only while the node's properties panel is open (set it from the "
                        "panel, clear it on hidePanel), or move the node inside a Group so the "
                        "callback only runs when working inside that group."),
            items=[_item(node, code, "knobChanged") for node, code in hits],
        )


@register
class TopLevelAutolabel(Check):
    id = "node_autolabel"
    title = "Nodes with Python autolabels"
    category = CATEGORY_SCRIPT
    description = "Looks for nodes in the main Node Graph whose autolabel knob holds Python."
    order = 20
    severity_steps = ((1, Severity.MEDIUM), (3, Severity.HIGH))

    def run(self, ctx):
        hits = _nodes_with_code(ctx, "autolabel", top_level=True)
        if not hits:
            return None
        count = len(hits)
        return self.finding(
            severity=self.severity_for(count),
            metric=plural(count, "node"),
            summary=(f"{plural(count, 'node')} in the main Node Graph "
                     f"{'runs' if count == 1 else 'run'} Python to build "
                     f"{'its' if count == 1 else 'their'} label every time the "
                     "Node Graph redraws."),
            whats_happening=(f"These nodes have code in their autolabel knob. "
                             f"By class: {class_breakdown([n.Class() for n, _ in hits])}."),
            why_slow=("A node's autolabel is evaluated whenever the Node Graph draws that node: "
                      "panning, zooming, selecting and any change to the graph. The cost "
                      "repeats on every redraw, for every one of these nodes."),
            how_to_fix=("Replace the autolabel with a plain label, or name the node so Nuke's "
                        "default label already shows what the autolabel was adding."),
            items=[_item(node, code, "autolabel") for node, code in hits],
        )


@register
class BuriedNodeCallbacks(Check):
    id = "buried_node_callbacks"
    title = "Callbacks inside groups"
    category = CATEGORY_SCRIPT
    description = "Looks for knobChanged and autolabel code on nodes inside groups and gizmos."
    order = 30
    severity_steps = ((1, Severity.LOW),)

    def run(self, ctx):
        knob_changed = _nodes_with_code(ctx, "knobChanged", top_level=False)
        autolabel = _nodes_with_code(ctx, "autolabel", top_level=False)
        if not knob_changed and not autolabel:
            return None
        nodes = {n.fullName() for n, _ in knob_changed + autolabel}
        parts = []
        if knob_changed:
            parts.append(f"{plural(len(knob_changed), 'knobChanged', 'knobChanged')}")
        if autolabel:
            parts.append(f"{plural(len(autolabel), 'autolabel', 'autolabel')}")
        return self.finding(
            severity=self.severity_for(len(nodes)),
            metric=plural(len(nodes), "node"),
            summary=(f"{plural(len(nodes), 'node')} inside groups "
                     f"{'has' if len(nodes) == 1 else 'have'} Python callbacks "
                     f"({' and '.join(parts)}). Usually fine; listed so you know they're there."),
            whats_happening=("These nodes live inside a Group or gizmo and carry knobChanged or "
                             "autolabel code. Burying callbacks inside a group is a common way "
                             "to keep them from running during normal comping."),
            why_slow=("They only run when the node itself changes or is drawn, which mostly "
                      "happens while working inside the group, or when a linked knob on the "
                      "group changes an inside knob. They only matter if those groups are "
                      "edited a lot or are very common in the script."),
            how_to_fix="Nothing, unless one of these groups is itself slow to work with.",
            items=([_item(n, c, "knobChanged") for n, c in knob_changed]
                   + [_item(n, c, "autolabel") for n, c in autolabel]),
        )
