"""
Trigger-and-record probes: make Nuke's UI do something (emit a signal, send
events, sit idle), and record which Python runs in response, how often and
for how long, attributed to the file and module it came from.

GUI only. Importing this module is safe headless (Qt classes are imported
but nothing is built until a probe runs).

The profiler records *entry points* only: Python functions called directly
from C++ (a Qt signal, an event filter, a timer). Everything those call is
counted inside their time. A frame is an entry point when the innermost
thing on the profiler's stack is a C call (sys.setprofile "c_call" events),
which is how Qt hands control to Python.
"""

from __future__ import annotations

import collections
import os
import sys
import time
from dataclasses import dataclass

import nuke

from whyslow.qt import QtCore, QtWidgets

SCRIPT_OUTPUT_CLASS = "Foundry::PythonUI::ScriptOutputWidget"

KIND_STOCK = "stock"      # # Nuke's own code (inside the install folder) or Python's
KIND_SELF = "whyslow"     # # this tool
KIND_OTHER = "other"      # # anything else: a third-party tool


@dataclass
class Entry:
    """One Python function that C++ called into during a probe."""

    filename: str
    module: str
    qualname: str
    line: int
    calls: int = 0
    seconds: float = 0.0
    kind: str = KIND_OTHER

    @property
    def tool(self) -> str:
        """Best guess at which tool this belongs to: its top-level package."""
        if self.module and self.module not in ("__main__", "builtins"):
            return self.module.split(".")[0]
        if self.filename.startswith("<"):
            return "__main__ (typed or exec'd code)"
        return os.path.basename(os.path.dirname(self.filename)) or self.filename

    @property
    def label(self) -> str:
        return f"{self.module or '?'}.{self.qualname}"

    @property
    def location(self) -> str:
        return f"{self.filename}:{self.line}"


def _kind(filename: str, module: str, nuke_dir: str) -> str:
    if module and (module == "whyslow" or module.startswith("whyslow.")):
        return KIND_SELF
    if filename.startswith("<frozen"):
        return KIND_STOCK
    if not filename.startswith("<"):
        real = os.path.realpath(filename)
        if real.startswith(nuke_dir + os.sep):
            return KIND_STOCK
    return KIND_OTHER


def profile_entries(fn) -> tuple[list[Entry], object]:
    """Call `fn()` and return (entry points that ran, fn's return value).

    Any profile function already installed (a profiler someone left on) is
    put back afterwards.
    """
    nuke_dir = os.path.realpath(os.path.dirname(nuke.EXE_PATH))
    entries: dict[tuple, Entry] = {}
    # # "c" while a C function is running, otherwise (entry key or None, start time).
    # # Starts with a frame for our own call to fn(), so fn itself isn't an entry.
    stack: list = [(None, 0.0)]
    perf = time.perf_counter

    def profiler(frame, event, arg):
        if event == "call":
            if stack and stack[-1] == "c":
                code = frame.f_code
                module = frame.f_globals.get("__name__") or ""
                key = (code.co_filename, module, getattr(code, "co_qualname", code.co_name), code.co_firstlineno)
                entry = entries.get(key)
                if entry is None:
                    entry = entries[key] = Entry(*key, kind=_kind(key[0], module, nuke_dir))
                entry.calls += 1
                stack.append((key, perf()))
            else:
                stack.append((None, 0.0))
        elif event == "return":
            if stack and stack[-1] != "c":
                key, started = stack.pop()
                if key is not None:
                    entries[key].seconds += perf() - started
        elif event == "c_call":
            stack.append("c")
        elif event in ("c_return", "c_exception"):
            if stack and stack[-1] == "c":
                stack.pop()

    previous = sys.getprofile()
    sys.setprofile(profiler)
    try:
        value = fn()
    finally:
        sys.setprofile(previous)
    return sorted(entries.values(), key=lambda e: -e.seconds), value


def median_ms(fn, runs: int = 5) -> float:
    """Median wall time of fn() in milliseconds (without the profiler)."""
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        times.append(time.perf_counter() - started)
    return sorted(times)[len(times) // 2] * 1000


def others(entries: list[Entry]) -> list[Entry]:
    """Only the entries from third-party code."""
    return [e for e in entries if e.kind == KIND_OTHER]


# # #----------------------------------------------------------------------------
# # Triggers
# # #----------------------------------------------------------------------------

def script_outputs() -> list:
    """The output panes of every open Script Editor."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return []
    return [w for w in app.allWidgets() if w.metaObject().className() == SCRIPT_OUTPUT_CLASS]


def text_changed_receivers(widget) -> int:
    """Connections to the widget's textChanged (vanilla Nuke: 0 on the Script Editor output)."""
    return widget.receivers(QtCore.SIGNAL("textChanged()"))


def emit_text_changed(widget) -> None:
    """Run everything listening to the output changing, without changing the text."""
    widget.textChanged.emit()


class _ProbeObject(QtCore.QObject):
    """A target for probe events: nothing in Nuke listens to it, so anything
    that sees its events is an application-wide event filter."""


def send_probe_events(count: int):
    """Return a function that sends `count` harmless custom events through Qt."""
    target = _ProbeObject()
    event_type = QtCore.QEvent.Type(QtCore.QEvent.registerEventType())

    def send():
        for _ in range(count):
            QtCore.QCoreApplication.sendEvent(target, QtCore.QEvent(event_type))
    send.target = target  # # keep it alive with the function
    return send


def run_event_loop(seconds: float) -> None:
    """Let Nuke run normally for `seconds`, so timers and idle work happen."""
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec_() if hasattr(loop, "exec_") else loop.exec()


def group_by_tool(entries: list[Entry]) -> dict[str, list[Entry]]:
    grouped = collections.defaultdict(list)
    for entry in entries:
        grouped[entry.tool].append(entry)
    return dict(grouped)
