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
| `stamps_custom` (card: "Juicy Stamps", the user-facing name of our build) | Low, any number | Stamps with `panel_hook`. The details note Anchors' autolabel (by design), Stamps currently armed, and older Wired Stamps that still have an autolabel. |

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
- GUI: Harry's checklist. The first pass (7 checks) passed on 2026-11-17.

## Roadmap

1. KnobScripter check: Script Editor `textChanged` receivers, the size of the Script Editor output, `config.all_knobscripters`, the echo pref.
2. Scanning a script on disk (headless Nuke subprocess, then the report as JSON).
3. Reading another user's preferences file.
4. Maybe fix buttons on cards (a `Finding` could carry actions).

## Adding a check

See the docstring in [whyslow/core/check.py](whyslow/core/check.py). In short: add a module to `whyslow/checks/` with an `@register`'d `Check` subclass that has an `id`, a `title`, a `category`, a `description` (shown when it passes), `needs` and `severity_steps`. Its `run(ctx)` returns `None` (passed) or `self.finding(...)` with a summary for the card, the three details texts and the `items` rows.
