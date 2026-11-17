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
| `node_knobchanged_<tool>` | From the entry's `severity`, or the main card's steps | One generated card per `config.EXTRA_NODE_KNOB_CHANGED` entry (e.g. CompAI). It claims top-level knobChanged nodes by an `id` knob/value, or else by the exact callback string. Claimed nodes leave the main card, so one tool can't flood it. Missing card text falls back to the main card's wording. |
| `buried_node_callbacks` | Low | knobChanged/autolabel on nodes inside groups and gizmos. Burying is a valid trick, so this is just for information. |
| `global_knobchanged` / `global_autolabel` / `global_updateui` | `*` High; a class present in the script Medium; absent Low | Stock entries are filtered by source file: anything inside the Nuke install folder, plus `config.IGNORED_CALLBACKS`. |
| `bbox_warning` | High | `preferences.boundingBoxWarning` (Preferences > Panels > Node Graph). |
| `stamps_original` (card: "Stamps") | Low; 11+ Medium; 31+ High | Stamps without a `panel_hook` knob: vanilla, or our build before its Phase 4. Only top-level Stamps count toward severity; ones in groups are listed. |
| `knobscripter` | Installed with no hooks: Low. Any hook: Medium. Echo on: at least High. Measured cost per printed line (doubled with echo): 5+ ms High, 20+ ms Critical | GUI. Emits the Script Editor output's `textChanged` (runs every listener, text unchanged) and times KnobScripter's share. Lists registered KnobScripters, hidden ones, and echo. |
| `script_editor_hooks` | Same rules as KnobScripter | GUI. Any tool's Python on the Script Editor output. Vanilla has 0 receivers there. KnobScripter rows are listed but counted on its own card (`config.DEDICATED_TOOLS`). |
| `app_event_filters` | Medium; 20+ µs per event High | GUI. Sends 500 custom events to an object nothing uses; any Python that sees them is an app-wide event filter. |
| `idle_python` | Low; 10+ ms/s Medium; 50+ ms/s High | GUI, **deep, async**. Watches Nuke idle for 1.5 s and records third-party Python (timers, polling). |
| `frame_change` | Low; 5+ ms/frame Medium; 20+ High; 40+ Critical (tool Python per frame). Info when frames are slow (50+ ms) with no tool Python | GUI, **deep, async**. Steps 10 frames (kept inside the project range when it's 11+ frames long) on a 0 ms QTimer chain, so the Viewer never renders. Records tool Python per frame, `[python]` knob code by node and knob, and what was printed to the Script Editor. Restores the frame. |
| `storage` | Per filer, by the time for one open + 64 KB read: 5+ ms Low, 20+ Medium, 100+ High, 1 s+ or no answer Critical. One level lower for a filer with no sequences on it | GUI, **deep, async**. One card, with one row per filer. It collects every `File_Knob` on nodes (except Writes and disabled nodes), at the current frame and the next two, plus session paths (script, autosave, caches, OCIO, `sys.path`, plugin path). Each directory is resolved with `realpath()` to its real filer via `/proc/mounts`; when mounts are stacked (autofs, then nfs4) the last one listed wins. Each filer is timed in daemon threads, with each operation watched against `STORAGE_OP_TIMEOUT_S`, so a hung filer is reported and never freezes Nuke. The UI freeze per frame is estimated as `STORAGE_FRAME_FACTOR` (1.5) × open time. |
| `node_create` | Tool Python per node created: Low; 20+ ms Medium; 100+ High; 1 s+ Critical. Info if creation is slow (100+ ms) with no tool Python | GUI, **deep, async**. Creates `config.NODE_CREATE_PROBE_CLASSES` (Blur) with `nuke.createNode` (so `onUserCreate` fires too), with undo off and nothing selected, then deletes it. Records tool entry points and their waits, then puts the selection back. Built for Harry's production freeze (creating a Blur taking minutes when a database is down). |
| `python_tracing` | Critical | `sys.gettrace/getprofile` and the threading equivalents. |
| `stream_redirect` | Medium | GUI. `sys.stdout/stderr` aren't `hiero.FnRedirect.SESysStdOut/Err`. |
| `stamps_custom` (card: "Juicy Stamps", the user-facing name of our build) | Low, any number | Stamps with `panel_hook`. The details note Anchors' autolabel (by design), Stamps currently armed, and older Wired Stamps that still have an autolabel. |

## Probes (trigger and record)

`whyslow/core/probe.py`. To find hooks we don't know about, we trigger something in Nuke's UI and record which Python runs in response. The profiler (`sys.setprofile`) records only **entry points**, functions called directly from C++, recognised by the innermost item on the profiler's stack being a `c_call`. Each is attributed to its file and module (the tool), and Nuke's own code (inside the install folder) and whyslow are filtered out. Timing is measured separately, without the profiler, as a median of 5 runs, and split between tools by their profiled share.

What counts as an entry point: a call from C++, or a third-party function called from Nuke's own Python that comes **from a real file, or was typed in the Script Editor** (`<string>` in `__main__`, e.g. a lambda passed to `nuke.addOnCreate`). The second covers `nuke.add…` callbacks, which Nuke runs through its stock dispatcher. Library-generated code (`namedtuple`, dataclasses: `<string>` in their own module) called from stock code isn't counted, so psutil behind Nuke's status bar doesn't show up. Code run from knob strings (`<string>`) records `nuke.thisNode()`/`thisKnob()`, so `[python]` expressions, labels and knob scripts are named by node and knob.

**Waits.** Every probe also times *blocking* C calls (disk: `stat`, `open`, listing, reads; network: socket `connect`/`recv`/`send`/`accept`, DNS, SSL; `select`/`poll`, `sleep`, process waits) and charges them to the entry point that's running (`Entry.waits`). It recognises them through the C function `sys.setprofile` passes with `c_call`, first by `__name__` (a set lookup, so the per-call cost stays low), then by the module that owns it (`__self__`). Rows show "waited 433 ms: socket.socket.recv x1 (400 ms) …", and card summaries say how much of the time was waiting.

**Sync vs async probes.** Emitting a signal and `sendEvent` are synchronous and work inside the scan's Python call. **But Nuke holds back its own UI work (Node Graph redraws, panel refreshes, label and expression evaluation, frame-change updates) until the running Python command returns.** A nested `QEventLoop` doesn't help: only Qt timers fire in it. So the idle and frame-change probes are `AsyncCheck`s. `start()` sets up a QTimer chain and returns, the profiler (`ProfileSession(nested=False)`) stays installed across returns to the main loop, and `done()` reports back. `runner.run_checks_async` runs the normal checks, then each async one in turn with a timeout. Only the report window's Deep scan (and `whyslow.report(deep=True)` in the GUI) runs them; `runner.run_checks` skips them.

**Deep scan:** checks with `deep = True` only run from the Deep scan button (or `whyslow.report(deep=True)`). Quick scans list them as skipped, "deep scan only".

**Progress:** `whyslow.progress.NukeProgress` (a `nuke.ProgressTask`) is passed to `runner.run_checks(progress=...)`. It's called before each check with that check's `progress_message`, and Cancel marks the remaining checks as skipped. The report window is disabled while a scan runs, so a deep scan's nested event loop can't start a second scan.

## Slow storage: what was measured (15.2, 2026-11-17)

`tests/slowfs.py` is a read-only FUSE mirror that adds a delay to each filesystem operation, read live from a JSON file; `"hang"` blocks. It runs from `tests/.venv` (fusepy). These were measured in the GUI with the Viewer connected and a frame every 600 ms, using a 10 ms stall meter:
- An EXR sequence at 300 ms per operation froze the UI **2.7 s on every frame change** (about 9 round trips, on the UI thread). Connecting the Viewer froze it 2.7 s too.
- A BRAW clip at 300 ms didn't freeze while scrubbing; it's opened once.
- With the Viewer off, frame changes don't touch the files. Panel, select, Reload and create Blur were barely affected.
- The probe's open + read through the mirror takes 6 round trips, against Nuke's 9 per frame, hence `STORAGE_FRAME_FACTOR = 1.5`. The card's estimate (2.7 s) matched the measured freeze.

So a clean session doesn't freeze when *creating a node* on a slow filer. Harry's work freezes (creating a Blur taking minutes when the database is down) point to a tool's Python waiting on the network. The `node_create` probe and the profiler's waits are built for that.

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
- `tests/test_gui_async.py`, **inside GUI Nuke**. It runs real async deep scans (clean, planted, then clean again) and stores results in `builtins._whyslow_gui_async`; read them once it's done (a few seconds). All passed in 15.2v9 on 2026-11-17.
- `tests/test_storage_probe.py`, with plain `python3` (no Nuke) and the slowfs EXR mirror mounted: mount parsing, fast, slow, a hung open, a hung lookup, and missing files.
- `tests/test_gui_storage.py`, **inside GUI Nuke** with both mirrors mounted: three real deep scans (fast, 300 ms, a hung open). The UI stayed under 41 ms per freeze while a filer was hung.
- `tests/test_gui_network.py`, **inside GUI Nuke** with the EXR mirror. A local TCP server accepts and never answers; a fake tool's Blur `onCreate` waits on it (0.4 s timeout) and its `onUserCreate` stats the slow mirror. Clean and planted deep scans: the card named `socket.socket.recv` and `os.stat`, the temporary node was removed, and the selection and frame were unchanged.
- `tests/test_gui_probes.py`, **inside GUI Nuke** (`exec(open(...).read())`, e.g. through nuke-mcp). It plants one hook of each kind from a fake `fake_tool` module and reproduces KnobScripter with filler output, checks each card, then cleans up. KnobScripter's hook can't be removed, so restart Nuke afterwards. Its KnobScripter severity tests need a fresh Nuke (no hooks, small output) and are skipped otherwise. All passed in 15.2v9 on 2026-11-17.
- GUI: Harry's checklist. The first pass (7 checks) passed on 2026-11-17.

## Field testing

`docs/snippets/` holds Script Editor snippets for investigating slowness Why Slow? doesn't catch yet: the callback inventory (every registry), the recorder (what Python runs while you reproduce a slow action), thread sampling, Qt objects and timers, and the stall meter. All ran in the 15.2 GUI on 2026-11-17. The first production test found the proprietary tool's slowdown straight from the global knobChanged card.

## Roadmap

1. More triggers, to *measure* what the global knobChanged and autolabel callbacks cost: change a knob on a temporary node, force a Node Graph redraw (async, like the frame probe).
2. **Only if an example turns up that the frame probe misses:** a static "Python in knob expressions" check (`[python` in `writeKnobs` output, plus `label`, which `writeKnobs` leaves out). Nuke evaluates expressions and labels on every frame change whether or not the node feeds a Viewer. The probe caught `[python]` on unconnected nodes (`Blur3.size`, `NoOp1.label`), so the probe covers it so far. Candidates to test: frames the probe doesn't visit, and labels inside groups whose Node Graph isn't open.
3. Scanning a script on disk (headless Nuke subprocess, then the report as JSON).
4. Reading another user's preferences file.
5. Maybe fix buttons on cards (a `Finding` could carry actions), e.g. "clear the Script Editor output".
6. Put the Juicy Stamps name in its version string.

## Adding a check

See the docstring in [whyslow/core/check.py](whyslow/core/check.py). In short: add a module to `whyslow/checks/` with an `@register`'d `Check` subclass that has an `id`, a `title`, a `category`, a `description` (shown when it passes), `needs` and `severity_steps`. Its `run(ctx)` returns `None` (passed) or `self.finding(...)` with a summary for the card, the three details texts and the `items` rows.
