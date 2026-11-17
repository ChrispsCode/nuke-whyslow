# # Global knobChanged cost of dragging nodes. Three parts, run one at a time
# # in the Script Editor, on the script that feels slow.

# # #---- Part 1: count the knob changes a real drag fires
import nuke, collections
_kc = collections.Counter()
def _kc_count():
    k = nuke.thisKnob()
    _kc[k.name() if k else None] += 1
nuke.addKnobChanged(_kc_count, nodeClass='*')

# # Now select ALL nodes (only the dragged ones count) and drag them around for
# # a couple of seconds. No prints in callbacks while measuring: printing to the
# # Script Editor costs far more than the callbacks themselves.

# # #---- Part 2: the count
print(dict(_kc), sum(_kc.values()), "knob changes for", len(nuke.allNodes()), "top-level nodes")
nuke.removeKnobChanged(_kc_count, nodeClass='*')


# # #---- Part 3: one move of every node, with and without the knobChanged callbacks
# # (all of them for the comparison, Nuke's own two '*' ones too; they're cheap).
# # Every node ends up where it started.
import nuke, time
def _move(nodes, dx):
    t = time.perf_counter()
    for n in nodes:
        n.setXpos(n.xpos() + dx)
    return (time.perf_counter() - t) * 1000
nodes = nuke.allNodes()
saved = {k: list(v) for k, v in nuke.callbacks.knobChangeds.items()}
on = sorted(_move(nodes, d) for d in (10, -10, 10, -10))[1]
nuke.callbacks.knobChangeds.clear()
try:
    off = sorted(_move(nodes, d) for d in (10, -10, 10, -10))[1]
finally:
    nuke.callbacks.knobChangeds.update(saved)
per_change = (on - off) / len(nodes)
print(f"{len(nodes)} nodes, one move: {on:.0f} ms with the callbacks, {off:.0f} ms without "
      f"({per_change * 1000:.2f} us of callbacks per knob change)")

# # The callbacks' cost of the drag in part 1 = per_change (ms) x part 2's total
# # knob changes. For a bigger script, scale part 2's count by the node count:
# # the same drag fires about (total / nodes dragged) changes per node.
