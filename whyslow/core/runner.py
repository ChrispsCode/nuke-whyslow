"""
Finds the checks, runs each one in isolation, and gathers the results into a
Report. A check that fails to import or raises becomes an "error" result
rather than stopping the scan.
"""

from __future__ import annotations

import importlib
import pkgutil
import time
import traceback
from dataclasses import dataclass, field

from whyslow import config, logger
from whyslow.core.check import Check, registered_checks
from whyslow.core.finding import Finding, Severity

STATUS_ISSUE = "issue"
STATUS_PASSED = "passed"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"


@dataclass
class CheckResult:
    check_id: str
    title: str
    category: str
    description: str
    status: str
    finding: Finding | None = None
    error: str = ""          # # traceback text for STATUS_ERROR
    duration: float = 0.0    # # seconds
    note: str = ""           # # why it was skipped, for STATUS_SKIPPED

    @property
    def severity(self) -> Severity | None:
        return self.finding.severity if self.finding else None


@dataclass
class Report:
    script_name: str
    nuke_version: str
    node_count: int
    duration: float
    results: list[CheckResult] = field(default_factory=list)
    deep: bool = False
    cancelled: bool = False

    @property
    def issues(self) -> list[CheckResult]:
        """Results with findings, most severe first."""
        found = [r for r in self.results if r.status == STATUS_ISSUE]
        return sorted(found, key=lambda r: -int(r.finding.severity))

    def with_status(self, status: str) -> list[CheckResult]:
        return [r for r in self.results if r.status == status]


# # #----------------------------------------------------------------------------
# # Discovery
# # #----------------------------------------------------------------------------

def discover_checks() -> list[CheckResult]:
    """Import every module in whyslow.checks so their @register calls run.

    Returns an error result for each module that failed to import.
    """
    from whyslow import checks as checks_pkg

    errors = []
    for module_info in pkgutil.iter_modules(checks_pkg.__path__):
        if module_info.name.startswith("_"):
            continue
        module_name = f"{checks_pkg.__name__}.{module_info.name}"
        try:
            importlib.import_module(module_name)
        except Exception:
            logger.error(f"Check module {module_name} failed to import")
            errors.append(CheckResult(
                check_id=module_info.name, title=f"{module_info.name} (failed to load)",
                category="", description=f"Check module {module_name}",
                status=STATUS_ERROR, error=traceback.format_exc(),
            ))
    return errors


# # #----------------------------------------------------------------------------
# # Running
# # #----------------------------------------------------------------------------

def _skip_reason(check: Check, ctx, deep: bool) -> str:
    """Why `check` can't run in this scan, or "" if it can."""
    missing = check.needs - ctx.provides
    if missing:
        return "needs the Nuke GUI" if missing == {"gui"} else f"not available here ({', '.join(sorted(missing))})"
    if check.deep and not deep:
        return "deep scan only"
    return ""


def _new_result(check: Check, status: str = STATUS_PASSED, note: str = "") -> CheckResult:
    return CheckResult(check.id, check.title, check.category, check.description, status, note=note)


def _apply_finding(check: Check, result: CheckResult, finding: Finding | None) -> None:
    if finding is None:
        return
    override = config.SEVERITY_OVERRIDES.get(check.id)
    if override is not None and not isinstance(override, (tuple, list)):
        finding.severity = Severity.coerce(override)
    result.status = STATUS_ISSUE
    result.finding = finding


def run_check(check: Check, ctx, deep: bool = False) -> CheckResult:
    """Run one synchronous check. Async checks are skipped here."""
    reason = _skip_reason(check, ctx, deep)
    if not reason and check.is_async:
        reason = "runs from the report window's deep scan"
    if reason:
        return _new_result(check, STATUS_SKIPPED, reason)

    result = _new_result(check)
    start = time.perf_counter()
    try:
        finding = check.run(ctx)
    except Exception:
        logger.error(f"Check '{check.id}' raised an error")
        result.status = STATUS_ERROR
        result.error = traceback.format_exc()
        finding = None
    result.duration = time.perf_counter() - start
    _apply_finding(check, result, finding)
    return result


def _sorted_checks() -> list[Check]:
    return sorted((cls() for cls in registered_checks()), key=lambda c: (c.order, c.title))


def _run_sync(checks, ctx, deep, progress, results, total) -> bool:
    """Run `checks` in order, appending to results. Returns True if cancelled."""
    cancelled = False
    for index, check in enumerate(checks):
        if not cancelled and progress is not None:
            message = check.progress_message or f"Checking: {check.title}"
            cancelled = bool(progress(index / total, message))
        if cancelled:
            results.append(_new_result(check, STATUS_SKIPPED, "scan cancelled"))
            continue
        results.append(run_check(check, ctx, deep))
    return cancelled


def _report(ctx, start, results, deep, cancelled) -> Report:
    return Report(
        script_name=ctx.script_name,
        nuke_version=ctx.nuke_version,
        node_count=len(ctx.all_nodes),
        duration=time.perf_counter() - start,
        results=results,
        deep=deep,
        cancelled=cancelled,
    )


def run_checks(ctx=None, deep: bool = False, progress=None) -> Report:
    """Run every registered check against `ctx` (the live session by default).

    `deep` also runs the slow checks. `progress(fraction, message)` is called
    before each check; if it returns True the scan is cancelled and the
    remaining checks are marked skipped. Async checks are skipped: use
    run_checks_async for those.
    """
    if ctx is None:
        from whyslow.core.context import ScanContext
        ctx = ScanContext()
    start = time.perf_counter()
    results = discover_checks()
    checks = _sorted_checks()
    cancelled = _run_sync(checks, ctx, deep, progress, results, len(checks))
    if progress is not None and not cancelled:
        progress(1.0, "Done")
    return _report(ctx, start, results, deep, cancelled)


def run_checks_async(on_finished, ctx=None, deep: bool = False, progress=None) -> None:
    """Like run_checks, but also runs AsyncChecks, which need Nuke's main loop.

    Returns straight away. The synchronous checks run first, then each async
    check in turn; on_finished(report) is called (from a Qt timer) at the end.
    Each async check is given its `timeout`, after which it becomes an error.
    """
    from whyslow.qt import QtCore

    if ctx is None:
        from whyslow.core.context import ScanContext
        ctx = ScanContext()
    start = time.perf_counter()
    results = discover_checks()
    checks = _sorted_checks()
    total = len(checks)
    sync_checks = [c for c in checks if not c.is_async]
    async_checks = [c for c in checks if c.is_async]
    state = {"cancelled": _run_sync(sync_checks, ctx, deep, progress, results, total)}

    def finish():
        if progress is not None and not state["cancelled"]:
            progress(1.0, "Done")
        on_finished(_report(ctx, start, results, deep, state["cancelled"]))

    def run_next(index: int) -> None:
        if index >= len(async_checks):
            finish()
            return
        check = async_checks[index]
        position = len(sync_checks) + index
        reason = "scan cancelled" if state["cancelled"] else _skip_reason(check, ctx, deep)
        if reason:
            results.append(_new_result(check, STATUS_SKIPPED, reason))
            run_next(index + 1)
            return

        result = _new_result(check)
        check_start = time.perf_counter()
        handled = {"done": False}

        def check_progress(fraction: float, message: str = "") -> bool:
            if progress is None:
                return False
            overall = (position + max(0.0, min(1.0, fraction))) / total
            if progress(overall, message or check.progress_message or f"Checking: {check.title}"):
                state["cancelled"] = True
            return state["cancelled"]

        def done(finding: Finding | None = None, error: str = "") -> None:
            if handled["done"]:
                return  # # late call after a timeout
            handled["done"] = True
            result.duration = time.perf_counter() - check_start
            if error:
                logger.error(f"Check '{check.id}' raised an error")
                result.status = STATUS_ERROR
                result.error = error
            elif state["cancelled"] and finding is None:
                result.status = STATUS_SKIPPED
                result.note = "scan cancelled"
            else:
                _apply_finding(check, result, finding)
            results.append(result)
            QtCore.QTimer.singleShot(0, lambda: run_next(index + 1))

        def timed_out() -> None:
            if not handled["done"]:
                done(error=f"Timed out after {check.timeout:g} s without finishing.")

        check_progress(0.0)
        QtCore.QTimer.singleShot(int(check.timeout * 1000), timed_out)
        try:
            check.start(ctx, done, check_progress)
        except Exception:
            done(error=traceback.format_exc())

    QtCore.QTimer.singleShot(0, lambda: run_next(0))
