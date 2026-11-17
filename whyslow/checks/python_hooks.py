"""
Python hooked into Nuke itself, by any tool, known or not. These generalise
what the KnobScripter investigation found (knobscripter_findings.md):

- Script Editor output hooks: anything listening to the output changing runs
  on every print (KnobScripter's problem, done by any other tool).
- Application-wide event filters: Python that sees every Qt event.
- Python running while idle: timers and polling (deep scan only).
- Tracing or profiling left on: slows every line of Python.
- Wrapped print: a tool replacing sys.stdout/stderr runs on every print.

Nuke's own code and whyslow are never reported. Tools with their own card
(config.DEDICATED_TOOLS) are listed but not counted.
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
import traceback

from whyslow import config
from whyslow.checks._helpers import entry_item, ms, plural, print_hook_severity, waits_note
from whyslow.core import probe
from whyslow.core.check import CATEGORY_SESSION, NEEDS_GUI, NEEDS_SESSION, AsyncCheck, Check, register
from whyslow.core.finding import FindingItem, Severity
from whyslow.qt import QtCore


_entry_item = entry_item


def _tools(entries) -> str:
    return ", ".join(sorted({e.tool for e in entries}))


# # #----------------------------------------------------------------------------
# # Script Editor output hooks
# # #----------------------------------------------------------------------------

@register
class ScriptEditorHooks(Check):
    id = "script_editor_hooks"
    title = "Script Editor output hooks"
    category = CATEGORY_SESSION
    description = "Triggers the Script Editor output and records any tool's Python that runs on every print."
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    progress_message = "Probing the Script Editor output for hooks..."
    order = 61
    # # ms per change (doubled with echo) -> severity; any hook is at least Medium.
    severity_steps = ((0, Severity.MEDIUM), (5, Severity.HIGH), (20, Severity.CRITICAL))

    def run(self, ctx):
        echo = bool(ctx.pref("echoAllCommands", False))
        items, counted, worst_ms, unexplained = [], [], 0.0, 0
        for index, output in enumerate(probe.script_outputs(), 1):
            receivers = probe.text_changed_receivers(output)
            if not receivers:
                continue
            entries, _, wall = probe.profile_entries(lambda: probe.emit_text_changed(output))
            hooks = probe.others(entries)
            mine = [e for e in hooks if e.tool not in config.DEDICATED_TOOLS]
            cost_ms = probe.median_ms(lambda: probe.emit_text_changed(output)) * probe.share(mine, wall)
            worst_ms = max(worst_ms, cost_ms)
            counted += mine
            # # Connections that ran no Python: C++ plugins, or hooks we can't see.
            unexplained += max(0, receivers - sum(e.calls for e in hooks))
            for entry in hooks:
                items.append(_entry_item(entry, f"output {index}: ran {entry.calls}x per change, "
                                                f"{entry.seconds * 1000:.2f} ms (profiled)"))
        if not counted and not unexplained:
            return None

        if counted:
            severity = print_hook_severity(self, worst_ms, echo)
            summary = (f"{plural(len(counted), 'hook')} from {_tools(counted)} "
                       f"{'runs' if len(counted) == 1 else 'run'} Python every time anything prints "
                       f"to the Script Editor, costing {ms(worst_ms)} per change.")
        else:
            severity = Severity.LOW
            summary = (f"The Script Editor output has {plural(unexplained, 'listener')} that "
                       "ran no Python. Probably harmless; listed so you know they're there.")
        if unexplained:
            items.append(FindingItem(label="Other connections", kind="textChanged",
                                     detail=f"{unexplained} ran no Python (C++ or unknown)"))
        return self.finding(
            severity=severity,
            metric=f"{ms(worst_ms)} per change" if counted else plural(unexplained, "listener"),
            summary=summary,
            whats_happening=(
                "Something connected to the Script Editor output's textChanged signal. Vanilla Nuke "
                "has nothing connected there, so every listener comes from a tool. The probe "
                "triggered the signal without changing the text and recorded what ran."
                + (" Echo is on, so ordinary UI actions print too, and the cost counts double."
                   if echo else "")
                + (f" Hooks from {', '.join(sorted(config.DEDICATED_TOOLS))} are listed but "
                   "counted on their own card." if any(i.kind.endswith("(has its own card)") for i in items) else "")),
            why_slow=("This Python runs on every line printed to the Script Editor, from any tool, "
                      "and from echoed UI actions. If it copies or searches the output (as "
                      "KnobScripter does), the cost grows as the Script Editor fills, so the session "
                      "gets steadily slower."),
            how_to_fix=("Find the tool in each row and check whether it needs the hook. Clearing "
                        "the Script Editor output and turning echo off reduce the cost straight "
                        "away. A well-behaved hook connects once, and only handles the new text."),
            items=items,
        )


# # #----------------------------------------------------------------------------
# # Application-wide event filters
# # #----------------------------------------------------------------------------

@register
class AppEventFilters(Check):
    id = "app_event_filters"
    title = "Application-wide event filters"
    category = CATEGORY_SESSION
    description = "Sends harmless events through Qt and records any Python that inspects every event."
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    progress_message = "Probing for application-wide event filters..."
    order = 62
    # # microseconds per event -> severity.
    severity_steps = ((0, Severity.MEDIUM), (20, Severity.HIGH))

    def run(self, ctx):
        count = config.PROBE_EVENT_COUNT
        send = probe.send_probe_events(count)
        entries, _, _ = probe.profile_entries(send)
        filters = probe.others(entries)
        if not filters:
            return None
        per_event_us = probe.median_ms(send, runs=3) * 1000 / count
        return self.finding(
            severity=self.severity_for(int(per_event_us)),
            metric=f"{per_event_us:.1f} µs per event",
            summary=(f"{plural(len(filters), 'event filter')} from {_tools(filters)} "
                     f"{'sees' if len(filters) == 1 else 'see'} every Qt event in Nuke: every mouse "
                     "move, paint and key press runs Python."),
            whats_happening=(f"{count} custom events were sent to an object nothing in Nuke uses. "
                             "Python still ran for them, so it's filtering events for the whole "
                             f"application. Each event took {per_event_us:.1f} µs in total."),
            why_slow=("Nuke handles thousands of events a second while you work. A Python event "
                      "filter on the application runs for all of them, so even a fast one adds up, "
                      "and a slow one makes the whole UI sluggish."),
            how_to_fix=("Find the tool in each row. Event filters should be installed on the "
                        "widgets that need them, not on the application, and removed when done."),
            items=[_entry_item(e, f"ran {e.calls}x for {count} events, "
                                  f"{e.seconds * 1000:.2f} ms (profiled)") for e in filters],
        )


# # #----------------------------------------------------------------------------
# # Python running while idle (deep)
# # #----------------------------------------------------------------------------

@register
class IdlePython(AsyncCheck):
    id = "idle_python"
    title = "Python running while idle"
    category = CATEGORY_SESSION
    description = "Watches Nuke sitting idle and records any tool's Python that runs anyway (timers, polling)."
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    deep = True
    progress_message = f"Watching Nuke idle for {config.IDLE_PROBE_SECONDS:g} s (don't touch anything)..."
    order = 63
    # # ms of Python per second of idle -> severity.
    severity_steps = ((0, Severity.LOW), (10, Severity.MEDIUM), (50, Severity.HIGH))

    def start(self, ctx, done, progress):
        # # Asynchronous, so Nuke's own idle work runs normally while we watch.
        seconds = config.IDLE_PROBE_SECONDS
        tick_ms = 250
        ticks = max(1, int(seconds * 1000 / tick_ms))
        session = probe.ProfileSession(nested=False)
        state = {"tick": 0, "started": time.perf_counter()}

        def tick():
            state["tick"] += 1
            cancelled = progress(state["tick"] / ticks)
            if state["tick"] < ticks and not cancelled:
                QtCore.QTimer.singleShot(tick_ms, tick)
                return
            entries = session.stop()
            if cancelled:
                done(None)
                return
            try:
                done(self._finding(entries, time.perf_counter() - state["started"]))
            except Exception:
                done(error=traceback.format_exc())

        session.start()
        QtCore.QTimer.singleShot(tick_ms, tick)

    def _finding(self, entries, seconds: float):
        busy = probe.others(entries)
        if not busy:
            return None
        ms_per_s = sum(e.seconds for e in busy) * 1000 / seconds
        return self.finding(
            severity=self.severity_for(int(ms_per_s)),
            metric=f"{ms_per_s:.1f} ms/s",
            summary=(f"While Nuke sat idle, Python from {_tools(busy)} kept running: "
                     f"{ms_per_s:.1f} ms of every second." + waits_note(busy)),
            whats_happening=(f"Nuke was left idle for {seconds:.1f} s and every Python function it "
                             "called into was recorded. Nuke's own (like the status bar) are left "
                             "out. These are usually QTimers polling for something."),
            why_slow=("Work that runs on a timer competes with everything else on Nuke's main "
                      "thread: drawing, playback and interaction all wait for it. Short, frequent "
                      "timers and polling that scans the script are the usual culprits."),
            how_to_fix=("Find the tool in each row. Polling is better replaced by Nuke callbacks or "
                        "signals, or the timer slowed down and stopped when its UI is hidden."),
            items=[_entry_item(e, f"{e.calls / seconds:.1f} calls/s, "
                                  f"{e.seconds * 1000 / seconds:.2f} ms/s (profiled)") for e in busy],
        )


# # #----------------------------------------------------------------------------
# # Tracing / profiling left on
# # #----------------------------------------------------------------------------

@register
class PythonTracing(Check):
    id = "python_tracing"
    title = "Python tracing or profiling is on"
    category = CATEGORY_SESSION
    description = "Checks for a debugger, coverage tool or profiler hooked into Python."
    needs = frozenset({NEEDS_SESSION})
    order = 64
    severity_steps = ((1, Severity.CRITICAL),)

    def run(self, ctx):
        hooks = [("sys.gettrace()", sys.gettrace()), ("sys.getprofile()", sys.getprofile())]
        for name in ("gettrace", "getprofile"):
            getter = getattr(threading, name, None)
            if getter is not None:
                hooks.append((f"threading.{name}()", getter()))
        active = [(where, hook) for where, hook in hooks if hook is not None]
        if not active:
            return None
        items = []
        for where, hook in active:
            module = getattr(hook, "__module__", None) or type(hook).__module__
            name = getattr(hook, "__qualname__", None) or type(hook).__qualname__
            try:
                source_file = inspect.getsourcefile(hook) or ""
            except TypeError:
                source_file = ""
            items.append(FindingItem(label=where, kind=module or "?", detail=f"{module}.{name}",
                                     code=f"# {source_file or 'source file unknown'}"))
        return self.finding(
            severity=self.severity_for(len(active)),
            metric=plural(len(active), "hook"),
            summary=("Python has a trace or profile hook installed, so every line of Python in "
                     "Nuke runs several times slower. Usually a debugger or profiler left attached."),
            whats_happening="Installed: " + ", ".join(f"{w} = {i.detail}" for (w, _), i in zip(active, items)) + ".",
            why_slow=("A trace or profile hook is called for every function call (and with tracing, "
                      "every line) of Python. Everything Python does in Nuke, including all the "
                      "callbacks on the other cards, gets much slower."),
            how_to_fix=("Detach the debugger or stop the profiler (for example a remote debugger "
                        "attached from an IDE, or a coverage run). Restart Nuke if you can't tell "
                        "what installed it."),
            items=items,
        )


# # #----------------------------------------------------------------------------
# # Wrapped print
# # #----------------------------------------------------------------------------

@register
class StreamRedirect(Check):
    id = "stream_redirect"
    title = "print is wrapped by a tool"
    category = CATEGORY_SESSION
    description = "Checks that sys.stdout and sys.stderr are still Nuke's own Script Editor streams."
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    order = 65
    severity_steps = ((1, Severity.MEDIUM),)

    def run(self, ctx):
        items = []
        for name, expected in config.EXPECTED_STREAMS.items():
            stream = getattr(sys, name)
            cls = type(stream)
            actual = f"{cls.__module__}.{cls.__qualname__}"
            if actual == expected:
                continue
            try:
                source_file = inspect.getsourcefile(cls) or ""
            except TypeError:
                source_file = ""
            items.append(FindingItem(label=f"sys.{name}", kind=cls.__module__.split(".")[0],
                                     detail=f"{actual} (expected {expected})",
                                     code=f"# {source_file or 'source file unknown'}"))
        if not items:
            return None
        return self.finding(
            severity=self.severity_for(len(items)),
            metric=plural(len(items), "stream"),
            summary=(f"A tool has replaced {' and '.join(i.label for i in items)}, so its Python "
                     "runs on every print in Nuke."),
            whats_happening=("Nuke puts its own Script Editor streams on sys.stdout and sys.stderr. "
                             "Something has swapped in its own: " + "; ".join(i.detail for i in items) + "."),
            why_slow=("Every print and every warning from every tool goes through the wrapper. If it "
                      "does real work per write (logging to a file, mirroring the output, searching "
                      "it), that cost lands on every line printed."),
            how_to_fix="Find the tool from the class's module and check what its wrapper does per write.",
            items=items,
        )
