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
from dataclasses import dataclass, field

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
    # # For code run from knob strings ("<string>": expressions, knobChanged,
    # # autolabel...): (node full name, knob name) -> [calls, seconds].
    contexts: dict = field(default_factory=dict)

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


def _knob_context() -> tuple[str, str]:
    """The node and knob Nuke is running code for, as (full name, knob name)."""
    try:
        node = nuke.thisNode()
        knob = nuke.thisKnob()
        return (node.fullName() if node is not None else "", knob.name() if knob is not None else "")
    except Exception:
        return ("", "")


class ProfileSession:
    """Records entry points between start() and stop().

    An entry point is where a tool's code starts running: a Python call made
    from C++ (a Qt signal, an event filter, a timer, a knob script), or a
    third-party function called from Nuke's own Python (the dispatcher behind
    nuke.addKnobChanged / addUpdateUI / addAutolabel). Code called from inside
    an entry point counts towards that entry's time.

    nested=True: for profiling one call made from Python (profile_entries).
    nested=False: for asynchronous probes, where the profiler stays installed
    while control goes back to Nuke's main loop; then a Python call with
    nothing above it on the stack also counts as called from C++.
    """

    def __init__(self, nested: bool = True):
        self.nuke_dir = os.path.realpath(os.path.dirname(nuke.EXE_PATH))
        self.entries: dict[tuple, Entry] = {}
        self._kinds: dict[tuple, str] = {}
        # # Items: "c" while a C function runs, else (entry key or None, start,
        # # knob context, inside third-party code). Nested mode starts with a
        # # frame for our own call, so it isn't an entry.
        self.stack: list = [(None, 0.0, None, False)] if nested else []
        self._previous = None
        self._running = False
        self._started = 0.0
        self.wall = 0.0  # # seconds between start() and stop()

    def _kind_of(self, filename: str, module: str) -> str:
        cache_key = (filename, module)
        kind = self._kinds.get(cache_key)
        if kind is None:
            kind = self._kinds[cache_key] = _kind(filename, module, self.nuke_dir)
        return kind

    def _profiler(self, frame, event, arg):
        stack = self.stack
        if event == "call":
            parent = stack[-1] if stack else "c"
            code = frame.f_code
            module = frame.f_globals.get("__name__") or ""
            kind = self._kind_of(code.co_filename, module)
            if parent == "c":
                is_entry, inside_other = True, kind == KIND_OTHER
            elif parent[3]:
                is_entry, inside_other = False, True
            else:
                # # Called from Nuke's own Python: only a function from a real file
                # # starts a tool's entry. Code generated by libraries (namedtuple,
                # # dataclasses: "<string>") belongs to whoever called it.
                is_entry = inside_other = kind == KIND_OTHER and not code.co_filename.startswith("<")
            if not is_entry:
                stack.append((None, 0.0, None, inside_other))
                return
            key = (code.co_filename, module, getattr(code, "co_qualname", code.co_name), code.co_firstlineno)
            entry = self.entries.get(key)
            if entry is None:
                entry = self.entries[key] = Entry(*key, kind=kind)
            entry.calls += 1
            context = _knob_context() if key[0].startswith("<") else None
            if context is not None:
                entry.contexts.setdefault(context, [0, 0.0])[0] += 1
            stack.append((key, time.perf_counter(), context, inside_other))
        elif event == "return":
            if stack and stack[-1] != "c":
                key, started, context, _ = stack.pop()
                if key is not None:
                    elapsed = time.perf_counter() - started
                    self.entries[key].seconds += elapsed
                    if context is not None:
                        self.entries[key].contexts[context][1] += elapsed
        elif event == "c_call":
            stack.append("c")
        elif event in ("c_return", "c_exception"):
            if stack and stack[-1] == "c":
                stack.pop()

    def start(self) -> None:
        self._previous = sys.getprofile()
        self._started = time.perf_counter()
        sys.setprofile(self._profiler)
        self._running = True

    def stop(self) -> list[Entry]:
        """Remove the profiler (putting back any that was there) and return the entries."""
        if self._running:
            sys.setprofile(self._previous)
            self._running = False
            self.wall = time.perf_counter() - self._started
        return sorted(self.entries.values(), key=lambda e: -e.seconds)


def profile_entries(fn) -> tuple[list[Entry], object, float]:
    """Call `fn()` and return (entry points that ran, fn's return value, wall seconds).

    Code with no file ("<string>": knob scripts and [python] expressions) is
    split by the node and knob it ran for (Entry.contexts). Any profile
    function already installed (a profiler someone left on) is put back
    afterwards.
    """
    session = ProfileSession(nested=True)
    session.start()
    try:
        value = fn()
    finally:
        entries = session.stop()
    return entries, value, session.wall


def share(entries: list[Entry], wall: float) -> float:
    """Fraction (0-1) of a profiled run's wall time spent in these entries."""
    return min(1.0, sum(e.seconds for e in entries) / wall) if wall > 0 else 0.0


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


def choose_frames(current: int, first: int, last: int, count: int) -> tuple[int, list[int]]:
    """(start frame, the `count` frames stepped to one at a time after it).

    Kept inside first..last (where the Reads have frames) when the range is
    long enough, as close to the current frame as possible; otherwise steps
    forward from the current frame.
    """
    if last - first >= count:
        start = min(max(current, first), last - count)
    else:
        start = current
    return start, [start + step for step in range(1, count + 1)]


def group_by_tool(entries: list[Entry]) -> dict[str, list[Entry]]:
    grouped = collections.defaultdict(list)
    for entry in entries:
        grouped[entry.tool].append(entry)
    return dict(grouped)
