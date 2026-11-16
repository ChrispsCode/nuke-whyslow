"""Read-only probe: counts Python calls, Qt events and event-loop stalls on Nuke's main thread."""
import sys, time, collections, os
from PySide2 import QtCore, QtWidgets

STATE = {}


class _EvFilter(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.counts = collections.Counter()

    def eventFilter(self, obj, ev):
        try:
            self.counts[(int(ev.type()), obj.metaObject().className())] += 1
        except Exception:
            pass
        return False


def _short(fn):
    for key in ("Knob-Scripter", "nukescripts", "nuke_internal", "nuke-mcp", "nuke_addon"):
        i = fn.find(key)
        if i >= 0:
            return fn[i:]
    return os.path.basename(fn)


def start(events=True):
    calls = collections.Counter()
    incl = collections.Counter()
    stack = []
    perf = time.perf_counter

    def prof(frame, event, arg):
        if event == "call":
            co = frame.f_code
            key = (_short(co.co_filename), co.co_name)
            calls[key] += 1
            stack.append((key, perf()))
        elif event == "return" and stack:
            key, t0 = stack.pop()
            incl[key] += perf() - t0

    # # stall meter: 10ms timer, record lateness
    lat = []
    timer = QtCore.QTimer()
    timer.setInterval(10)
    last = [perf()]

    def tick():
        now = perf()
        lat.append(now - last[0])
        last[0] = now

    timer.timeout.connect(tick)
    timer.start()
    evf = None
    if events:
        evf = _EvFilter()
        QtWidgets.QApplication.instance().installEventFilter(evf)
    STATE.pop("act_durs", None)
    STATE.update(calls=calls, incl=incl, lat=lat, timer=timer, evf=evf, t0=perf(), prof=prof)
    sys.setprofile(prof)
    return "started"


def stop(top=25):
    sys.setprofile(None)
    STATE["timer"].stop()
    dur = time.perf_counter() - STATE["t0"]
    evf = STATE["evf"]
    if evf:
        QtWidgets.QApplication.instance().removeEventFilter(evf)
    lat = STATE["lat"]
    names = {int(v): k for k, v in QtCore.QEvent.__dict__.items() if isinstance(v, QtCore.QEvent.Type)}
    rep = {
        "seconds": round(dur, 2),
        "py_calls_total": sum(STATE["calls"].values()),
        "top_calls": [("%s:%s" % k, n, round(STATE["incl"][k] * 1000, 1)) for k, n in STATE["calls"].most_common(top)],
        "top_inclusive_ms": [("%s:%s" % k, round(t * 1000, 1), STATE["calls"][k]) for k, t in STATE["incl"].most_common(top)],
        "ticks": len(lat),
        "tick_avg_ms": round(sum(lat) / len(lat) * 1000, 2) if lat else None,
        "tick_max_ms": round(max(lat) * 1000, 1) if lat else None,
        "ticks_over_50ms": sum(1 for x in lat if x > 0.05),
    }
    if evf:
        rep["events_total"] = sum(evf.counts.values())
        rep["top_events"] = [(names.get(t, t), cls, n) for (t, cls), n in evf.counts.most_common(top)]
    d = STATE.get("act_durs")
    if d:
        rep["actions"] = len(d)
        rep["action_ms_total"] = round(sum(d) * 1000, 1)
    STATE.clear()
    return rep


def run_actions(steps=150, interval=20):
    """Timer-driven DAG work so the event loop (and any UI reactions) runs between steps."""
    import nuke, random
    nodes = nuke.allNodes()
    rnd = random.Random(1)
    i = [0]
    durs = []
    t = QtCore.QTimer()
    t.setInterval(interval)

    def step():
        if i[0] >= steps:
            t.stop()
            return
        t0 = time.perf_counter()
        k = i[0] % 3
        if k == 0:
            nuke.selectAll() if i[0] % 30 == 0 else [n.setSelected(False) for n in nuke.selectedNodes()]
        elif k == 1:
            n = rnd.choice(nodes)
            n.setSelected(True)
            n.setXYpos(n.xpos() + 1, n.ypos())
        else:
            nuke.frame(1 + i[0] % 100)
        durs.append(time.perf_counter() - t0)
        i[0] += 1

    t.timeout.connect(step)
    t.start()
    STATE["act_timer"] = t
    STATE["act_durs"] = durs
    return {"scheduled": steps}


def print_bench(lines=200, width=100, tag="bench"):
    """Print from a timer callback (real SE stdout path) and time it."""
    out = {}

    def go():
        t0 = time.perf_counter()
        for i in range(lines):
            print("%s %04d %s" % (tag, i, "x" * width))
        QtWidgets.QApplication.processEvents()
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["stdout"] = type(sys.stdout).__name__

    QtCore.QTimer.singleShot(0, go)
    STATE["bench"] = out
    return {"scheduled": lines}


def se_state():
    from KnobScripter import utils, config
    se = utils.findSEConsole()
    return {"se_chars": len(se.document().toPlainText()), "se_blocks": se.document().blockCount(),
            "textChanged_receivers": se.receivers(QtCore.SIGNAL("textChanged()")),
            "ks_registered": len(config.all_knobscripters)}
