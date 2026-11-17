# # What other Python threads are doing (busy threads compete with the UI for
# # the GIL). Samples every thread's stack 50 times over 1 s.
import collections, os, sys, threading, time, traceback

_main = threading.main_thread().ident
_counts = collections.Counter()
for _ in range(50):
    for _tid, _frame in sys._current_frames().items():
        if _tid == _main:
            continue
        _top = traceback.extract_stack(_frame)[-3:]
        _where = " <- ".join(f"{f.name}@{os.path.basename(f.filename)}:{f.lineno}"
                             for f in reversed(_top))
        _counts[(_tid, _where)] += 1
    time.sleep(0.02)
_names = {t.ident: f"{t.name} ({type(t).__module__})" for t in threading.enumerate()}
print(f"--- {len(_names)} threads. Samples out of 50 where each was seen (waits = idle):")
for (_tid, _where), _n in _counts.most_common(30):
    print(f"{_n:3d}/50  {_names.get(_tid, _tid)}: {_where}")
