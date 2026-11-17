# # Python-made Qt widgets that are alive (and how many are hidden), and every
# # active Python QTimer with its interval and owner.
import collections, gc
from whyslow.qt import QtCore, QtWidgets

_app = QtWidgets.QApplication.instance()
_alive, _hidden = collections.Counter(), collections.Counter()
for _w in _app.allWidgets():
    _mod = type(_w).__module__
    if _mod.startswith(("PySide2", "PySide6", "shiboken")):
        continue
    _key = f"{_mod}.{type(_w).__qualname__}"
    _alive[_key] += 1
    if not _w.isVisible():
        _hidden[_key] += 1
print("--- Python-made widgets alive (hidden):")
for _key, _n in _alive.most_common(30):
    print(f"  {_n:4d} ({_hidden[_key]} hidden)  {_key}")
print("--- Active Python QTimers:")
for _o in gc.get_objects():
    try:
        if isinstance(_o, QtCore.QTimer) and _o.isActive():
            _p = _o.parent()
            print(f"  every {_o.interval():5d} ms  single-shot={_o.isSingleShot()}  "
                  f"parent={type(_p).__module__ + '.' + type(_p).__qualname__ if _p is not None else None}")
    except Exception:
        pass
