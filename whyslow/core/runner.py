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

def run_check(check: Check, ctx) -> CheckResult:
    result = CheckResult(check.id, check.title, check.category, check.description, STATUS_PASSED)
    if not check.needs <= ctx.provides:
        result.status = STATUS_SKIPPED
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


def run_checks(ctx=None) -> Report:
    """Run every registered check against `ctx` (the live session by default)."""
    if ctx is None:
        from whyslow.core.context import ScanContext
        ctx = ScanContext()

    start = time.perf_counter()
    results = discover_checks()
    checks = sorted((cls() for cls in registered_checks()), key=lambda c: (c.order, c.title))
    results += [run_check(check, ctx) for check in checks]

    return Report(
        script_name=ctx.script_name,
        nuke_version=ctx.nuke_version,
        node_count=len(ctx.all_nodes),
        duration=time.perf_counter() - start,
        results=results,
    )
