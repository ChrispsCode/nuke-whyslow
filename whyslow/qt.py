"""PySide2/PySide6 shim: the rest of the package imports Qt from here."""

from __future__ import annotations

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    import shiboken2 as shiboken
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    import shiboken6 as shiboken

Signal = QtCore.Signal
Qt = QtCore.Qt


def is_valid(obj) -> bool:
    return obj is not None and shiboken.isValid(obj)
