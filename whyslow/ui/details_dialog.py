"""Everything known about one finding: explanation, affected items, their code."""

from __future__ import annotations

from whyslow.core import text_report
from whyslow.core.runner import STATUS_ERROR, CheckResult
from whyslow.qt import QtGui, QtWidgets, Qt
from whyslow.ui import theme

_COLUMNS = ("Item", "Class / registered for", "Detail")


class DetailsDialog(QtWidgets.QDialog):

    def __init__(self, result: CheckResult, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.result = result
        finding = result.finding
        is_error = result.status == STATUS_ERROR
        self.items = finding.items if finding else []
        self.setWindowTitle(f"Why Slow? - {result.title}")
        self.resize(820, 640)

        layout = QtWidgets.QVBoxLayout(self)

        # # #--------------------------------------------------------------------
        # # Title row
        # # #--------------------------------------------------------------------
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(finding.title if finding else result.title)
        font = title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.25)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch(1)
        if finding:
            color, text = theme.SEVERITY_COLORS[finding.severity], finding.severity.label.upper()
        else:
            color, text = theme.ERROR_COLOR, "ERROR"
        badge = QtWidgets.QLabel(text)
        badge.setStyleSheet(theme.badge_style(color))
        header.addWidget(badge)
        layout.addLayout(header)

        if is_error:
            subtitle = f"This check failed to run. {result.description}"
        else:
            subtitle = f"{result.category} check. {result.description}"
        description = QtWidgets.QLabel(theme.wrapped_html(subtitle, 110))
        description.setTextFormat(Qt.RichText)
        description.setStyleSheet(f"color: {theme.MUTED_TEXT};")
        layout.addWidget(description)

        # # #--------------------------------------------------------------------
        # # Explanation sections
        # # #--------------------------------------------------------------------
        if finding:
            form = QtWidgets.QFormLayout()
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
            form.setHorizontalSpacing(14)
            for heading, body in (("What's happening", finding.whats_happening),
                                  ("Why it's slow", finding.why_slow),
                                  ("How to fix", finding.how_to_fix)):
                if not body:
                    continue
                heading_label = QtWidgets.QLabel(f"<b>{heading}</b>")
                body_label = QtWidgets.QLabel(theme.wrapped_html(body, 90))
                body_label.setTextFormat(Qt.RichText)
                body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                form.addRow(heading_label, body_label)
            layout.addLayout(form)

        # # #--------------------------------------------------------------------
        # # Items table and code view
        # # #--------------------------------------------------------------------
        splitter = QtWidgets.QSplitter(Qt.Vertical)

        self.table = QtWidgets.QTableWidget(len(self.items), len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, item in enumerate(self.items):
            for column, value in enumerate((item.label, item.kind, item.detail)):
                cell = QtWidgets.QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, min(self.table.columnWidth(0), 300))
        self.table.setColumnWidth(1, min(self.table.columnWidth(1), 200))
        self.table.setSortingEnabled(False)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda *_: self.select_in_dag())
        self.table.setVisible(bool(self.items))
        splitter.addWidget(self.table)

        self.code_view = QtWidgets.QPlainTextEdit()
        self.code_view.setReadOnly(True)
        self.code_view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.code_view.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.code_view.setPlaceholderText("Select a row to see its code.")
        has_code = is_error or any(item.code for item in self.items)
        self.code_view.setVisible(has_code)
        if is_error:
            self.code_view.setPlainText(result.error)
        splitter.addWidget(self.code_view)
        splitter.setSizes([260, 220])
        layout.addWidget(splitter, 1)
        if not self.items and not has_code:
            layout.addStretch(1)

        # # #--------------------------------------------------------------------
        # # Buttons
        # # #--------------------------------------------------------------------
        buttons = QtWidgets.QHBoxLayout()
        self.dag_button = QtWidgets.QPushButton("Select in Node Graph")
        self.dag_button.setToolTip("Select the highlighted nodes and frame them in their Node Graph.")
        self.dag_button.clicked.connect(self.select_in_dag)
        self.dag_button.setEnabled(False)
        self.dag_button.setVisible(any(item.node_path for item in self.items))
        buttons.addWidget(self.dag_button)
        buttons.addStretch(1)
        copy_button = QtWidgets.QPushButton("Copy")
        copy_button.setToolTip("Copy this finding, with every item, as plain text.")
        copy_button.clicked.connect(self.copy)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(copy_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        if self.items:
            self.table.selectRow(0)

    # # #------------------------------------------------------------------------
    # # Behaviour
    # # #------------------------------------------------------------------------

    def _selected_items(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.items[row] for row in rows]

    def _selection_changed(self) -> None:
        selected = self._selected_items()
        self.dag_button.setEnabled(any(item.node_path for item in selected))
        if self.result.status != STATUS_ERROR:
            self.code_view.setPlainText(selected[0].code if len(selected) == 1 else "")

    def select_in_dag(self) -> None:
        from whyslow.ui import nuke_actions
        paths = [item.node_path for item in self._selected_items() if item.node_path]
        if paths:
            nuke_actions.select_nodes(paths)

    def copy(self) -> None:
        if self.result.finding:
            text = text_report.format_finding(self.result.finding, max_items=None)
        else:
            text = f"{self.result.title}\n\n{self.result.error}"
        QtGui.QGuiApplication.clipboard().setText(text)
