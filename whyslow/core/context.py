"""
ScanContext: everything checks read about Nuke, gathered in one place.

Checks go through the context rather than calling `nuke` for preferences,
callbacks and session info, so later sources (another user's preferences
file, a script scanned in a headless Nuke) only need a different context.
Expensive lookups are cached, so checks share one node walk.
"""

from __future__ import annotations

import collections
import inspect
import os
from dataclasses import dataclass
from functools import cached_property

import nuke

from whyslow import config
from whyslow.core.check import NEEDS_GUI, NEEDS_PREFS, NEEDS_SCRIPT, NEEDS_SESSION


@dataclass
class CallbackEntry:
    """One function registered through nuke.add<Callback>()."""

    registry: str        # # e.g. "knobChangeds"
    node_class: str      # # the class it was registered for, "*" for all
    module: str
    qualname: str
    source_file: str     # # "" when it can't be found (builtins, C code)
    is_stock: bool
    func: object

    @property
    def name(self) -> str:
        return f"{self.module}.{self.qualname}"

    def source(self) -> str:
        """The function's source code, or a note on why it isn't available."""
        try:
            text = inspect.getsource(self.func)
        except (OSError, TypeError):
            return f"# Source not available for {self.name}"
        if len(text) > config.MAX_CODE_CHARS:
            text = text[: config.MAX_CODE_CHARS] + "\n# ... (truncated)"
        return text


class ScanContext:
    """The live Nuke session: the open script, its preferences and callbacks."""

    @property
    def provides(self) -> frozenset[str]:
        base = {NEEDS_SCRIPT, NEEDS_PREFS, NEEDS_SESSION}
        return frozenset(base | {NEEDS_GUI}) if nuke.GUI else frozenset(base)

    def shared(self, key: str, factory):
        """Compute a value once per scan and share it between checks.

        For checks that split one pass into several cards (the Stamps checks).
        """
        store = self.__dict__.setdefault("_shared", {})
        if key not in store:
            store[key] = factory()
        return store[key]

    # # #------------------------------------------------------------------------
    # # Session
    # # #------------------------------------------------------------------------

    @cached_property
    def nuke_version(self) -> str:
        return nuke.NUKE_VERSION_STRING

    @cached_property
    def script_name(self) -> str:
        name = nuke.root().name()
        return os.path.basename(name) if name and name != "Root" else "Untitled"

    @cached_property
    def nuke_dir(self) -> str:
        return os.path.realpath(os.path.dirname(nuke.EXE_PATH))

    # # #------------------------------------------------------------------------
    # # Script
    # # #------------------------------------------------------------------------

    @cached_property
    def all_nodes(self) -> list:
        """Every node in the script, including inside groups and gizmos."""
        return nuke.allNodes(group=nuke.root(), recurseGroups=True)

    @cached_property
    def class_counts(self) -> collections.Counter:
        return collections.Counter(node.Class() for node in self.all_nodes)

    @staticmethod
    def is_top_level(node) -> bool:
        """True for nodes in the root Node Graph rather than inside a group."""
        # # Node names can't contain dots, so a dot means a parent group.
        return "." not in node.fullName()

    @staticmethod
    def is_stamp(node) -> bool:
        """True for Stamps Anchors and Wired Stamps (they get their own check)."""
        id_knob = node.knob(config.STAMP_IDENTIFIER_KNOB)
        if id_knob is None or node.knob(config.STAMP_TITLE_KNOB) is None:
            return False
        try:
            return id_knob.value() in config.STAMP_IDENTIFIER_VALUES
        except Exception:
            return False

    # # #------------------------------------------------------------------------
    # # Preferences
    # # #------------------------------------------------------------------------

    @cached_property
    def _prefs(self):
        return nuke.toNode("preferences")

    def pref(self, name: str, default=None):
        """A preference value, or `default` if this Nuke doesn't have it."""
        prefs = self._prefs
        knob = prefs.knob(name) if prefs is not None else None
        return knob.value() if knob is not None else default

    # # #------------------------------------------------------------------------
    # # Global callbacks
    # # #------------------------------------------------------------------------

    def callbacks(self, registry: str) -> list[CallbackEntry]:
        """Entries in one nuke.callbacks registry, e.g. "knobChangeds"."""
        if registry not in self._callback_cache:
            self._callback_cache[registry] = self._read_callbacks(registry)
        return self._callback_cache[registry]

    @cached_property
    def _callback_cache(self) -> dict[str, list[CallbackEntry]]:
        return {}

    def _read_callbacks(self, registry: str) -> list[CallbackEntry]:
        table = getattr(nuke.callbacks, registry, None) or {}
        entries = []
        for node_class, registered in list(table.items()):
            for entry in list(registered):
                func = entry[0]
                module = getattr(func, "__module__", None) or "?"
                qualname = getattr(func, "__qualname__", None) or getattr(func, "__name__", repr(func))
                try:
                    source_file = os.path.realpath(inspect.getsourcefile(func) or "")
                except TypeError:
                    source_file = ""
                is_stock = (
                    (source_file != "" and source_file.startswith(self.nuke_dir + os.sep))
                    or (module, qualname) in config.IGNORED_CALLBACKS
                )
                entries.append(CallbackEntry(registry, node_class, module, qualname,
                                             source_file, is_stock, func))
        return entries
