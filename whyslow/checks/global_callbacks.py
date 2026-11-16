"""
Callbacks registered globally by tools (nuke.addKnobChanged, addAutolabel,
addUpdateUI). Nuke's own entries are filtered out by the context (anything
whose source lives inside the Nuke install, plus config.IGNORED_CALLBACKS).

Severity follows reach: a callback registered for "*" runs for every node,
one for a class only matters if the script has nodes of that class.
"""

from __future__ import annotations

from whyslow.checks._helpers import plural
from whyslow.core.check import CATEGORY_SESSION, NEEDS_SESSION, Check
from whyslow.core.check import register
from whyslow.core.finding import FindingItem, Severity


class _GlobalCallbackCheck(Check):
    """Shared logic; subclasses set the registry and the wording."""

    category = CATEGORY_SESSION
    needs = frozenset({NEEDS_SESSION})
    registry = ""
    add_function = ""
    runs_when = ""
    why_slow = ""

    def severity_for_entry(self, entry, nodes_affected: int) -> Severity:
        if entry.node_class == "*":
            return Severity.HIGH
        return Severity.MEDIUM if nodes_affected else Severity.LOW

    def run(self, ctx):
        entries = [e for e in ctx.callbacks(self.registry) if not e.is_stock]
        if not entries:
            return None

        items = []
        severity = Severity.INFO
        for entry in entries:
            if entry.node_class == "*":
                affected = len(ctx.all_nodes)
                reach = f"all nodes ({affected} in script)"
            else:
                affected = ctx.class_counts.get(entry.node_class, 0)
                reach = f"{entry.node_class} ({affected} in script)"
            severity = max(severity, self.severity_for_entry(entry, affected))
            items.append(FindingItem(
                label=entry.name,
                kind=entry.node_class,
                detail=f"{reach}; from {entry.source_file or 'unknown file'}",
                code=f"# {entry.source_file or 'source file unknown'}\n\n{entry.source()}",
            ))

        files = sorted({e.source_file for e in entries if e.source_file})
        count = len(entries)
        return self.finding(
            severity=severity,
            metric=plural(count, "callback"),
            summary=(f"{plural(count, 'callback')} added by tools "
                     f"{'runs' if count == 1 else 'run'} Python {self.runs_when}."),
            whats_happening=(f"Something called {self.add_function}() to register "
                             f"{'this function' if count == 1 else 'these functions'}. "
                             + (f"Registered from: {', '.join(files)}." if files else "")),
            why_slow=self.why_slow,
            how_to_fix=("Find the tool that registers it (see the file in each row) and check "
                        "whether it's still needed. If it is, narrow it to the node classes it "
                        "actually cares about instead of \"*\", and keep the function fast: "
                        "return early for nodes and knobs it doesn't handle."),
            items=items,
        )


@register
class GlobalKnobChanged(_GlobalCallbackCheck):
    id = "global_knobchanged"
    title = "Global knobChanged callbacks"
    description = "Looks for knobChanged callbacks registered by tools with nuke.addKnobChanged()."
    order = 40
    registry = "knobChangeds"
    add_function = "nuke.addKnobChanged"
    runs_when = "whenever a knob changes on nodes of the registered class"
    why_slow = ("Nuke calls these for every knob change on every node of the registered class, "
                "including selecting and moving nodes. One registered for \"*\" runs for every "
                "node in the script, so its cost is added to nearly everything you do.")


@register
class GlobalAutolabel(_GlobalCallbackCheck):
    id = "global_autolabel"
    title = "Global autolabel callbacks"
    description = "Looks for autolabel callbacks registered by tools with nuke.addAutolabel()."
    order = 41
    registry = "autolabels"
    add_function = "nuke.addAutolabel"
    runs_when = "to build node labels every time the Node Graph redraws"
    why_slow = ("Autolabel callbacks run for each node every time the Node Graph draws it. "
                "Nuke's own autolabel already does this; each extra callback adds its cost "
                "per node on every redraw, so it grows with the size of the script.")


@register
class GlobalUpdateUI(_GlobalCallbackCheck):
    id = "global_updateui"
    title = "Global updateUI callbacks"
    description = "Looks for updateUI callbacks registered by tools with nuke.addUpdateUI()."
    order = 42
    registry = "updateUIs"
    add_function = "nuke.addUpdateUI"
    runs_when = "every time Nuke refreshes the UI for nodes of the registered class"
    why_slow = ("updateUI callbacks run for nodes of the registered class whenever Nuke "
                "updates the interface, such as on frame changes, so they add Python to "
                "playback and scrubbing.")
