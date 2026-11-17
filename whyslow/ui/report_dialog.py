"""The main report window: one card per issue, plus what passed."""

from __future__ import annotations

import html

from whyslow import __version__, logger
from whyslow.core import runner, text_report
from whyslow.progress import NukeProgress
from whyslow.core.finding import Severity
from whyslow.core.runner import STATUS_ERROR, STATUS_PASSED, STATUS_SKIPPED, Report
from whyslow.qt import QtGui, QtWidgets, Qt, is_valid
from whyslow.ui import theme
from whyslow.ui.details_dialog import DetailsDialog
from whyslow.ui.finding_card import FindingCard

# # One report window at a time (TMM launcher pattern).
_OPEN_WINDOWS: dict[str, QtWidgets.QWidget] = {}
_WINDOW_KEY = "report"


def launch(report: Report | None = None) -> "ReportDialog":
    """Show the report window, re-scanning if it's already open."""
    existing = _OPEN_WINDOWS.get(_WINDOW_KEY)
    if is_valid(existing):
        try:
            existing.rescan()
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except RuntimeError:
            pass

    dialog = ReportDialog(report=report, parent=QtWidgets.QApplication.activeWindow())
    _OPEN_WINDOWS[_WINDOW_KEY] = dialog
    dialog.destroyed.connect(lambda *_: _OPEN_WINDOWS.pop(_WINDOW_KEY, None))
    dialog.show()
    return dialog


class ReportDialog(QtWidgets.QDialog):

    def __init__(self, report: Report | None = None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Why Slow?  v{__version__}")
        self.setMinimumWidth(700)
        self.report: Report | None = None
        self._details: dict[str, DetailsDialog] = {}
        self._spawned = False
        self._scanning = False

        layout = QtWidgets.QVBoxLayout(self)

        # # #--------------------------------------------------------------------
        # # Header
        # # #--------------------------------------------------------------------
        header = QtWidgets.QHBoxLayout()
        header_text = QtWidgets.QVBoxLayout()
        self.script_label = QtWidgets.QLabel()
        font = self.script_label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.25)
        self.script_label.setFont(font)
        self.info_label = QtWidgets.QLabel()
        self.info_label.setStyleSheet(f"color: {theme.MUTED_TEXT};")
        self.summary_label = QtWidgets.QLabel()
        self.summary_label.setTextFormat(Qt.RichText)
        header_text.addWidget(self.script_label)
        header_text.addWidget(self.info_label)
        header_text.addWidget(self.summary_label)
        header.addLayout(header_text)
        header.addStretch(1)
        scan_buttons = QtWidgets.QVBoxLayout()
        rescan_button = QtWidgets.QPushButton("Re-scan")
        rescan_button.setToolTip("Run the quick checks again.")
        rescan_button.clicked.connect(lambda: self.rescan(deep=False))
        deep_button = QtWidgets.QPushButton("Deep scan")
        deep_button.setToolTip("Also run the slow checks, e.g. watching Nuke idle for a moment "
                               "to catch tools that keep running Python in the background.")
        deep_button.clicked.connect(lambda: self.rescan(deep=True))
        scan_buttons.addWidget(rescan_button)
        scan_buttons.addWidget(deep_button)
        scan_buttons.addStretch(1)
        header.addLayout(scan_buttons)
        layout.addLayout(header)

        # # #--------------------------------------------------------------------
        # # Cards
        # # #--------------------------------------------------------------------
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)

        # # #--------------------------------------------------------------------
        # # Passed / skipped, collapsed by default
        # # #--------------------------------------------------------------------
        self.passed_toggle = QtWidgets.QToolButton()
        self.passed_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.passed_toggle.setArrowType(Qt.RightArrow)
        self.passed_toggle.setCheckable(True)
        self.passed_toggle.setAutoRaise(True)
        self.passed_toggle.toggled.connect(self._toggle_passed)
        self.passed_label = QtWidgets.QLabel()
        self.passed_label.setTextFormat(Qt.RichText)
        self.passed_label.setContentsMargins(20, 0, 0, 0)
        self.passed_label.setVisible(False)
        layout.addWidget(self.passed_toggle)
        layout.addWidget(self.passed_label)

        # # #--------------------------------------------------------------------
        # # Buttons
        # # #--------------------------------------------------------------------
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        copy_button = QtWidgets.QPushButton("Copy report")
        copy_button.setToolTip("Copy the full report as plain text, e.g. to paste into a ticket.")
        copy_button.clicked.connect(self.copy_report)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(copy_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        if report is None:
            self.rescan()
        else:
            self.set_report(report)

    # # #------------------------------------------------------------------------
    # # Populating
    # # #------------------------------------------------------------------------

    def rescan(self, deep: bool = False) -> None:
        """Run the checks with a Nuke progress bar.

        A deep scan is asynchronous (its probes need Nuke's main loop running),
        so the window stays disabled until it reports back: no second scan can
        start on top of it.
        """
        if self._scanning:
            return
        self._scanning = True
        self.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        progress = NukeProgress("Why Slow? deep scan" if deep else "Why Slow? scan")

        def finished(report: Report) -> None:
            progress.close()
            QtWidgets.QApplication.restoreOverrideCursor()
            self._scanning = False
            if not is_valid(self):
                return
            self.setEnabled(True)
            self.set_report(report)

        try:
            if deep:
                runner.run_checks_async(finished, deep=True, progress=progress)
            else:
                finished(runner.run_checks(progress=progress))
        except Exception:
            progress.close()
            QtWidgets.QApplication.restoreOverrideCursor()
            self._scanning = False
            self.setEnabled(True)
            raise

    def set_report(self, report: Report) -> None:
        self.report = report
        self._close_details()

        self.script_label.setText(report.script_name)
        scan = "Deep scan" if report.deep else "Quick scan"
        self.info_label.setText(f"{report.node_summary}  ·  Nuke {report.nuke_version}  ·  "
                                f"{scan} in {report.duration:.2f}s"
                                + ("  ·  CANCELLED" if report.cancelled else ""))
        self.summary_label.setText(self._summary_html(report))

        container = QtWidgets.QWidget()
        cards = QtWidgets.QVBoxLayout(container)
        cards.setContentsMargins(0, 4, 4, 4)
        cards.setSpacing(8)
        shown = report.issues + report.with_status(STATUS_ERROR)
        for result in shown:
            card = FindingCard(result)
            card.details_requested.connect(self.open_details)
            cards.addWidget(card)
        if not shown:
            nothing = QtWidgets.QLabel("No known causes of slowness found in this session.")
            nothing.setAlignment(Qt.AlignCenter)
            nothing.setStyleSheet(f"color: {theme.MUTED_TEXT}; padding: 30px;")
            cards.addWidget(nothing)
        cards.addStretch(1)
        self.scroll.setWidget(container)

        passed = report.with_status(STATUS_PASSED)
        skipped = report.with_status(STATUS_SKIPPED)
        self.passed_toggle.setText(f"{len(passed)} checks passed"
                                   + (f", {len(skipped)} skipped" if skipped else ""))
        rows = [f"<span style='color:{theme.PASSED_COLOR}'>✔</span> <b>{html.escape(r.title)}</b><br>"
                f"&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:{theme.MUTED_TEXT}'>{html.escape(r.description)}</span>"
                for r in passed]
        rows += [f"<span style='color:{theme.MUTED_TEXT}'>– {html.escape(r.title)} "
                 f"(skipped{': ' + html.escape(r.note) if r.note else ''})</span>"
                 for r in skipped]
        self.passed_label.setText("<br>".join(rows))

        self._fit_to_screen()

    @staticmethod
    def _summary_html(report: Report) -> str:
        issues = report.issues
        errors = report.with_status(STATUS_ERROR)
        if not issues and not errors:
            return f"<span style='color:{theme.PASSED_COLOR}'>No issues found</span>"
        parts = [f"<b>{len(issues)} issue{'s' if len(issues) != 1 else ''} found</b>"]
        for severity in sorted(Severity, reverse=True):
            count = sum(1 for r in issues if r.finding.severity == severity)
            if count:
                parts.append(f"<span style='color:{theme.SEVERITY_COLORS[severity]}'>●</span> "
                             f"{count} {severity.label}")
        if errors:
            parts.append(f"<span style='color:{theme.ERROR_COLOR}'>●</span> "
                         f"{len(errors)} check{'s' if len(errors) != 1 else ''} failed")
        return "&nbsp;&nbsp;&nbsp;".join(parts)

    def _fit_to_screen(self) -> None:
        """Grow to show the cards without scrolling, up to 85% of the screen.

        The first fit (on spawn) is scaled up by theme.SPAWN_HEIGHT_SCALE, so the
        window opens with room to spare; later re-scans only ever grow it.
        """
        screen = (self.screen() if hasattr(self, "screen") else None) or QtGui.QGuiApplication.primaryScreen()
        max_height = int(screen.availableGeometry().height() * 0.85)
        content = self.scroll.widget().sizeHint()
        chrome = self.sizeHint().height() - self.scroll.sizeHint().height()
        wanted = content.height() + chrome + 10
        if not self._spawned:
            wanted = int(wanted * theme.SPAWN_HEIGHT_SCALE)
            self._spawned = True
        width = max(self.width(), content.width() + 40, self.minimumWidth())
        height = min(max_height, max(self.height(), wanted))
        self.resize(width, height)

    def _toggle_passed(self, checked: bool) -> None:
        self.passed_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.passed_label.setVisible(checked)

    # # #------------------------------------------------------------------------
    # # Actions
    # # #------------------------------------------------------------------------

    def open_details(self, result) -> None:
        existing = self._details.get(result.check_id)
        if is_valid(existing):
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = DetailsDialog(result, parent=self)
        self._details[result.check_id] = dialog
        dialog.show()

    def _close_details(self) -> None:
        for dialog in list(self._details.values()):
            if is_valid(dialog):
                dialog.close()
        self._details.clear()

    def copy_report(self) -> None:
        if self.report is None:
            return
        QtGui.QGuiApplication.clipboard().setText(text_report.format_report(self.report))
        logger.info("Report copied to the clipboard.")
