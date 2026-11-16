"""
Constants for Why Slow?. Everything a user might want to tune lives here,
so the checks themselves never need editing to change thresholds.
"""

from __future__ import annotations

import logging

# # #----------------------------------------------------------------------------
# # Logging (see logger.py)
# # #----------------------------------------------------------------------------

LOG_LEVEL = logging.INFO
# # "full" -> [whyslow.module.Class.function] INFO: ...   (development)
# # "short" -> [whyslow] INFO: ...                         (published)
LOG_PREFIX_STYLE = "short"

# # #----------------------------------------------------------------------------
# # Severity overrides
# # #----------------------------------------------------------------------------

# # Each check ships its own default severity (its `severity_steps`). Override
# # any check here by its id, without touching the check itself. Two forms:
# #
# #   "bbox_warning": "medium",                          # always this severity
# #   "node_knobchanged": ((1, "medium"), (3, "high")),  # by count: at least N -> severity
# #
# # Severity names: info, low, medium, high, critical. The fixed form works for
# # every check; the count form only for checks that grade by a count (the
# # global callback checks grade by reach instead).
SEVERITY_OVERRIDES: dict = {
}

# # #----------------------------------------------------------------------------
# # Global callbacks
# # #----------------------------------------------------------------------------

# # Callbacks whose source file is inside the Nuke install folder are Nuke's
# # own and are never reported. List any other (module, qualname) pairs to treat
# # as harmless here, e.g. ("my_studio.callbacks", "fast_autolabel").
IGNORED_CALLBACKS: set[tuple[str, str]] = set()

# # #----------------------------------------------------------------------------
# # Stamps
# # #----------------------------------------------------------------------------

# # Stamps nodes are left out of the generic node checks (they get their own
# # section). A node is a Stamp when it has both knobs and the identifier knob
# # holds one of these values. Mirrors stamps.node_queries.is_anchor/is_wired,
# # so keep it in step by hand.
STAMP_IDENTIFIER_KNOB = "identifier"
STAMP_TITLE_KNOB = "title"
STAMP_IDENTIFIER_VALUES = ("anchor", "wired")
# # Stamps made by Harry's custom build carry this (zero-size PyCustom) knob,
# # which re-arms their knobChanged on panel open. Stamps without it (vanilla,
# # or the custom build before its Phase 4) keep knobChanged on all the time.
STAMP_CUSTOM_KNOB = "panel_hook"

# # #----------------------------------------------------------------------------
# # Report display
# # #----------------------------------------------------------------------------

# # Card and details text is wrapped by hand at this width (word-wrapped
# # QLabels clip inside Nuke, see the nuke-dev panels notes).
TEXT_WRAP = 78
# # Longest callback source shown in the details code view.
MAX_CODE_CHARS = 20_000
