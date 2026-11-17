# # Record which Python runs while you reproduce the slowness. Run it, then do
# # the slow thing in Nuke for SECONDS. Needs Why Slow? installed.
SECONDS = 15

from whyslow.core import probe
from whyslow.qt import QtCore

_session = probe.ProfileSession(nested=False)


def _whyslow_record_stop():
    entries = _session.stop()
    print(f"--- {SECONDS} s recorded ({_session.wall:.1f} s wall). Top Python entry points "
          "(kind 'other' = not Nuke, not Why Slow?):")
    # # Leave out Why Slow? and these snippets' own functions.
    for e in [e for e in entries if e.kind != "whyslow" and not e.qualname.startswith("_whyslow")][:40]:
        nodes = ""
        if e.contexts:
            nodes = "  nodes: " + ", ".join(f"{n}.{k}" if k else n for n, k in list(e.contexts)[:6])
        print(f"{e.seconds * 1000:9.1f} ms {e.calls:7d}x  [{e.kind:6}] {e.tool:28} {e.label}  "
              f"({e.location}){nodes}")


_session.start()
QtCore.QTimer.singleShot(SECONDS * 1000, _whyslow_record_stop)
print(f"Recording for {SECONDS} s: reproduce the slowness now...")
