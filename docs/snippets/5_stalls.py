# # Stall meter: how often Nuke's UI thread freezes, whatever the cause (Python
# # or not). Run it, then use Nuke normally / do the slow thing for SECONDS.
SECONDS = 15
import time
from whyslow.qt import QtCore

_gaps, _last = [], [time.perf_counter()]
_whyslow_stall_timer = QtCore.QTimer()
_whyslow_stall_timer.setInterval(10)


def _whyslow_tick():
    now = time.perf_counter()
    _gaps.append((now - _last[0]) * 1000)
    _last[0] = now


def _whyslow_stall_report():
    _whyslow_stall_timer.stop()
    big = sorted((g for g in _gaps if g > 50), reverse=True)
    print(f"--- stall meter: {len(_gaps)} ticks over {SECONDS} s, max gap {max(_gaps):.0f} ms, "
          f"{len(big)} freezes > 50 ms, {sum(big) / 1000:.1f} s frozen in total. "
          f"Longest: {[round(g) for g in big[:10]]}")


_whyslow_stall_timer.timeout.connect(_whyslow_tick)
_whyslow_stall_timer.start()
QtCore.QTimer.singleShot(SECONDS * 1000, _whyslow_stall_report)
print(f"Measuring stalls for {SECONDS} s: use Nuke / reproduce the slowness now...")
