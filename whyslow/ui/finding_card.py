"""The framed card shown in the report for each check that found something."""

from __future__ import annotations

from whyslow.core.finding import Severity
from whyslow.core.runner import STATUS_ERROR, CheckResult
from whyslow.qt import QtWidgets, Qt, Signal
from whyslow.ui import theme


class FindingCard(QtWidgets.QFrame):
    """Severity strip, title, short summary and a Details button."""

    details_requested = Signal(object)  # # the CheckResult

    def __init__(self, result: CheckResult, parent=None):
        super().__init__(parent)
        self.result = result
        is_error = result.status == STATUS_ERROR
        finding = result.finding

        if is_error:
            color, badge_text = theme.ERROR_COLOR, "ERROR"
            title = result.title
            last_line = result.error.strip().splitlines()[-1] if result.error else "Unknown error"
            summary = f"This check failed to run, so it couldn't tell whether there's a problem.\n\n{last_line}"
            metric = ""
        else:
            severity: Severity = finding.severity
            color, badge_text = theme.SEVERITY_COLORS[severity], severity.label.upper()
            title, summary, metric = finding.title, finding.summary, finding.metric

        self.setObjectName("FindingCard")
        self.setStyleSheet(
            f"#FindingCard {{ background: {theme.CARD_BACKGROUND}; "
            f"border: 1px solid {theme.CARD_BORDER}; border-left: 6px solid {color}; "
            "border-radius: 3px; }"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(6)

        # # #--------------------------------------------------------------------
        # # Header row: badge, title, metric
        # # #--------------------------------------------------------------------
        header = QtWidgets.QHBoxLayout()
        badge = QtWidgets.QLabel(badge_text)
        badge.setStyleSheet(theme.badge_style(color))
        header.addWidget(badge)

        title_label = QtWidgets.QLabel(title)
        font = title_label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.1)
        title_label.setFont(font)
        header.addWidget(title_label)
        header.addStretch(1)

        if metric:
            metric_label = QtWidgets.QLabel(metric)
            metric_label.setStyleSheet(f"color: {theme.MUTED_TEXT};")
            header.addWidget(metric_label)
        layout.addLayout(header)

        # # #--------------------------------------------------------------------
        # # Summary and Details button
        # # #--------------------------------------------------------------------
        summary_label = QtWidgets.QLabel(theme.wrapped_html(summary, theme.CARD_WRAP))
        summary_label.setTextFormat(Qt.RichText)
        layout.addWidget(summary_label)

        footer = QtWidgets.QHBoxLayout()
        if not is_error:
            category = QtWidgets.QLabel(result.category)
            category.setStyleSheet(f"color: {theme.MUTED_TEXT};")
            footer.addWidget(category)
        footer.addStretch(1)
        details_button = QtWidgets.QPushButton("Details...")
        details_button.clicked.connect(lambda: self.details_requested.emit(self.result))
        footer.addWidget(details_button)
        layout.addLayout(footer)
