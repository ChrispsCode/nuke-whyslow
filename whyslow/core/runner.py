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

def run_check(check: Check, ctx, deep: bool = False) -> CheckResult:
    result = CheckResult(check.id, check.title, check.category, check.description, STATUS_PASSED)
    missing = check.needs - ctx.provides
    if missing:
        result.status = STATUS_SKIPPED
        result.note = "needs the Nuke GUI" if missing == {"gui"} else f"not available here ({', '.join(sorted(missing))})"
        return result
    if check.deep and not deep:
        result.status = STATUS_SKIPPED
        result.note = "deep scan only"
        return result

    start = time.perf_counter()
    try:
        finding = check.run(ctx)
    except Exception:
        logger.error(f"Check '{check.id}' raised an error")
        result.status = STATUS_ERROR
        result.error = traceback.format_exc()
        finding = None
    result.duration = time.perf_counter() - start

    if finding is not None:
        override = config.SEVERITY_OVERRIDES.get(check.id)
        if override is not None and not isinstance(override, (tuple, list)):
            finding.severity = Severity.coerce(override)
        result.status = STATUS_ISSUE
        result.finding = finding
    return result


def run_checks(ctx=None, deep: bool = False, progress=None) -> Report:
    """Run every registered check against `ctx` (the live session by default).

    `deep` also runs the slow checks. `progress(fraction, message)` is called
    before each check; if it returns True the scan is cancelled and the
    remaining checks are marked skipped.
    """
    if ctx is None:
        from whyslow.core.context import ScanContext
        ctx = ScanContext()

    start = time.perf_counter()
    results = discover_checks()
    checks = sorted((cls() for cls in registered_checks()), key=lambda c: (c.order, c.title))
    cancelled = False
    for index, check in enumerate(checks):
        if not cancelled and progress is not None:
            message = check.progress_message or f"Checking: {check.title}"
            cancelled = bool(progress(index / len(checks), message))
        if cancelled:
            results.append(CheckResult(check.id, check.title, check.category, check.description,
                                       STATUS_SKIPPED, note="scan cancelled"))
            continue
        results.append(run_check(check, ctx, deep))
    if progress is not None and not cancelled:
        progress(1.0, "Done")

    return Report(
        script_name=ctx.script_name,
        nuke_version=ctx.nuke_version,
        node_count=len(ctx.all_nodes),
        duration=time.perf_counter() - start,
        results=results,
        deep=deep,
        cancelled=cancelled,
    )
