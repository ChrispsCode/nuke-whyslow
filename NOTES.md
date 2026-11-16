# Why Slow? - working notes

A read-only diagnostic that explains why the Nuke GUI is slow. It runs a set of checks against the open session and shows one card per problem found, each with a Details window.

```python
import whyslow
whyslow.show()      # report window (Nuke > Help > Why Slow?)
whyslow.report()    # same report as text in the Script Editor
```

## Background

Started 2026-11-17 after the KnobScripter investigation ([knobscripter_findings.md](knobscripter_findings.md)), which showed how much a single tool's hooks can cost in a big script. The goal is a tool that grows a new check whenever a new cause of slowness is found.

## Design

- **Qt-free core** (`whyslow/core/`): the `Check` base class and registry, `Finding`/`FindingItem`/`Severity`, `ScanContext`, the runner and the text report. All of it runs headless.
- **One module per kind of slowdown** (`whyslow/checks/`). The runner imports every module there that doesn't start with `_`, on every scan, so adding a check is adding a file.
- **Checks read Nuke through `ScanContext`** for prefs, callbacks and session info. The future modes (a script on disk, another user's prefs) become other contexts. Each check declares what it `needs`; a context that can't supply it marks the check *skipped*.
- **Findings are plain data** (strings and numbers, `to_dict()`), so a headless Nuke could scan a script on disk and send the report back as JSON.
- **A check that raises, or a module that fails to import, becomes a grey "error" card.** It never stops the scan.
- **UI** (`whyslow/ui/`): the report dialog (cards sorted by severity, a collapsible "passed" list, Re-scan, Copy report) and a details dialog per finding (explanation, items table, code view, Select in Node Graph). Both are non-modal and `WA_DeleteOnClose`, with one report window at a time.
- **Severity**: each check has default `severity_steps` (count to severity). `config.SEVERITY_OVERRIDES` can override any check by id, either with a fixed severity or with new steps.

## Checks (first pass)

| id | Severity | Notes |
|---|---|---|
| `node_knobchanged` | 1-2 nodes Medium, 3+ High | Top-level nodes only. Stamps excluded. |
| `node_autolabel` | 1-2 nodes Medium, 3+ High | Top-level nodes only. Stamps excluded. |
| `buried_node_callbacks` | Low | knobChanged/autolabel on nodes inside groups and gizmos. Burying is a valid trick, so this is just for information. |
| `global_knobchanged` / `global_autolabel` / `global_updateui` | `*` High; a class present in the script Medium; absent Low | Stock entries are filtered by source file: anything inside the Nuke install folder, plus `config.IGNORED_CALLBACKS`. |
| `bbox_warning` | High | `preferences.boundingBoxWarning` (Preferences > Panels > Node Graph). |
| `stamps_original` (card: "Stamps") | Low; 11+ Medium; 31+ High | Stamps without a `panel_hook` knob: vanilla, or our build before its Phase 4. Only top-level Stamps count toward severity; ones in groups are listed. |
| `knobscripter` | Installed with no hooks: Low. Any hook: Medium. Echo on: at least High. Measured cost per printed line (doubled with echo): 5+ ms High, 20+ ms Critical | GUI. Emits the Script Editor output's `textChanged` (runs every listener, text unchanged) and times KnobScripter's share. Lists registered KnobScripters, hidden ones, and echo. |
| `script_editor_hooks` | Same rules as KnobScripter | GUI. Any tool's Python on the Script Editor output. Vanilla has 0 receivers there. KnobScripter rows are listed but counted on its own card (`config.DEDICATED_TOOLS`). |
| `app_event_filters` | Medium; 20+ µs per event High | GUI. Sends 500 custom events to an object nothing uses; any Python that sees them is an app-wide event filter. |
| `idle_python` | Low; 10+ ms/s Medium; 50+ ms/s High | GUI, **deep scan only**. Lets the event loop run for 1.5 s and records third-party Python (timers, polling). |
| `python_tracing` | Critical | `sys.gettrace/getprofile` and the threading equivalents. |
| `stream_redirect` | Medium | GUI. `sys.stdout/stderr` aren't `hiero.FnRedirect.SESysStdOut/Err`. |
| `stamps_custom` (card: "Juicy Stamps", the user-facing name of our build) | Low, any number | Stamps with `panel_hook`. The details note Anchors' autolabel (by design), Stamps currently armed, and older Wired Stamps that still have an autolabel. |

## Probes (trigger and record)

`whyslow/core/probe.py`. To find hooks we don't know about, we trigger something in Nuke's UI and record which Python runs in response. The profiler (`sys.setprofile`) records only **entry points**, functions called directly from C++, recognised by the innermost item on the profiler's stack being a `c_call`. Each is attributed to its file and module (the tool), and Nuke's own code (inside the install folder) and whyslow are filtered out. Timing is measured separately, without the profiler, as a median of 5 runs, and split between tools by their profiled share.

The probes found so far are synchronous: emitting a signal runs its receivers directly, sendEvent goes through the app's event filters, and the idle probe runs a short nested `QEventLoop`. Future triggers that need Nuke's own processing between steps (a real Node Graph redraw, the real print path) should be QTimer-driven.

**Deep scan:** checks with `deep = True` only run from the Deep scan button (or `whyslow.report(deep=True)`). Quick scans list them as skipped, "deep scan only".

**Progress:** `whyslow.progress.NukeProgress` (a `nuke.ProgressTask`) is passed to `runner.run_checks(progress=...)`. It's called before each check with that check's `progress_message`, and Cancel marks the remaining checks as skipped. The report window is disabled while a scan runs, so a deep scan's nested event loop can't start a second scan.

## Decisions

- **Stamps have their own two checks**, one per build, because the severity rules and fixes differ. Stamps nodes (`identifier` knob = anchor/wired, plus a `title` knob) are left out of the generic node checks. `config.STAMP_*` mirrors `stamps.node_queries.is_anchor/is_wired` by hand.
- **Custom vs original is decided by the `panel_hook` knob**, the zero-size PyCustom_Knob that re-arms knobChanged on panel open. Stamps made by our build before Phase 4 don't have it and keep knobChanged on all the time, so they count as "original", which is the behaviour that matters. The version knob (`Stamps v1.2` vs `Stamps official v1.2 (custom hc v1.0)`) is shown per item.
- **Armed custom Stamps aren't raised in severity.** Their onCreate knob script re-arms knobChanged in the GUI on every script open or paste (for vanilla compatibility), and each clears it the first time it's touched with its panel closed. So "armed" right after opening a script is expected.
- **"Top level" means the node's full name has no dot.**
- **Report only for now.** No fix buttons. "Select in Node Graph" is the only action, and it only selects and frames.
- **Stock-callback detection uses the source path, not a hard-coded list**, so it holds across Nuke versions. Verified in the 15.2 GUI: every stock entry, including CaraVR's `__main__` functions, resolves to a file under `/opt/Nuke15.2v9`.

## Testing

`tests/` is local-only (gitignored, per Harry).

- `tests/test_whyslow.py`, headless: `/opt/Nuke15.2v9/Nuke15.2 -ti tests/test_whyslow.py` (add `-v` to print the reports). Synthetic scripts plus real Stamps nodes from `tests/fixtures/`. It writes `showcase_report.json` to `$WHYSLOW_TEST_OUT` (default `/tmp/whyslow_test`). It passed in 15.2v9 and 17.1v1 on 2026-11-17. Vanilla Stamps' unguarded knob scripts print `NameError: stamps` while pasting headless; that's noise, not a failure.
- `tests/make_stamps_fixtures.py` regenerates `stamps_vanilla.nk` and `stamps_custom.nk` with each real Stamps build, one Nuke process per build.
- `tests/gui_preview.py` renders the report and details dialogs offscreen from the showcase JSON (run the tests first). See the docstring for the command.
- `tests/test_gui_probes.py`, **inside GUI Nuke** (`exec(open(...).read())`, e.g. through nuke-mcp). It plants one hook of each kind from a fake `fake_tool` module and reproduces KnobScripter with filler output, checks each card, then cleans up. KnobScripter's hook can't be removed, so restart Nuke afterwards. All passed in 15.2v9 on 2026-11-17.
- GUI: Harry's checklist. The first pass (7 checks) passed on 2026-11-17.

## Roadmap

1. More triggers, to *measure* what the global knobChanged and autolabel callbacks cost: change a knob on a temporary node, force a Node Graph redraw (QTimer-driven).
2. Scanning a script on disk (headless Nuke subprocess, then the report as JSON).
3. Reading another user's preferences file.
4. Maybe fix buttons on cards (a `Finding` could carry actions), e.g. "clear the Script Editor output".
5. Put the Juicy Stamps name in its version string.

## Adding a check

See the docstring in [whyslow/core/check.py](whyslow/core/check.py). In short: add a module to `whyslow/checks/` with an `@register`'d `Check` subclass that has an `id`, a `title`, a `category`, a `description` (shown when it passes), `needs` and `severity_steps`. Its `run(ctx)` returns `None` (passed) or `self.finding(...)` with a summary for the card, the three details texts and the `items` rows.
