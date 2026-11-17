# # Every callback registered with nuke.add...() that isn't Nuke's own, in every
# # registry (Why Slow? only checks knobChanged, autolabel and updateUI).
import inspect, os
import nuke

_nuke_dir = os.path.realpath(os.path.dirname(nuke.EXE_PATH))
_found = 0
for _name in sorted(n for n in dir(nuke.callbacks)
                    if not n.startswith("_") and isinstance(getattr(nuke.callbacks, n), dict)):
    for _cls, _entries in getattr(nuke.callbacks, _name).items():
        for _entry in _entries:
            _func = _entry[0]
            try:
                _src = os.path.realpath(inspect.getsourcefile(_func) or "?")
            except TypeError:
                _src = "?"
            if _src.startswith(_nuke_dir + os.sep):
                continue
            _found += 1
            print(f"{_name:22} {str(_cls):22} {getattr(_func, '__module__', '?')}."
                  f"{getattr(_func, '__qualname__', _func)}  {_src}")
print(f"--- {_found} non-Nuke callbacks")
