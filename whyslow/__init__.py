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
    """Run the checks against the open script and return the Report.

    Synchronous, so the probes that need Nuke's main loop (the async deep-scan
    checks) are skipped; use report(deep=True) or the report window for those.
    """
    from whyslow.core import runner
    from whyslow.progress import NukeProgress
    with NukeProgress("Why Slow? scan") as progress:
        return runner.run_checks(deep=deep, progress=progress)


def report(print_report: bool = True, deep: bool = False):
    """Run the checks and print (and return) a plain-text report.

    In the GUI, deep=True runs asynchronously: it returns None straight away
    and prints the report when the probes finish.
    """
    from whyslow.core import runner, text_report

    if deep:
        import nuke
        if nuke.GUI:
            from whyslow.progress import NukeProgress
            progress = NukeProgress("Why Slow? deep scan")

            def finished(result):
                progress.close()
                print(text_report.format_report(result))

            runner.run_checks_async(finished, deep=True, progress=progress)
            return None

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
