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
# # any check here by its id (each check's `id`), without touching the check
# # itself.
# #
# # Severity names (any case): "info", "low", "medium", "high", "critical".
# #
# # A value is one of:
# #   - A name: that severity whatever the count.
# #       "bbox_warning": "medium",
# #   - Count steps: a tuple (or list) of (at least N, name) pairs. The highest
# #     step the count reaches wins; a count below the first step is "info".
# #       "node_knobchanged": ((1, "medium"), (3, "high")),
# #     gives 1-2 nodes Medium, and 3 or more (e.g. 5) High.
# #       ((1, "low"), (10, "medium"), (30, "high"))
# #     gives 1-9 Low, 10-29 Medium, 30+ High.
# #
# # A name works for every check. Count steps only apply to checks that grade
# # by a count (the global callback checks grade by reach, and the probes by
# # measured time, which their steps are in instead: see each check).
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

# # #----------------------------------------------------------------------------
# # Probes (trigger something in the UI and record which Python answers)
# # #----------------------------------------------------------------------------

# # Tools that have their own card. The generic hook checks still list their
# # hooks but don't count them, so one problem doesn't show up on two cards.
DEDICATED_TOOLS = {"KnobScripter"}
# # How long the deep scan watches Nuke sitting idle.
IDLE_PROBE_SECONDS = 1.5
# # Custom events sent through Qt to find application-wide event filters.
PROBE_EVENT_COUNT = 500
# # The classes Nuke itself puts on sys.stdout / sys.stderr in the GUI
# # (same in 15.2 and 17.1). Anything else means a tool has wrapped print.
EXPECTED_STREAMS = {
    "stdout": "hiero.FnRedirect.SESysStdOut",
    "stderr": "hiero.FnRedirect.SESysStdErr",
}
# # Frame-change probe (deep scan): how many one-frame steps to time, and the
# # GUI time per frame change above which it's reported even when no tool's
# # Python is to blame.
FRAME_PROBE_STEPS = 10
FRAME_SLOW_MS = 50.0

# # Storage probe (deep scan): every file the script reads and the session
# # depends on is traced to its real filer, and each filer is timed in the
# # background (a stat, then an open + 64 KB read of a file the script uses).
# # A single operation slower than this many seconds counts as "no answer".
STORAGE_OP_TIMEOUT_S = 2.0
# # Timings per filer (the median is reported).
STORAGE_REPEATS = 3
# # Node classes whose files are only written on render, never read by the GUI.
STORAGE_IGNORE_CLASSES = {"Write", "DeepWrite", "WriteGeo", "WriteGeo2", "WriteTank"}
# # With the Viewer on a file-per-frame sequence, Nuke opens each frame's file
# # on the UI thread. The UI freeze per frame change is about this many times
# # the probe's open + read time. Measured in 15.2 with a FUSE mirror adding
# # 300 ms per filesystem operation: Nuke froze 2.7 s per frame (9 round trips),
# # the probe's open + read took 1.8 s (6 round trips).
STORAGE_FRAME_FACTOR = 1.5

# # #----------------------------------------------------------------------------
# # Tools whose nodes get their own knobChanged card
# # #----------------------------------------------------------------------------

# # Nodes in the main Node Graph whose knobChanged belongs to one of these tools
# # go on that tool's own card instead of "Nodes with knobChanged callbacks",
# # so one tool's nodes can't flood the main card. Each entry:
# #
# #   "name"      Tool name, used for the card title ("<name> nodes with
# #               knobChanged callbacks") and its check id: "node_knobchanged_"
# #               plus the name in lower case, with anything other than letters
# #               and digits turned into "_" ("Fake Tool" -> node_knobchanged_fake_tool).
# #   "id"        {"id_knob": knob name, "id_value": value}: matches nodes that
# #               have that knob with that value. With no "id_value" (or None),
# #               having the knob is enough. When "id" is a dict it's used and
# #               "callback" is ignored.
# #   "callback"  Otherwise: the node's knobChanged code. Either one string, or
# #               a list of strings (a node matching any of them counts). Matched
# #               exactly, except that spaces, tabs and newlines at the start and
# #               end are ignored on both sides.
# #   "severity"  Severity of the tool's card, by how many of its nodes there are:
# #                 - a name ("info", "low", "medium", "high", "critical", any
# #                   case): that severity for any number of nodes;
# #                 - count steps, a tuple (or list) of (at least N, name) pairs:
# #                   ((1, "low"), (10, "medium")) gives 1-9 nodes Low and 10 or
# #                   more (e.g. 25) Medium; below the first step is "info";
# #                 - missing or None: the main card's steps, ((1, "medium"),
# #                   (3, "high")), so 1-2 nodes Medium and 3 or more (e.g. 5) High.
# #               SEVERITY_OVERRIDES["node_knobchanged_<name>"] still wins over this.
# #   "summary", "what", "why", "how"
# #               Card text: the summary on the card, and the details' "What's
# #               happening", "Why it's slow" and "How to fix". Missing, None or
# #               "": the main card's wording.
# #
# # Only nodes with knobChanged code are considered; Stamps never are.
EXTRA_NODE_KNOB_CHANGED = [
    {
        "name": "CompAI",
        "callback": "import compai; compai.on_knob_changed()",
        "severity": ((5, "low"), (15, "medium")),
        "why": ("CompAI's knobChanged runs on every knob change on its nodes, but it only "
                "does real work for a few of its own knobs and for input changes; for "
                "anything else it's mostly quick name checks."),
        "how": ("Nothing to fix: it can't be disarmed while the panel is closed, "
                "because it has to keep working then."),
    },
]