"""
Frame-change probe (deep scan): step through a few frames and record which
Python runs and how much GUI time each frame change takes.

Only GUI time is measured: each change is queued straight after the previous
one, so the Viewer never gets to render (render speed is a separate kind of
diagnostic). This catches per-frame Python no other check sees, like
[python ...] in knob expressions and labels, and shows which of the other
cards' hooks actually run on the timeline.
"""

from __future__ import annotations

import statistics
import time
import traceback

import nuke

from whyslow import config
from whyslow.checks._helpers import ms, plural
from whyslow.core import probe
from whyslow.core.check import CATEGORY_SESSION, NEEDS_GUI, NEEDS_SESSION, AsyncCheck, register
from whyslow.core.finding import FindingItem, Severity
from whyslow.qt import QtCore


def _python_knobs(node) -> list:
    """Knobs on `node` whose value or expression contains [python ...]."""
    found = []
    for knob in node.allKnobs():
        try:
            if "[python" in knob.toScript():
                found.append(knob)
        except Exception:
            continue
    return found


def _knob_code(node, knob_name: str) -> tuple[str, str]:
    """(row label suffix, code view text) for knob-string code run on `node`.

    Nuke doesn't set nuke.thisKnob() while it evaluates a label (and some other
    knobs), so when the knob is unknown, show every knob holding [python ...];
    failing that, the label and all non-default knobs.
    """
    knob = node.knob(knob_name) if knob_name else None
    if knob is not None:
        return f".{knob_name}", knob.toScript()
    knobs = _python_knobs(node)
    if knobs:
        names = ", ".join(k.name() for k in knobs)
        return f".{names}" if len(knobs) == 1 else f" ({names})", "\n\n".join(
            f"# {k.name()}\n{k.toScript()}" for k in knobs)
    label_knob = node.knob("label")
    label = label_knob.toScript() if label_knob is not None else ""
    return "", (f"# Knob not known. label:\n{label}\n\n# Non-default knobs:\n"
                f"{node.writeKnobs(nuke.WRITE_NON_DEFAULT_ONLY | nuke.TO_SCRIPT)}")


def _items(entries: list[probe.Entry], frames: int) -> list[FindingItem]:
    """One row per function, or per node and knob for knob-string code."""
    items = []
    for entry in entries:
        if entry.contexts:
            for (node_path, knob_name), (calls, seconds) in sorted(
                    entry.contexts.items(), key=lambda item: -item[1][1]):
                node = nuke.toNode(node_path) if node_path else None
                if node is not None:
                    suffix, code = _knob_code(node, knob_name)
                    label = f"{node_path}{suffix}"
                else:
                    label = f"{node_path}.{knob_name}" if knob_name else (node_path or "(no node)")
                    code = "# Node no longer exists."
                items.append(FindingItem(
                    label=label, node_path=node_path if node is not None and node_path else "",
                    kind="Python in a knob", detail=f"{calls / frames:.1f} calls/frame, "
                                                    f"{ms(seconds * 1000 / frames)}/frame (profiled)",
                    code=f"# {label}\n{code}"))
        else:
            items.append(FindingItem(
                label=entry.label, kind=entry.tool,
                detail=f"{entry.calls / frames:.1f} calls/frame, {ms(entry.seconds * 1000 / frames)}/frame (profiled)",
                code=f"# {entry.location}"))
    return items


@register
class FrameChange(AsyncCheck):
    id = "frame_change"
    title = "Python on frame change"
    category = CATEGORY_SESSION
    description = (f"Steps through {config.FRAME_PROBE_STEPS} frames (GUI only, no render) and records "
                   "any tool's Python that runs on each frame change.")
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    deep = True
    progress_message = f"Stepping through {config.FRAME_PROBE_STEPS} frames..."
    order = 66
    # # ms of third-party Python per frame change -> severity.
    severity_steps = ((0, Severity.LOW), (5, Severity.MEDIUM), (20, Severity.HIGH), (40, Severity.CRITICAL))

    def start(self, ctx, done, progress):
        root = nuke.root()
        original = nuke.frame()
        first, last = int(root.firstFrame()), int(root.lastFrame())
        jump_to, frames = probe.choose_frames(int(original), first, last, config.FRAME_PROBE_STEPS)
        session = probe.ProfileSession(nested=False)
        times: list[float] = []
        state = {"index": 0, "started": None}
        outputs = probe.script_outputs()
        printed = {"before": [], "text": ""}
        echo = bool(ctx.pref("echoAllCommands", False))

        def finish(error: str = "", cancelled: bool = False):
            entries = session.stop()
            try:
                # # What the frame changes printed to the Script Editor.
                printed["text"] = "".join(o.document().toPlainText()[before:]
                                          for o, before in zip(outputs, printed["before"]))
            except Exception:
                pass
            try:
                nuke.frame(original)
            except Exception:
                pass
            if error:
                done(error=error)
            elif cancelled:
                done(None)
            else:
                try:
                    done(self._finding(entries, times, frames, first, last, printed["text"], echo))
                except Exception:
                    done(error=traceback.format_exc())

        def step():
            # # Each change is queued straight after Nuke has handled the last one's
            # # GUI work, so the Viewer never gets to render.
            try:
                now = time.perf_counter()
                if state["started"] is not None:
                    times.append((now - state["started"]) * 1000)
                index = state["index"]
                if index >= len(frames):
                    finish()
                    return
                if progress(index / len(frames), f"Frame change {index + 1} of {len(frames)} "
                                                  f"(frame {frames[index]})..."):
                    finish(cancelled=True)
                    return
                state["started"] = time.perf_counter()
                nuke.frame(frames[index])
                state["index"] += 1
                QtCore.QTimer.singleShot(0, step)
            except Exception:
                finish(error=traceback.format_exc())

        def begin():
            # # Let the jump to the first frame settle, then profile the steps.
            printed["before"] = [len(o.document().toPlainText()) for o in outputs]
            session.start()
            step()

        nuke.frame(jump_to)
        QtCore.QTimer.singleShot(100, begin)

    def _finding(self, entries, times, frames, first, last, printed_text: str = "", echo: bool = False):
        count = len(frames)
        per_frame = statistics.median(times) if times else 0.0
        python = probe.others(entries)
        python_ms = sum(e.seconds for e in python) * 1000 / count
        where = (f"frames {frames[0]}-{frames[-1]}"
                 + (" (inside the project range)" if first <= frames[0] and frames[-1] <= last else ""))
        printed_lines = [line for line in printed_text.splitlines() if line.strip()]
        print_note = ""
        print_item = []
        if printed_lines:
            per = len(printed_lines) / count
            print_note = (f" Each frame change printed {per:.1f} line{'s' if per != 1 else ''} to the "
                          "Script Editor"
                          + (" (echo is on, so every [python] expression Nuke evaluates is echoed "
                             "there), and anything hooked to the Script Editor pays for each line."
                             if echo else ", and anything hooked to the Script Editor pays for each line."))
            print_item = [FindingItem(
                label="Script Editor output", kind="Printed on frame change",
                detail=f"{per:.1f} lines per frame" + ("; echo is on" if echo else ""),
                code="# First lines printed during the frame changes:\n" + "\n".join(printed_lines[:20]))]

        if not python and not printed_lines and per_frame < config.FRAME_SLOW_MS:
            return None
        if not python and printed_lines:
            return self.finding(
                severity=Severity.LOW,
                metric=f"{len(printed_lines) / count:.1f} lines/frame",
                summary=("Frame changes print to the Script Editor. Cheap on its own, but costly "
                         "if a tool is hooked to the Script Editor output."),
                whats_happening=f"Stepped through {where}, without letting the Viewer render.{print_note}",
                why_slow=("Every printed line goes through Nuke's Script Editor, and through any "
                          "tool listening to it (see the Script Editor and KnobScripter cards)."),
                how_to_fix=("Turn off 'echo python commands to output window' if it's on, and remove "
                            "the prints from whatever runs per frame."),
                items=print_item,
            )
        if not python:
            return self.finding(
                severity=Severity.INFO,
                metric=f"{ms(per_frame)}/frame",
                summary=(f"Each frame change takes {ms(per_frame)} of GUI time, but no tool's Python "
                         "runs during it, so the cost is in Nuke itself."),
                whats_happening=(f"Stepped through {where}, without letting the Viewer render. "
                                 "No third-party Python ran."),
                items=print_item,
                why_slow=("Nuke updates open properties panels, the timeline, curves and the Node "
                          "Graph on every frame change. Many open panels or a large Dope Sheet or "
                          "Curve Editor make it slower."),
                how_to_fix="Close properties panels and editors you don't need while playing back.",
            )

        tools = sorted({("knob Python" if e.contexts else e.tool) for e in python})
        return self.finding(
            severity=self.severity_for(int(python_ms)),
            metric=f"{ms(python_ms)}/frame",
            summary=(f"Every frame change runs {ms(python_ms)} of Python from {', '.join(tools)}. "
                     f"Each frame change took {ms(per_frame)} of GUI time in total."),
            whats_happening=(
                f"Stepped through {where}, one frame at a time, without letting the Viewer render, "
                f"and recorded what ran. {plural(len(python), 'Python entry point')} ran on frame "
                "changes. Rows marked 'Python in a knob' are [python ...] expressions, labels or "
                "knob scripts on that node. Timers that happen to fire during the steps show up "
                "too (see the idle card). GUI time was measured while profiling, so it's a little "
                "higher than normal." + print_note),
            why_slow=("This Python runs on every frame change, so it slows scrubbing and playback "
                      "in the GUI. At 24 fps a frame has about 42 ms in total."),
            how_to_fix=("For knob Python, use a Tcl expression instead where possible (frame, "
                        "[value ...]), or bake the result. For tools, check the matching card "
                        "(global callbacks, idle, hooks), and whether the tool needs to run per frame."),
            items=_items(python, count) + print_item,
        )
