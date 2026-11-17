"""
Slow storage (deep scan): which filers the script and session read from, and
how long each takes to answer.

Why it matters to the GUI (measured, see NOTES.md): with the Viewer on a
file-per-frame sequence, Nuke opens each frame's file on the UI thread, so a
slow filer freezes the UI on every frame change (about 2.7 s per frame at
300 ms per filesystem operation). Single-file clips are opened once.

Paths are collected on the main thread (knob evaluation only, no disk
access); everything that touches the disk runs in core.storage's background
threads, so a hung filer can't freeze Nuke during the diagnosis.
"""

from __future__ import annotations

import os
import re
import sys
import time
import traceback

import nuke

from whyslow import config
from whyslow.checks._helpers import ms, plural
from whyslow.core import storage
from whyslow.core.check import CATEGORY_SESSION, NEEDS_GUI, NEEDS_SESSION, AsyncCheck, register
from whyslow.core.finding import FindingItem, Severity
from whyslow.qt import QtCore

_SEQUENCE = re.compile(r"#+|%0?\d*d")


# # #----------------------------------------------------------------------------
# # What the session reads
# # #----------------------------------------------------------------------------

def _evaluate(knob, frame: int) -> str:
    """The knob's path at `frame` (evaluate() alone gives frame 0)."""
    try:
        value = knob.evaluate(frame)
    except Exception:
        value = None
    return (value or knob.value() or "").strip()


def _absolute(path: str) -> bool:
    return bool(path) and os.path.isabs(path)


def collect_uses(ctx) -> list[storage.Use]:
    """Every file the script's nodes read, plus the session's own paths."""
    frame = int(nuke.frame())
    uses: list[storage.Use] = []

    # # #---- nodes (any File_Knob, so Reads, DeepReads, ReadGeos, LUTs, gizmo internals...)
    for node in ctx.all_nodes:
        if node.Class() in config.STORAGE_IGNORE_CLASSES:
            continue
        disable = node.knob("disable")
        if disable is not None and disable.value():
            continue
        for knob in node.allKnobs():
            if knob.Class() != "File_Knob":
                continue
            raw = (knob.value() or "").strip()
            if not raw:
                continue
            if _SEQUENCE.search(raw):
                kind = storage.KIND_SEQUENCE
                samples = [_evaluate(knob, f) for f in (frame, frame + 1, frame + 2)]
            else:
                kind = storage.KIND_FILE
                samples = [_evaluate(knob, frame)]
            samples = [s for s in samples if _absolute(s)]
            if not samples:
                continue
            uses.append(storage.Use(kind, f"{node.fullName()}.{knob.name()}", os.path.dirname(samples[0]),
                                    samples, node.fullName()))

    # # #---- the session
    def add_file(source: str, path: str) -> None:
        if _absolute(path):
            uses.append(storage.Use(storage.KIND_ENVIRONMENT, source, os.path.dirname(path), [path]))

    def add_folder(source: str, path: str) -> None:
        if _absolute(path):
            folder = os.path.dirname(path) if path.endswith(".zip") else path
            uses.append(storage.Use(storage.KIND_ENVIRONMENT, source, folder.rstrip("/") or "/"))

    script = nuke.root().name()
    if script and script != "Root":
        add_file("script", script)
    for holder, name in ((nuke.root(), "root"), (nuke.toNode("preferences"), "preferences")):
        if holder is None:
            continue
        for knob in holder.allKnobs():
            if knob.Class() in ("File_Knob", "EvalString_Knob") and (knob.Class() == "File_Knob"
                                                                     or knob.name() == "AutoSaveName"):
                path = _evaluate(knob, frame)
                if knob.name() in ("DiskCachePath", "TimelineDiskCachePath", "localCachePath",
                                   "autoLocalCachePath", "project_directory", "DefaultProjectDirectory",
                                   "DefaultProjectCustomExportDirectory", "syncReviewProjectSaveDir"):
                    add_folder(f"{name}.{knob.name()}", path)
                else:
                    add_file(f"{name}.{knob.name()}", path)
    add_file("$OCIO", os.environ.get("OCIO", ""))
    for entry in sys.path:
        add_folder("sys.path", entry)
    for entry in nuke.pluginPath():
        add_folder("plugin path", entry)
    return uses


# # #----------------------------------------------------------------------------
# # The check
# # #----------------------------------------------------------------------------

@register
class SlowStorage(AsyncCheck):
    id = "storage"
    title = "Slow storage"
    category = CATEGORY_SESSION
    description = ("Times every filer the script and session read from, in the background "
                   "(a stat and an open + 64 KB read of a file the script uses).")
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    deep = True
    progress_message = "Timing the filers the script reads from..."
    order = 67
    timeout = 60.0
    # # ms per file open (+ 64 KB read) on a filer -> severity. Below the first
    # # step a filer counts as healthy. A filer that doesn't answer is Critical.
    severity_steps = ((5, Severity.LOW), (20, Severity.MEDIUM), (100, Severity.HIGH), (1000, Severity.CRITICAL))

    def start(self, ctx, done, progress):
        try:
            uses = collect_uses(ctx)
        except Exception:
            done(error=traceback.format_exc())
            return
        if not uses:
            done(None)
            return
        probe = storage.StorageProbe(uses, op_timeout=config.STORAGE_OP_TIMEOUT_S,
                                     repeats=config.STORAGE_REPEATS)
        expected = config.STORAGE_OP_TIMEOUT_S * (config.STORAGE_REPEATS * 2 + 6)
        started = time.perf_counter()

        def poll():
            try:
                if progress(min(0.95, (time.perf_counter() - started) / expected)):
                    done(None)  # # cancelled: abandon the probe threads
                    return
                if not probe.poll():
                    QtCore.QTimer.singleShot(50, poll)
                    return
                done(self._finding(probe.results()))
            except Exception:
                done(error=traceback.format_exc())

        probe.start()
        QtCore.QTimer.singleShot(50, poll)

    # # #------------------------------------------------------------------------

    def _filer_severity(self, result: storage.FilerResult) -> Severity:
        if result.hung:
            return Severity.CRITICAL
        severity = self.severity_for(int(result.latency or 0))
        # # Without sequences on it, a slow filer only costs on open, not every frame.
        if not result.count(storage.KIND_SEQUENCE) and severity > Severity.LOW:
            severity = Severity(severity - 1)
        return severity

    def _is_slow(self, result: storage.FilerResult) -> bool:
        return result.hung or self.severity_for(int(result.latency or 0)) >= Severity.LOW

    @staticmethod
    def _speed(result: storage.FilerResult) -> str:
        if result.hung:
            return f"no answer within {config.STORAGE_OP_TIMEOUT_S:g} s"
        if result.open_median is not None:
            return f"{ms(result.open_median)} per file open"
        if result.stat_median is not None:
            return f"{ms(result.stat_median)} per lookup"
        return "not timed"

    @staticmethod
    def _used_by(result: storage.FilerResult) -> str:
        parts = []
        for kind, word in ((storage.KIND_SEQUENCE, "sequence"), (storage.KIND_FILE, "file"),
                           (storage.KIND_ENVIRONMENT, "session path")):
            count = result.count(kind)
            if count:
                parts.append(plural(count, word))
        return ", ".join(parts) or "nothing"

    @staticmethod
    def _frame_freeze(result: storage.FilerResult) -> float | None:
        if result.count(storage.KIND_SEQUENCE) and result.open_median is not None:
            return result.open_median * config.STORAGE_FRAME_FACTOR
        return None

    def _finding(self, results: list[storage.FilerResult]):
        slow = [r for r in results if self._is_slow(r)]
        if not slow:
            return None
        severity = max(self._filer_severity(r) for r in slow)
        worst = slow[0]
        freeze = self._frame_freeze(worst)
        summary = (f"{len(slow)} of {plural(len(results), 'filer')} the session reads from "
                   f"{'is' if len(slow) == 1 else 'are'} slow. Worst: {worst.mount.point} "
                   f"({self._speed(worst)}), used by {self._used_by(worst)}.")
        if worst.hung:
            summary += " Anything in Nuke that touches it can freeze until it answers."
        elif freeze is not None:
            summary += (f" With the Viewer on a sequence there, each frame change freezes the UI "
                        f"for about {ms(freeze)}.")

        items = []
        for result in results:
            freeze_ms = self._frame_freeze(result)
            detail = f"{self._speed(result)}; {self._used_by(result)}"
            if freeze_ms is not None and self._is_slow(result):
                detail += f"; ≈{ms(freeze_ms)} UI freeze per frame with the Viewer on a sequence"
            lines = [f"# {result.mount.point}  {result.mount.fstype}  {result.mount.device}".rstrip()]
            if result.hung:
                lines.append(f"# No answer while: {result.hung_on}")
            if result.note:
                lines.append(f"# {result.note}")
            if result.open_ms:
                lines.append(f"# open + 64 KB read: {', '.join(ms(v) for v in result.open_ms)}")
            if result.stat_ms:
                lines.append(f"# stat: {', '.join(ms(v) for v in result.stat_ms)}")
            lines.append("")
            for use in result.uses:
                lines.append(f"{use.kind:12} {use.source:40} {use.samples[0] if use.samples else use.directory}")
            items.append(FindingItem(
                label=result.mount.point + (" (no answer)" if result.hung else ""),
                kind=" ".join(p for p in (result.mount.fstype, result.mount.device) if p),
                detail=detail, code="\n".join(lines)))

        return self.finding(
            severity=severity,
            metric=self._speed(worst),
            summary=summary,
            whats_happening=(
                "Every file the script's nodes read, plus the session's own paths (the script, "
                "autosave, caches, OCIO, sys.path, plugin path), was traced through symlinks to the "
                "filer it really lives on. Each filer was then timed in the background: a stat, and "
                f"an open + 64 KB read of a file the script uses, {config.STORAGE_REPEATS} times. "
                "Healthy local disks and networks answer in well under 5 ms. Rows are every filer, "
                "slowest first; select one to see what uses it."),
            why_slow=(
                "With the Viewer on a file-per-frame sequence (EXRs and the like), Nuke opens each "
                "frame's file on the UI thread, which takes several round trips to the filer, so the "
                "whole UI waits on every frame change. Measured: about 2.7 s per frame at 300 ms per "
                "round trip. Single-file clips (movies, BRAW) only wait when they're first opened, "
                "e.g. when the Viewer connects. Slow session paths can make Nuke wait too: sys.path "
                "and plugin paths on every first import, the autosave folder when it saves."),
            how_to_fix=(
                "Tell whoever looks after storage which filer it is (each row shows its mount and "
                "server). Meanwhile: keep the Viewer off sequences on that filer while working, "
                "localise them (Nuke's Localization, or a copy on local disk), and keep plugin and "
                "Python paths off slow or unreliable filers."),
            items=items,
        )
