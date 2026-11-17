"""
The base class every check derives from, and the @register decorator.

To add a check, drop a module into whyslow/checks/ containing:

    from whyslow.core.check import Check, register
    from whyslow.core.finding import Severity

    @register
    class MyCheck(Check):
        id = "my_check"
        title = "Short card heading"
        category = "Script"
        description = "One line on what this looks for (shown when it passes)."
        severity_steps = ((1, Severity.MEDIUM), (10, Severity.HIGH))

        def run(self, ctx):
            hits = [...]
            if not hits:
                return None
            return self.finding(severity=self.severity_for(len(hits)), summary=..., ...)

The registry finds it on the next scan; nothing else needs editing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from whyslow import config
from whyslow.core.finding import Finding, Severity

if TYPE_CHECKING:
    from whyslow.core.context import ScanContext


# # #----------------------------------------------------------------------------
# # Categories (also the order sections are grouped in text reports)
# # #----------------------------------------------------------------------------

CATEGORY_SCRIPT = "Script"
CATEGORY_PREFERENCES = "Preferences"
CATEGORY_SESSION = "Session"

# # What a scan context can supply. A check lists what it needs, so a future
# # context that can't supply it (a script on disk has no live session) skips
# # the check rather than failing it.
NEEDS_SCRIPT = "script"
NEEDS_PREFS = "prefs"
NEEDS_SESSION = "session"
# # Needs Nuke's Qt UI (probes that trigger widgets and watch what runs).
NEEDS_GUI = "gui"


class Check:
    """One kind of slowdown: knows what to look for and how to describe it."""

    id: str = ""
    title: str = ""
    category: str = CATEGORY_SCRIPT
    description: str = ""
    needs: frozenset[str] = frozenset({NEEDS_SCRIPT})
    # # Deep checks are slow (e.g. watching Nuke idle for a while), so they only
    # # run when a deep scan is asked for.
    deep: bool = False
    # # Shown in the progress bar while this check runs.
    progress_message: str = ""
    # # True for AsyncCheck subclasses.
    is_async: bool = False
    # # Lower runs (and lists) first among equal severities.
    order: int = 100
    # # Count-based default severity: the highest step whose threshold the count
    # # reaches wins. Overridable per check id in config.SEVERITY_OVERRIDES.
    severity_steps: tuple[tuple[int, Severity], ...] = ((1, Severity.MEDIUM),)

    def run(self, ctx: ScanContext) -> Finding | None:
        """Return a Finding if something was found, otherwise None (passed)."""
        raise NotImplementedError

    # # #------------------------------------------------------------------------
    # # Helpers for subclasses
    # # #------------------------------------------------------------------------

    def severity_for(self, count: int) -> Severity:
        """Map a count to a severity through the (possibly overridden) steps."""
        steps = self.severity_steps
        override = config.SEVERITY_OVERRIDES.get(self.id)
        if isinstance(override, (tuple, list)):
            steps = override
        result = Severity.INFO
        for threshold, severity in sorted(steps, key=lambda step: step[0]):
            if count >= threshold:
                result = Severity.coerce(severity)
        return result

    def finding(self, **kwargs) -> Finding:
        """Build a Finding with this check's id and title filled in."""
        kwargs.setdefault("check_id", self.id)
        kwargs.setdefault("title", self.title)
        return Finding(**kwargs)


class AsyncCheck(Check):
    """A check that needs Nuke's main loop to run while it works.

    Nuke holds back its own UI updates (Node Graph redraws, panel refreshes,
    frame-change work) until the running Python command returns, so probes of
    those can't run inside a scan's Python call. Instead start() sets things
    going (usually with QTimers) and returns, and calls done() later. Only
    runner.run_checks_async runs these; the synchronous runner skips them.
    """

    is_async = True
    # # Seconds before the runner gives up on done() and reports an error.
    timeout: float = 60.0

    def start(self, ctx: ScanContext, done, progress) -> None:
        """Begin the probe. Call done(finding_or_None) once, or done(error=traceback_text).

        progress(fraction, message) updates the progress bar for this check
        (fraction 0-1 within the check) and returns True if the user cancelled.
        """
        raise NotImplementedError

    def run(self, ctx):
        raise RuntimeError(f"{self.id} is asynchronous: use runner.run_checks_async")


# # #----------------------------------------------------------------------------
# # Registry
# # #----------------------------------------------------------------------------

_REGISTRY: dict[str, type[Check]] = {}


def register(cls: type[Check]) -> type[Check]:
    """Class decorator that adds a check to the registry, keyed by its id.

    Re-registering an id replaces the old class, so reloading a check module
    during development picks up the new code.
    """
    if not cls.id:
        raise ValueError(f"{cls.__name__} has no id")
    _REGISTRY[cls.id] = cls
    return cls


def unregister(check_id: str) -> None:
    """Remove a check (used when regenerating checks built from config)."""
    _REGISTRY.pop(check_id, None)


def registered_checks() -> list[type[Check]]:
    return list(_REGISTRY.values())
