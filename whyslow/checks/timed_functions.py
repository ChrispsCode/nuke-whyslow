"""
Timed functions (deep scan): run each function listed in
config.EXTRA_TIMED_FUNCTIONS and time it, one card per function.

For checks only a studio can write: a query to each database, a call to a
file service, a licence check. So that the card can say which service is
slow, not just that the network is.

Each function runs in a background thread by default, so a service that
never answers can't freeze Nuke: a call still running after its timeout is
abandoned (its thread ends whenever the call returns) and the card says it
didn't answer. Functions that use the nuke module must run on Nuke's main
thread instead ("main_thread": True), where nothing can stop a hung call.
"""

from __future__ import annotations

import inspect
import re
import statistics
import time
import traceback

from whyslow import config
from whyslow.checks._helpers import ms
from whyslow.core.check import CATEGORY_SESSION, NEEDS_GUI, NEEDS_SESSION, AsyncCheck, register, unregister
from whyslow.core.finding import FindingItem, Severity
from whyslow.core.storage import Watched
from whyslow.qt import QtCore

# # Defaults for entries that leave these out (see config.EXTRA_TIMED_FUNCTIONS).
DEFAULT_STEPS = ((100, "low"), (500, "medium"), (2000, "high"), (10000, "critical"))
DEFAULT_REPEATS = 3
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_ERROR_SEVERITY = "high"
# # The summary mentions the first call when it took this many times the median.
FIRST_CALL_FACTOR = 1.5
# # How much of a return value to show in Details.
RETURN_CHARS = 2000

DEFAULT_WHY = ("A timed function stands in for something Nuke or a tool does, like a database "
               "query. When it's slow, anything in Nuke that makes the same call waits as long, "
               "and if that happens on the UI thread, the whole UI waits.")
DEFAULT_HOW = ("Check the service the function talks to; Details shows the function and each "
               "call's time. A tool that makes the same call should time out quickly, cache the "
               "answer, or do the work in the background.")


def _entry_dict(entry) -> dict:
    """An entry is a dict, or just a function (or code string) with no options."""
    return entry if isinstance(entry, dict) else {"function": entry}


def _entry_name(entry: dict, index: int) -> str:
    name = entry.get("name")
    if name:
        return str(name)
    function = entry.get("function")
    return getattr(function, "__name__", None) or f"Timed function {index + 1}"


def _callable(entry: dict, name: str):
    """(callable, source shown in Details) for an entry's "function".

    A string is Python code, run with config's names available: an
    expression ("my_db_ping()") gives its value, statements give None.
    """
    function = entry.get("function")
    if callable(function):
        try:
            source = inspect.getsource(function)
        except (OSError, TypeError):
            source = repr(function)
        return function, source
    if isinstance(function, str) and function.strip():
        code = function.strip()
        namespace = dict(vars(config))
        filename = f"<whyslow timed function: {name}>"
        try:
            compiled = compile(code, filename, "eval")
            return (lambda: eval(compiled, namespace)), code
        except SyntaxError:
            compiled = compile(code, filename, "exec")  # # a real syntax error raises here
            return (lambda: exec(compiled, namespace)), code
    raise ValueError(f"config.EXTRA_TIMED_FUNCTIONS '{name}': \"function\" must be a function "
                     f"or a string of Python code, not {function!r}")


def _short(value) -> str:
    text = repr(value)
    return text if len(text) <= RETURN_CHARS else text[:RETURN_CHARS] + " ..."


# # #----------------------------------------------------------------------------
# # The check (one generated subclass per config entry)
# # #----------------------------------------------------------------------------

class _TimedFunction(AsyncCheck):
    category = CATEGORY_SESSION
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    deep = True
    # # Set per generated class.
    entry: dict = {}
    function_name = ""
    repeats = DEFAULT_REPEATS
    call_timeout = DEFAULT_TIMEOUT_S

    def start(self, ctx, done, progress):
        try:
            function, source = _callable(self.entry, self.function_name)
        except Exception:
            done(error=traceback.format_exc())
            return
        record = {"times": [], "returned": None, "error": "", "error_ms": 0.0, "hung_on": ""}
        message = f"Timing {self.function_name}..."

        def call_once(index: int) -> bool:
            """Time call `index`; False once it raised."""
            started = time.perf_counter()
            try:
                record["returned"] = function()
            except Exception:
                record["error"] = traceback.format_exc()
                record["error_ms"] = (time.perf_counter() - started) * 1000
                return False
            record["times"].append((time.perf_counter() - started) * 1000)
            return True

        # # Run by a main scan: a fast result passes quietly, so a healthy session can
        # # still show "no issues found". A deep scan shows it, to rule it out.
        quiet = not getattr(ctx, "deep", False)

        def finish():
            try:
                done(self._finding(record, source, quiet))
            except Exception:
                done(error=traceback.format_exc())

        # # #---- on Nuke's main thread: one call per timer, so the UI can update between
        if self.entry.get("main_thread"):
            def call(index: int):
                if index >= self.repeats:
                    finish()
                    return
                if progress(index / self.repeats, f"{message} (call {index + 1} of {self.repeats})"):
                    done(None)
                    return
                if not call_once(index):
                    finish()
                    return
                QtCore.QTimer.singleShot(0, lambda: call(index + 1))

            QtCore.QTimer.singleShot(0, lambda: call(0))
            return

        # # #---- in a background thread, watched for a call that never returns
        def work(watched: Watched):
            for index in range(self.repeats):
                watched.begin(f"call {index + 1} of {self.repeats}")
                ok = call_once(index)
                watched.end()
                if not ok:
                    return

        watched = Watched(work, name="whyslow-timed")

        def poll():
            try:
                if progress(min(0.95, len(record["times"]) / self.repeats), message):
                    done(None)  # # cancelled: abandon the thread
                    return
                if watched.stuck(self.call_timeout):
                    record["hung_on"] = watched.step
                    finish()
                    return
                if not watched.done:
                    QtCore.QTimer.singleShot(50, poll)
                    return
                if watched.error is not None:  # # SystemExit and the like
                    record["error"] = repr(watched.error)
                finish()
            except Exception:
                done(error=traceback.format_exc())

        watched.thread.start()
        QtCore.QTimer.singleShot(50, poll)

    # # #------------------------------------------------------------------------

    def _finding(self, record: dict, source: str, quiet: bool = False):
        entry, name = self.entry, self.function_name
        times = record["times"]
        lines = [f"# {name}", source.rstrip(), ""]
        if times:
            lines.append(f"# Calls: {', '.join(ms(t) for t in times)}")

        if record["hung_on"]:
            severity = Severity.CRITICAL
            metric = f"no answer in {self.call_timeout:g} s"
            result = f"{name} didn't answer within {self.call_timeout:g} s ({record['hung_on']})."
            detail = metric
            lines.append(f"# No answer within {self.call_timeout:g} s on {record['hung_on']}; "
                         "the call was abandoned.")
        elif record["error"]:
            severity = Severity.coerce(entry.get("error_severity") or DEFAULT_ERROR_SEVERITY)
            last = record["error"].strip().splitlines()[-1]
            metric = "failed"
            result = f"{name} failed after {ms(record['error_ms'])}: {last}"
            detail = f"raised after {ms(record['error_ms'])}: {last}"
            lines += ["# Raised:", record["error"].rstrip()]
        else:
            median = statistics.median(times)
            severity = self.severity_for(int(median))
            metric = ms(median)
            result = f"{name} took {ms(median)}"
            if len(times) > 1:
                # # A much slower first call is usually connecting or importing.
                first = f"; the first took {ms(times[0])}" if times[0] > median * FIRST_CALL_FACTOR else ""
                result += f" (median of {len(times)} calls{first})"
            result += "."
            detail = ", ".join(ms(t) for t in times)
            lines.append(f"# Returned: {_short(record['returned'])}")
            if quiet and severity == Severity.INFO:
                return None

        where = ("on Nuke's main thread" if entry.get("main_thread")
                 else "in a background thread, so it couldn't freeze Nuke")
        method = (f"Ran {name} (config.EXTRA_TIMED_FUNCTIONS) up to {self.repeats} "
                  f"time{'s' if self.repeats != 1 else ''}, {where}, and timed each call.")
        return self.finding(
            severity=severity,
            metric=metric,
            summary=" ".join(p for p in (result, entry.get("summary")) if p),
            whats_happening=" ".join(p for p in (entry.get("what"), method) if p),
            why_slow=entry.get("why") or DEFAULT_WHY,
            how_to_fix=entry.get("how") or DEFAULT_HOW,
            items=[FindingItem(label=name, kind="Timed function", detail=detail, code="\n".join(lines))],
        )


# # #----------------------------------------------------------------------------
# # Generated from config
# # #----------------------------------------------------------------------------

_GENERATED: list[str] = []


def register_timed_checks() -> None:
    """(Re)build one check per config.EXTRA_TIMED_FUNCTIONS entry.

    Runs at import. Call it again after changing the config at runtime.
    """
    while _GENERATED:
        unregister(_GENERATED.pop())
    for index, raw in enumerate(config.EXTRA_TIMED_FUNCTIONS):
        entry = _entry_dict(raw)
        name = _entry_name(entry, index)
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or str(index + 1)
        check_id = f"timed_{slug}"
        if check_id in _GENERATED:
            check_id = f"{check_id}_{index + 1}"
        severity = entry.get("severity")
        if not severity:
            steps = DEFAULT_STEPS
        elif isinstance(severity, (tuple, list)):
            steps = tuple(severity)
        else:
            steps = ((0, severity),)
        repeats = max(1, int(entry.get("repeats") or DEFAULT_REPEATS))
        call_timeout = float(entry.get("timeout") or DEFAULT_TIMEOUT_S)
        cls = type(f"Timed_{slug}", (_TimedFunction,), {
            "id": check_id,
            "title": f"Timed: {name}",
            "description": f"Runs and times {name} (config.EXTRA_TIMED_FUNCTIONS).",
            "progress_message": f"Timing {name}...",
            "order": 69 + index * 0.01,
            "severity_steps": steps,
            "entry": entry,
            "function_name": name,
            "repeats": repeats,
            "call_timeout": call_timeout,
            # # deep_scan_only False: the main scan runs it too (see config).
            "deep": bool(entry.get("deep_scan_only", True)),
            # # The runner's limit for the whole card: every call, plus slack.
            "timeout": call_timeout * repeats + 10.0,
        })
        register(cls)
        _GENERATED.append(cls.id)


register_timed_checks()
