"""The Node Graph's bounding box warning preference."""

from __future__ import annotations

from whyslow.core.check import CATEGORY_PREFERENCES, NEEDS_PREFS, Check, register
from whyslow.core.finding import FindingItem, Severity


@register
class BboxWarning(Check):
    id = "bbox_warning"
    title = "Bounding box warning"
    category = CATEGORY_PREFERENCES
    description = "Checks whether the Node Graph's bounding box warning preference is enabled."
    needs = frozenset({NEEDS_PREFS})
    order = 50
    severity_steps = ((1, Severity.HIGH),)

    def run(self, ctx):
        if not ctx.pref("boundingBoxWarning", False):
            return None
        threshold = ctx.pref("boundingBoxWarningThreshold")
        return self.finding(
            title="Bounding box warning is on",
            severity=self.severity_for(1),
            metric="Preference",
            summary=("The bounding box warning is enabled, so Nuke checks every node's "
                     "bounding box against the format whenever it draws the Node Graph."),
            whats_happening=("Preferences > Panels > Node Graph > Bounding Box Warning > enable "
                             "is ticked"
                             + (f", with the threshold at {threshold:g}%." if threshold is not None else ".")),
            why_slow=("To decide which nodes to highlight, Nuke needs each node's bounding box, "
                      "which means working out the upstream tree for every node it draws. In "
                      "large scripts this adds a lot of overhead to panning, zooming, selecting "
                      "and editing in the Node Graph."),
            how_to_fix=("Untick Preferences > Panels > Node Graph > Bounding Box Warning > enable. "
                        "Turn it on briefly when you want to hunt for oversized bounding boxes, "
                        "then off again."),
            items=[FindingItem(label="boundingBoxWarning", kind="Preference", detail="enabled"),
                   FindingItem(label="boundingBoxWarningThreshold", kind="Preference",
                               detail=f"{threshold:g}%" if threshold is not None else "unknown")],
        )
