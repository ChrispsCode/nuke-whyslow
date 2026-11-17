"""
Node-creation probe (deep scan): create a node like a user would, delete it
again, and record which tools' Python runs and what it waits on.

Harry's production symptom: when a database or file service is down, even
creating a Blur takes minutes. In a clean session creation stays fast even
on slow storage (measured), so that freeze is a tool's onCreate /
onUserCreate / knobChanged waiting on the network: this names it.
"""

from __future__ import annotations

import time
import traceback

import nuke

from whyslow import config
from whyslow.checks._helpers import entry_item, ms, plural, waits_note
from whyslow.core import probe
from whyslow.core.check import CATEGORY_SESSION, NEEDS_GUI, NEEDS_SESSION, AsyncCheck, register
from whyslow.core.finding import FindingItem, Severity
from whyslow.qt import QtCore

# # Lets the GUI side of creating / deleting finish, and anything a tool
# # deferred with a short QTimer run, before the next step.
SETTLE_MS = 150


@register
class NodeCreate(AsyncCheck):
    id = "node_create"
    title = "Python on node creation"
    category = CATEGORY_SESSION
    description = ("Creates and deletes a temporary node (like a user would, undo off) and records "
                   "any tool's Python that runs, and what it waits on.")
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    deep = True
    progress_message = "Creating and deleting a temporary node..."
    order = 68
    # # ms of tool Python per node created (and deleted) -> severity.
    severity_steps = ((0, Severity.LOW), (20, Severity.MEDIUM), (100, Severity.HIGH), (1000, Severity.CRITICAL))

    def start(self, ctx, done, progress):
        classes = list(config.NODE_CREATE_PROBE_CLASSES)
        if not classes:
            done(None)
            return
        with nuke.root():
            selected = nuke.selectedNodes()
            for node in selected:
                node.setSelected(False)
        session = probe.ProfileSession(nested=False)
        times: dict[str, list[float]] = {}   # # class -> [create ms, delete ms]
        state = {"node": None, "started": None, "index": 0, "phase": "create"}

        def restore_selection():
            for node in selected:
                try:
                    node.setSelected(True)
                except ValueError:  # # deleted meanwhile
                    pass

        def finish(error: str = ""):
            entries = session.stop()
            if state["node"] is not None:  # # never leave the temporary node behind
                try:
                    nuke.delete(state["node"])
                except Exception:
                    pass
            restore_selection()
            if error:
                done(error=error)
                return
            try:
                done(self._finding(entries, times, classes))
            except Exception:
                done(error=traceback.format_exc())

        def timed():
            # # The step's own GUI work is done when this 0 ms timer fires: record it,
            # # then let deferred work settle before the next step.
            cls = classes[state["index"]]
            times.setdefault(cls, []).append((time.perf_counter() - state["started"]) * 1000)
            if state["phase"] == "delete":
                state["index"] += 1
                state["phase"] = "create"
            else:
                state["phase"] = "delete"
            QtCore.QTimer.singleShot(SETTLE_MS, step)

        def step():
            try:
                if state["index"] >= len(classes):
                    finish()
                    return
                cls = classes[state["index"]]
                if progress(state["index"] / len(classes), f"Creating and deleting a {cls}..."):
                    finish()
                    return
                nuke.Undo.disable()
                try:
                    state["started"] = time.perf_counter()
                    if state["phase"] == "create":
                        with nuke.root():
                            state["node"] = nuke.createNode(cls, inpanel=False)
                    else:
                        node, state["node"] = state["node"], None
                        nuke.delete(node)
                finally:
                    nuke.Undo.enable()
                QtCore.QTimer.singleShot(0, timed)
            except Exception:
                finish(error=traceback.format_exc())

        session.start()
        QtCore.QTimer.singleShot(0, step)

    # # #------------------------------------------------------------------------

    def _finding(self, entries, times, classes):
        count = max(1, len(times))
        tools = probe.others(entries)
        python_ms = sum(e.seconds for e in tools) * 1000 / count
        gui = times
        slowest = max((sum(steps) for steps in gui.values()), default=0.0)
        timing = "; ".join(f"{cls}: create {ms(steps[0])}" + (f", delete {ms(steps[1])}" if len(steps) > 1 else "")
                           for cls, steps in gui.items())

        if not tools and slowest < config.NODE_CREATE_SLOW_MS:
            return None
        if not tools:
            return self.finding(
                severity=Severity.INFO,
                metric=ms(slowest),
                summary=(f"Creating and deleting a node takes {ms(slowest)} of GUI time, but no tool's "
                         "Python runs during it, so the cost is in Nuke itself."),
                whats_happening=f"Created and deleted {', '.join(classes)} with undo off. {timing}.",
                why_slow="Nuke itself is slow to add nodes here: a very large script, or many open panels.",
                how_to_fix="Check the script size and close panels you don't need.",
            )

        names = ", ".join(sorted({e.tool for e in tools}))
        return self.finding(
            severity=self.severity_for(int(python_ms)),
            metric=f"{ms(python_ms)}/node",
            summary=(f"Creating a node runs {ms(python_ms)} of Python from {names}."
                     + waits_note(tools)),
            whats_happening=(
                f"Created and deleted {', '.join(classes)} with nuke.createNode (as a user would; "
                f"undo was off, and the selection was put back) and recorded what ran. {timing} "
                "(GUI time). Rows are tool functions: usually onCreate, onUserCreate, onDestroy or "
                "knobChanged callbacks, or a node's own knob scripts."),
            why_slow=("This Python runs every time anyone creates a node, so the UI waits for it each "
                      "time. If it talks to a database, a file service or the network, creating nodes "
                      "freezes Nuke whenever that service is slow or down."),
            how_to_fix=("Find the tool in each row. Callbacks that need a service should time out "
                        "quickly, cache the answer, or do the work in the background instead of "
                        "waiting on the UI thread."),
            items=[entry_item(e, f"ran {e.calls}x, {ms(e.seconds * 1000 / count)}/node (profiled)")
                   for e in tools],
        )
