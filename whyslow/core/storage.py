"""
Storage latency probe: which filers the script and session depend on, and how
long each takes to answer.

Everything that touches the disk runs in daemon threads, because a stat() on a
hung filer can block forever: the main thread only polls. A thread stuck on a
dead filer is abandoned (it ends whenever the filer answers); its filer is
reported as not answering.

No Qt and no nuke here, so it can be tested anywhere.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

KIND_SEQUENCE = "sequence"        # # a file-per-frame Read: opened on every frame change
KIND_FILE = "file"                # # a single file a node reads (a movie, a geo, a LUT)
KIND_ENVIRONMENT = "environment"  # # the script, autosave, sys.path, plugin path, OCIO, caches

READ_BYTES = 64 * 1024            # # about a header's worth


@dataclass
class Use:
    """One thing the session reads from disk."""

    kind: str
    source: str                   # # "Read1.file", "sys.path", "preferences.DiskCachePath"...
    directory: str                # # the folder it lives in (what gets resolved)
    samples: list[str] = field(default_factory=list)   # # files to time, best first
    node_path: str = ""


@dataclass
class Mount:
    """A filesystem from /proc/mounts (or a guess when it can't be read)."""

    point: str
    fstype: str = ""
    device: str = ""


@dataclass
class FilerResult:
    mount: Mount
    uses: list[Use] = field(default_factory=list)
    stat_ms: list[float] = field(default_factory=list)
    open_ms: list[float] = field(default_factory=list)
    hung: bool = False            # # an operation didn't finish within the timeout
    hung_on: str = ""             # # what it was doing then
    note: str = ""                # # e.g. "no file could be opened"

    @property
    def open_median(self) -> float | None:
        return _median(self.open_ms) if self.open_ms else None

    @property
    def stat_median(self) -> float | None:
        return _median(self.stat_ms) if self.stat_ms else None

    @property
    def latency(self) -> float | None:
        """The figure severity is judged on: open + read, else stat."""
        return self.open_median if self.open_ms else self.stat_median

    def count(self, kind: str) -> int:
        return sum(1 for use in self.uses if use.kind == kind)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


# # #----------------------------------------------------------------------------
# # Mounts
# # #----------------------------------------------------------------------------

def _unescape(text: str) -> str:
    """/proc/mounts writes spaces etc. as octal escapes (\\040)."""
    return text.encode().decode("unicode_escape") if "\\" in text else text


def read_mounts(proc_mounts: str = "/proc/mounts") -> list[Mount]:
    """Mounted filesystems, longest mount point first. Reading this file is
    served by the kernel, so it never waits on a filer.

    When several are mounted on the same point (systemd's autofs placeholder,
    then the real NFS mount on top), the last one listed is the one in use.
    """
    by_point: dict[str, Mount] = {}
    try:
        with open(proc_mounts) as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 3:
                    point = _unescape(parts[1])
                    by_point[point] = Mount(point=point, fstype=parts[2], device=_unescape(parts[0]))
    except OSError:
        pass
    return sorted(by_point.values(), key=lambda m: -len(m.point))


def mount_for(path: str, mounts: list[Mount]) -> Mount:
    """The mount a (real) path lives on. Without mount info, its first folder."""
    for mount in mounts:
        point = mount.point.rstrip("/") or "/"
        if point == "/" or path == point or path.startswith(point + "/"):
            return mount
    parts = [p for p in path.split("/") if p]
    return Mount(point="/" + parts[0] if parts else "/")


# # #----------------------------------------------------------------------------
# # The probe
# # #----------------------------------------------------------------------------

class _Watched:
    """Runs fn in a daemon thread and records when its current step started,
    so the poller can tell a slow filer from a hung one."""

    def __init__(self, fn):
        self.fn = fn
        self.step = ""
        self.step_started: float | None = None
        self.done = False
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="whyslow-storage")

    def begin(self, step: str) -> None:
        self.step, self.step_started = step, time.perf_counter()

    def end(self) -> None:
        self.step_started = None

    def _run(self) -> None:
        try:
            self.fn(self)
        except BaseException as error:  # # never let a probe thread raise into Nuke
            self.error = error
        finally:
            self.step_started = None
            self.done = True

    def stuck(self, timeout: float) -> bool:
        started = self.step_started
        return started is not None and time.perf_counter() - started > timeout


class StorageProbe:
    """Resolve every directory in `uses` to its real filer, then time each filer.

    start() returns at once; call poll() (cheap, never blocks) until it
    returns True, then read results(). A step running longer than
    `op_timeout` seconds counts as no answer.
    """

    def __init__(self, uses: list[Use], op_timeout: float = 2.0, repeats: int = 3,
                 max_threads: int = 16, mounts: list[Mount] | None = None):
        self.uses = uses
        self.op_timeout = op_timeout
        self.repeats = repeats
        self.mounts = mounts if mounts is not None else read_mounts()
        self._slots = threading.Semaphore(max_threads)
        self._resolvers: dict[str, _Watched] = {}
        self._real: dict[str, str] = {}
        self._timers: dict[str, tuple[_Watched, FilerResult]] = {}
        self._phase = "new"
        self._phase_started = 0.0
        self._filers: dict[str, FilerResult] = {}

    # # #------------------------------------------------------------------------

    def start(self) -> None:
        for directory in sorted({use.directory for use in self.uses}):
            watched = _Watched(lambda w, d=directory: self._resolve(w, d))
            self._resolvers[directory] = watched
            watched.thread.start()
        self._phase = "resolving"
        self._phase_started = time.perf_counter()

    def _phase_overdue(self, steps: int) -> bool:
        """Past the phase's deadline: covers threads still queued for a slot
        behind ones stuck on a dead filer."""
        return time.perf_counter() - self._phase_started > self.op_timeout * steps

    def _resolve(self, watched: _Watched, directory: str) -> None:
        with self._slots:
            watched.begin("resolving")
            self._real[directory] = os.path.realpath(directory)
            watched.end()

    def _measure(self, watched: _Watched, result: FilerResult) -> None:
        samples = [s for use in result.uses for s in use.samples]
        directories = [use.directory for use in result.uses]
        with self._slots:
            sample = None
            for candidate in samples[:20]:
                watched.begin(f"stat {candidate}")
                started = time.perf_counter()
                try:
                    exists = os.path.isfile(candidate)
                finally:
                    # # A missing file still costs a round trip: it's a valid sample.
                    result.stat_ms.append((time.perf_counter() - started) * 1000)
                    watched.end()
                if exists:
                    sample = candidate
                    break
            if sample is None:
                result.note = "no file the script uses could be opened here; timed folder lookups instead"
                for _ in range(self.repeats):
                    for directory in directories[:1]:
                        watched.begin(f"stat {directory}")
                        started = time.perf_counter()
                        try:
                            os.stat(directory)
                        except OSError:
                            pass
                        result.stat_ms.append((time.perf_counter() - started) * 1000)
                        watched.end()
                return
            for _ in range(self.repeats):
                watched.begin(f"stat {sample}")
                started = time.perf_counter()
                try:
                    os.stat(sample)
                except OSError:
                    pass
                result.stat_ms.append((time.perf_counter() - started) * 1000)
                watched.begin(f"open {sample}")
                started = time.perf_counter()
                try:
                    with open(sample, "rb") as handle:
                        handle.read(READ_BYTES)
                except OSError as error:
                    result.note = f"couldn't read {sample}: {error.strerror or error}"
                    watched.end()
                    return
                result.open_ms.append((time.perf_counter() - started) * 1000)
                watched.end()

    # # #------------------------------------------------------------------------

    def poll(self) -> bool:
        """Advance the probe; True once every filer is measured or given up on."""
        if self._phase == "resolving":
            pending = [d for d, w in self._resolvers.items() if not w.done and not w.stuck(self.op_timeout)]
            if pending and not self._phase_overdue(3):
                return False
            self._start_measuring()
            return False
        if self._phase == "measuring":
            overdue = self._phase_overdue(self.repeats * 2 + 3)
            for watched, result in self._timers.values():
                if not watched.done and not result.hung and (watched.stuck(self.op_timeout) or overdue):
                    result.hung = True
                    result.hung_on = watched.step or "waiting for a free probe thread"
            return all(w.done or r.hung for w, r in self._timers.values())
        return self._phase == "done"

    def _start_measuring(self) -> None:
        filers: dict[str, FilerResult] = {}
        for use in self.uses:
            real = self._real.get(use.directory)
            if real is None:  # # realpath never came back: the filer is hung
                mount = mount_for(os.path.normpath(use.directory), self.mounts)
                key = "unresolved:" + mount.point
                result = filers.setdefault(key, FilerResult(mount=mount, hung=True,
                                                            hung_on=f"resolving {use.directory}"))
            else:
                mount = mount_for(real, self.mounts)
                result = filers.setdefault(mount.point, FilerResult(mount=mount))
                # # Point the samples at the real path, so they hit the same filer.
                if real != use.directory:
                    use = Use(use.kind, use.source, real,
                              [real + s[len(use.directory):] if s.startswith(use.directory) else s
                               for s in use.samples], use.node_path)
            result.uses.append(use)
        for key, result in filers.items():
            if result.hung:
                continue
            watched = _Watched(lambda w, r=result: self._measure(w, r))
            self._timers[key] = (watched, result)
            watched.thread.start()
        self._filers = filers
        self._phase = "measuring"
        self._phase_started = time.perf_counter()

    def results(self) -> list[FilerResult]:
        """Every filer, the slowest (or hung) first."""
        filers = list(self._filers.values())
        return sorted(filers, key=lambda r: (not r.hung, -(r.latency or 0.0)))
