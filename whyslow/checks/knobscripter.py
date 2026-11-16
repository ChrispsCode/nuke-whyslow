"""
KnobScripter (Adrian Pueyo's script editor). See knobscripter_findings.md.

Every KnobScripter widget ever constructed connects a lambda to the Script
Editor output's textChanged that is never disconnected, and each one copies
the *whole* Script Editor output into every registered KnobScripter. The
docked pane never unregisters, even when its tab is closed. So every printed
line costs O(Script Editor size) x connections x registered KnobScripters.

The check measures that directly: it emits the output's textChanged (which
runs every listener without changing the text) and times KnobScripter's part.
"""

from __future__ import annotations

import os
import sys

from whyslow.checks._helpers import ms, plural, print_hook_severity
from whyslow.core import probe
from whyslow.core.check import CATEGORY_SESSION, NEEDS_GUI, NEEDS_SESSION, Check, register
from whyslow.core.finding import FindingItem, Severity

TOOL = "KnobScripter"


def _registered() -> list:
    """KnobScripter's own list of live instances (config.all_knobscripters)."""
    config = sys.modules.get(f"{TOOL}.config")
    return list(getattr(config, "all_knobscripters", None) or [])


def _is_visible(widget) -> bool | None:
    try:
        return bool(widget.isVisible())
    except RuntimeError:  # # C++ object already deleted
        return None


@register
class KnobScripterHooks(Check):
    id = "knobscripter"
    title = "KnobScripter"
    category = CATEGORY_SESSION
    description = "Measures what KnobScripter's Script Editor hooks cost on every printed line."
    needs = frozenset({NEEDS_SESSION, NEEDS_GUI})
    progress_message = "Measuring KnobScripter's Script Editor hooks..."
    order = 60
    # # ms per Script Editor change (doubled when echo is on) -> severity.
    # # Any listener is at least Medium; see _helpers.print_hook_severity.
    severity_steps = ((0, Severity.MEDIUM), (5, Severity.HIGH), (20, Severity.CRITICAL))

    def run(self, ctx):
        if TOOL not in sys.modules:
            return None
        package = sys.modules[TOOL]
        version = getattr(sys.modules.get(f"{TOOL}.info"), "__version__", "unknown version")
        location = os.path.dirname(getattr(package, "__file__", "") or "") or "unknown location"
        echo = bool(ctx.pref("echoAllCommands", False))
        registered = _registered()
        hidden = [ks for ks in registered if _is_visible(ks) is False]

        items = [FindingItem(label=f"KnobScripter {version}", kind="Install", detail=location)]
        listeners = 0
        worst_ms = 0.0
        worst_chars = 0
        for index, output in enumerate(probe.script_outputs(), 1):
            entries, _ = probe.profile_entries(lambda: probe.emit_text_changed(output))
            ks_entries = [e for e in entries if e.tool == TOOL]
            total_seconds = sum(e.seconds for e in entries) or 1.0
            ks_share = sum(e.seconds for e in ks_entries) / total_seconds if ks_entries else 0.0
            cost_ms = probe.median_ms(lambda: probe.emit_text_changed(output)) * ks_share
            count = sum(e.calls for e in ks_entries if e.qualname.endswith("<lambda>")) or len(ks_entries)
            chars = output.document().characterCount()
            listeners += count
            if cost_ms > worst_ms:
                worst_ms, worst_chars = cost_ms, chars
            items.append(FindingItem(
                label=f"Script Editor output {index}", kind="Script Editor",
                detail=f"{chars:,} characters; {plural(count, 'KnobScripter listener')}; "
                       f"{ms(cost_ms)} per change",
                code="\n".join(f"# {e.label}  ({e.location})  ran {e.calls}x, {e.seconds * 1000:.2f} ms"
                               for e in ks_entries) or "# No KnobScripter code ran."))
        for ks in registered:
            visible = _is_visible(ks)
            state = {True: "visible", False: "hidden but still registered", None: "deleted but still registered"}[visible]
            items.append(FindingItem(label=type(ks).__name__, kind="Registered KnobScripter", detail=state))
        items.append(FindingItem(label="echoAllCommands", kind="Preference", detail="on" if echo else "off"))

        if not listeners:
            return self.finding(
                severity=Severity.LOW,
                metric="Installed",
                summary=("KnobScripter is installed but isn't listening to the Script Editor yet. "
                         "Opening KnobScripter (its pane or its window) adds a hook that copies the "
                         "whole Script Editor output on every print, until Nuke is restarted."),
                whats_happening=(f"KnobScripter {version} is loaded from {location}. No KnobScripter "
                                 "has been opened in this session, so nothing is hooked up."),
                why_slow=self._why_slow(),
                how_to_fix=("Nothing right now. If Nuke gets slow after using KnobScripter, re-scan: "
                            "this card will show what it costs. Keep its pane out of saved workspaces, "
                            "or it's hooked up at every launch."),
                items=items,
            )

        severity = print_hook_severity(self, worst_ms, echo)
        per_action = " Echo is on, so ordinary UI actions print too." if echo else ""
        return self.finding(
            severity=severity,
            metric=f"{ms(worst_ms)} per line",
            summary=(f"KnobScripter copies the whole Script Editor output every time anything prints "
                     f"({plural(listeners, 'listener')}). Each printed line costs {ms(worst_ms)} "
                     f"now, and more as the output grows.{per_action}"),
            whats_happening=(
                f"{plural(listeners, 'KnobScripter hook')} on the Script Editor output, which has "
                f"{worst_chars:,} characters. {plural(len(registered), 'KnobScripter')} registered"
                + (f", {len(hidden)} of them hidden (a closed pane tab, or a window closed with Esc, "
                   "stays registered and keeps paying)" if hidden else "")
                + ". Hooks are added each time a KnobScripter is created and never removed."
                + (" Echo is on: each echoed UI action prints at least two changes, so the cost "
                   "counts double." if echo else "")),
            why_slow=self._why_slow(),
            how_to_fix=(
                "Right now: clear the Script Editor output (the cost drops straight away), and turn "
                "off the 'echo python commands to output window' preference. Restarting Nuke removes "
                "the hooks.\n\n"
                "To stop it coming back: keep the KnobScripter pane out of saved workspaces, and close "
                "KnobScripter windows with the X button, not Esc.\n\n"
                "The real fix is in KnobScripter (see knobscripter_findings.md): connect once per "
                "session, append only new text, skip hidden KnobScripters, and unregister the pane "
                "when its tab closes."),
            items=items,
        )

    @staticmethod
    def _why_slow() -> str:
        return ("Each hook copies the entire Script Editor output and re-lays it out in every "
                "registered KnobScripter, on every printed line. The cost per line grows with the "
                "size of the output (measured: about 5 ms at 100 KB, 28 ms at 500 KB, for one "
                "KnobScripter), and multiplies by the number of hooks and registered KnobScripters. "
                "Long sessions, big scripts printing warnings, and echo all make it worse.")
