"""
Python stored on nodes: the knobChanged and autolabel knobs.

Top-level nodes are the real cost, since they're the ones touched in normal
comping. Callbacks buried inside groups (a deliberate trick to keep them
quiet) get a separate low-severity check. Stamps are left out of all of
these: they get their own section.

Top-level knobChanged nodes belonging to a tool listed in
config.EXTRA_NODE_KNOB_CHANGED go on that tool's own card (one generated
check per entry), so one tool's nodes can't flood the main card.
"""

from __future__ import annotations

import re

from whyslow import config
from whyslow.checks._helpers import class_breakdown, line_count, plural
from whyslow.core.check import CATEGORY_SCRIPT, Check, register, unregister
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


# # #----------------------------------------------------------------------------
# # knobChanged on top-level nodes: the main card, plus one card per tool
# # #----------------------------------------------------------------------------

KNOB_CHANGED_STEPS = ((1, Severity.MEDIUM), (3, Severity.HIGH))
KNOB_CHANGED_WHY = (
    "Nuke runs a node's knobChanged code for every knob change on that node, not only the "
    "ones the code cares about. Selecting, dragging and connecting nodes all change knobs, so "
    "ordinary work in the Node Graph keeps calling into Python. Selecting or moving many of "
    "these nodes at once runs the code once per node.")
KNOB_CHANGED_HOW = (
    "Remove the knobChanged code if it's no longer needed. If it is needed, arm it only while "
    "the node's properties panel is open (set it from the panel, clear it on hidePanel), or "
    "move the node inside a Group so the callback only runs when working inside that group.")


def _knob_changed_summary(count: int) -> str:
    return (f"{plural(count, 'node')} in the main Node Graph "
            f"{'runs' if count == 1 else 'run'} Python every time one of "
            f"{'its' if count == 1 else 'their'} knobs changes, "
            "including when selected, moved or connected.")


def _knob_changed_what(hits) -> str:
    return (f"These nodes have code in their knobChanged knob. "
            f"By class: {class_breakdown([n.Class() for n, _ in hits])}.")


def _matcher(entry):
    """A function (node, code) -> bool for one EXTRA_NODE_KNOB_CHANGED entry.

    Raises ValueError for an entry that can't match anything, so its card
    shows the config mistake instead of silently matching nothing.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"EXTRA_NODE_KNOB_CHANGED entry {entry!r} isn't a dict.")
    ident = entry.get("id")
    if isinstance(ident, dict):
        knob_name = ident.get("id_knob")
        if not knob_name:
            raise ValueError(f"EXTRA_NODE_KNOB_CHANGED '{entry.get('name')}': 'id' needs an 'id_knob'.")
        expected = ident.get("id_value")

        def match_id(node, code):
            knob = node.knob(knob_name)
            if knob is None:
                return False
            if expected is None:
                return True
            try:
                value = knob.value()
            except Exception:
                return False
            return value == expected or str(value) == str(expected)
        return match_id

    callbacks = entry.get("callback")
    if isinstance(callbacks, str):
        callbacks = [callbacks]
    wanted = {c.strip() for c in (callbacks or []) if isinstance(c, str) and c.strip()}
    if not wanted:
        raise ValueError(f"EXTRA_NODE_KNOB_CHANGED '{entry.get('name')}' needs a 'callback' "
                         "string (or list) or an 'id' dict.")
    return lambda node, code: code.strip() in wanted


def _split_knob_changed(ctx):
    """Top-level knobChanged nodes split between the tool entries and the main card.

    Returns (claimed {entry index: [(node, code)]}, unclaimed [(node, code)],
    errors {entry index: message}). The first matching entry wins. Shared by
    all the knobChanged cards, so it runs once per scan.
    """
    def build():
        matchers, errors = {}, {}
        for index, entry in enumerate(config.EXTRA_NODE_KNOB_CHANGED):
            try:
                matchers[index] = _matcher(entry)
            except ValueError as error:
                errors[index] = str(error)
        claimed = {index: [] for index in matchers}
        unclaimed = []
        for node, code in _nodes_with_code(ctx, "knobChanged", top_level=True):
            for index, match in matchers.items():
                if match(node, code):
                    claimed[index].append((node, code))
                    break
            else:
                unclaimed.append((node, code))
        return claimed, unclaimed, errors
    return ctx.shared("knob_changed_split", build)


@register
class TopLevelKnobChanged(Check):
    id = "node_knobchanged"
    title = "Nodes with knobChanged callbacks"
    category = CATEGORY_SCRIPT
    description = "Looks for nodes in the main Node Graph whose knobChanged knob holds Python."
    order = 10
    severity_steps = KNOB_CHANGED_STEPS

    def run(self, ctx):
        claimed, hits, _ = _split_knob_changed(ctx)
        if not hits:
            return None
        count = len(hits)
        elsewhere = [f"{_entry_name(index)} ({len(nodes)})" for index, nodes in claimed.items() if nodes]
        return self.finding(
            severity=self.severity_for(count),
            metric=plural(count, "node"),
            summary=_knob_changed_summary(count),
            whats_happening=_knob_changed_what(hits)
            + (f" Nodes from {', '.join(elsewhere)} are on their own cards." if elsewhere else ""),
            why_slow=KNOB_CHANGED_WHY,
            how_to_fix=KNOB_CHANGED_HOW,
            items=[_item(node, code, "knobChanged") for node, code in hits],
        )


def _entry_name(index: int) -> str:
    entry = config.EXTRA_NODE_KNOB_CHANGED[index]
    name = entry.get("name") if isinstance(entry, dict) else None
    return str(name or f"Tool {index + 1}")


class _ToolKnobChanged(Check):
    """Base for the generated per-tool knobChanged checks (see register_extra_checks)."""

    category = CATEGORY_SCRIPT
    entry_index = 0
    entry: dict = {}

    def run(self, ctx):
        claimed, _, errors = _split_knob_changed(ctx)
        if self.entry_index in errors:
            raise ValueError(errors[self.entry_index])
        hits = claimed.get(self.entry_index) or []
        if not hits:
            return None
        count = len(hits)
        text = self.entry if isinstance(self.entry, dict) else {}
        return self.finding(
            severity=self.severity_for(count),
            metric=plural(count, "node"),
            summary=text.get("summary") or _knob_changed_summary(count),
            whats_happening=text.get("what") or _knob_changed_what(hits),
            why_slow=text.get("why") or KNOB_CHANGED_WHY,
            how_to_fix=text.get("how") or KNOB_CHANGED_HOW,
            items=[_item(node, code, "knobChanged") for node, code in hits],
        )


_GENERATED: list[str] = []


def register_extra_checks() -> None:
    """(Re)build one check per config.EXTRA_NODE_KNOB_CHANGED entry.

    Runs at import. Call it again after changing the config at runtime.
    """
    while _GENERATED:
        unregister(_GENERATED.pop())
    for index, entry in enumerate(config.EXTRA_NODE_KNOB_CHANGED):
        name = _entry_name(index)
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or str(index + 1)
        severity = entry.get("severity") if isinstance(entry, dict) else None
        if not severity:
            steps = KNOB_CHANGED_STEPS
        elif isinstance(severity, (tuple, list)):
            steps = tuple(severity)
        else:
            steps = ((1, severity),)
        cls = type(f"KnobChanged_{slug}", (_ToolKnobChanged,), {
            "id": f"node_knobchanged_{slug}",
            "title": f"{name} nodes with knobChanged callbacks",
            "description": f"Looks for {name} nodes in the main Node Graph with knobChanged code "
                           "(config.EXTRA_NODE_KNOB_CHANGED).",
            "order": 11 + index * 0.01,
            "severity_steps": steps,
            "entry_index": index,
            "entry": entry,
        })
        register(cls)
        _GENERATED.append(cls.id)


register_extra_checks()


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
