"""
The few things the report does to the Nuke session. Only navigation:
selecting and framing nodes. Nothing in the script's content is changed.
"""

from __future__ import annotations

import nuke

from whyslow import logger


def _group_path(node) -> str:
    """The full name of the group a node lives in ("Group1.Blur1" -> "Group1"), "" for root."""
    return node.fullName().rpartition(".")[0]


def select_nodes(paths: list[str]) -> None:
    """Select the nodes and frame them in their Node Graph.

    Nodes can live in different groups; only the first node's group is shown
    and framed, and the selection is limited to that group.
    """
    nodes = [n for n in (nuke.toNode(p) for p in paths) if n is not None]
    if not nodes:
        logger.warning("Those nodes no longer exist. Re-scan to refresh the report.")
        return

    group_path = _group_path(nodes[0])
    nodes = [n for n in nodes if _group_path(n) == group_path]
    parent = nuke.toNode(group_path) if group_path else nuke.root()

    with parent:
        for node in nuke.selectedNodes():
            node.setSelected(False)
        for node in nodes:
            node.setSelected(True)

    xs = [n.xpos() + n.screenWidth() / 2 for n in nodes]
    ys = [n.ypos() + n.screenHeight() / 2 for n in nodes]
    center = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]

    if not group_path:
        with nuke.root():
            if len(nodes) == 1:
                nuke.zoom(max(nuke.zoom(), 1.0), center)
            else:
                nuke.zoomToFitSelected()
        return

    # # Nuke 15 can't make a Group view active from Python, so framing inside a
    # # group may do nothing (nuke-dev api-quirks: Node Graph navigation). Open
    # # the group's tab so the selection is at least visible, then try anyway.
    nuke.showDag(parent)
    with parent:
        nuke.zoom(max(nuke.zoom(), 1.0), center)
        nuke.zoomToFitSelected()
