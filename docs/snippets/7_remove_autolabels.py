# # Remove every global autolabel callback (nuke.addAutolabel) except Nuke's own,
# # for this session only (restart Nuke to get them back). Nuke's own is the one
# # its menu.py registers on '*' from plugins/autolabel.py; anything whose code
# # lives inside the Nuke install folder is kept, everything else is removed.
# # Python in nodes' own autolabel knobs isn't touched (see the Why Slow? card).
import os, nuke

def _whyslow_unwrap(call):
    call = getattr(call, "func", call)                    # # functools.partial
    return getattr(call, "__func__", call)                # # bound methods

def _whyslow_code_file(call):
    code = getattr(_whyslow_unwrap(call), "__code__", None)
    if code is None:
        return ""
    return code.co_filename if code.co_filename.startswith("<") else os.path.realpath(code.co_filename)

_nuke_dir = os.path.realpath(os.path.dirname(nuke.EXE_PATH)) + os.sep
for _cls, _entries in nuke.callbacks.autolabels.items():
    _keep = [e for e in _entries if _whyslow_code_file(e[0]).startswith(_nuke_dir)]
    for _entry in _entries:
        _f = _whyslow_unwrap(_entry[0])
        print(("kept    " if _entry in _keep else "removed ") + f"{_cls}: {getattr(_f, '__module__', '?')}."
              f"{getattr(_f, '__qualname__', _f)}  ({_whyslow_code_file(_entry[0]) or 'no source file'})")
    _entries[:] = _keep
if not nuke.callbacks.autolabels.get("*"):
    print("WARNING: no autolabel left on '*'. Nuke's own was replaced (an autolabel.py earlier on the plugin path?)")


# # #----------------------------------------------------------------------------
# # Simpler version: keep everything registered on '*', remove every autolabel
# # registered for a specific node class. Tools that registered on '*' stay too.
import nuke
for _cls, _entries in nuke.callbacks.autolabels.items():
    if _cls != "*":
        for _entry in _entries:
            print(f"removed {_cls}: {_entry[0]}")
        _entries.clear()
