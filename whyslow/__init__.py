"""
Why Slow? - a read-only diagnostic for what's making the Nuke GUI slow.

    import whyslow
    whyslow.show()              # the report dialog (GUI)
    whyslow.report()            # the same report printed to the Script Editor
    whyslow.report(deep=True)   # also run the slow checks

Nothing here builds Qt objects at import time: the UI is imported on demand.
"""

from __future__ import annotations

import sys

__version__ = "0.1.0"


def run(deep: bool = False):
    """Run every check against the open script (with a progress bar in the GUI)."""
    from whyslow.core import runner
    from whyslow.progress import NukeProgress
    with NukeProgress("Why Slow? deep scan" if deep else "Why Slow? scan") as progress:
        return runner.run_checks(deep=deep, progress=progress)


def report(print_report: bool = True, deep: bool = False) -> str:
    """Run every check and print (and return) a plain-text report."""
    from whyslow.core import text_report
    text = text_report.format_report(run(deep=deep))
    if print_report:
        print(text)
    return text


def show():
    """Open the report dialog, or bring the open one to the front."""
    from whyslow.ui import report_dialog
    return report_dialog.launch()


def reload_package() -> None:
    """Development helper: drop every whyslow module so the next call re-imports.

    Open dialogs keep their old code until they're closed and reopened.
    """
    for name in [m for m in sys.modules if m == __name__ or m.startswith(__name__ + ".")]:
        del sys.modules[name]
