# KnobScripter: why the docked pane slows Nuke

Investigated 2026-11-17, live in NukeX 15.2v9 (GUI) through nuke-mcp. KnobScripter v3.2 at `~/.nuke/Knob-Scripter/` (git `d77a388`), loaded from `~/.nuke/init.py` via `nuke.pluginAddPath("./Knob-Scripter/")`. Nothing in KnobScripter was modified.

**Symptom reported:** with the registered KnobScripter pane installed, the Nuke UI becomes dramatically slower in large scripts. In small scripts nobody notices. The floating Alt+Z window doesn't cause it.

## Summary

The pane mirrors Nuke's Script Editor output, and it copies the **entire** output every time **any** line is printed. Three things compound the cost:

1. A new signal connection is added for every KnobScripter instance created, and none is ever removed.
2. The docked pane never unregisters itself, even after its tab is closed.
3. "Echo all python commands to Script Editor" is on in the prefs, so ordinary UI actions print lines.

The cost per printed line is O(size of the Script Editor output), so the session gets steadily slower as the Script Editor fills. Total cost is quadratic in output volume.

It is **not** an idle timer or an updateUI callback. With the pane open and nothing happening, KnobScripter runs no Python at all.

## Root cause

### The console mirror: `KnobScripter/utils.py:139-162`

```python
def setSEConsoleChanged():
    se_console = findSEConsole()
    se_console.textChanged.connect(lambda: consoleChanged(se_console))   # never disconnected

def consoleChanged(self):
    for ks in config.all_knobscripters:
        console_text = self.document().toPlainText()           # full copy of the SE output
        ...
        elif console_text.startswith(omit_se_console_text):    # O(n) compare
            ks_text = str(console_text[len(omit_se_console_text):])
        ...
        ks_output.setPlainText(ks_text)                        # full re-layout of a QTextEdit
        ks_output.verticalScrollBar().setValue(ks_output.verticalScrollBar().maximum())
```

`setSEConsoleChanged()` is called from `KnobScripterWidget.__init__` (`knob_scripter.py:160`), so **every** KnobScripter ever constructed (pane or floating) adds another lambda. Each lambda loops over **every** registered KnobScripter. The work per Script Editor write is therefore:

```
(connections made this session) x (KnobScripters registered) x O(Script Editor size)
```

### Why only the pane: registration leak

- The floating window removes itself from `config.all_knobscripters` in `closeEvent` (`knob_scripter.py:2034`), so after you close it with the X button the loop is empty and cheap.
- The pane (`KnobScripterPane`) is never removed. Closing its dock tab doesn't send `closeEvent` to the widget. I verified this: after closing the tab, `len(config.all_knobscripters)` stayed at 1, and `isVisible()` was False. A hidden, closed pane still pays the full copy on every print.
- If the pane is part of the saved workspace, it is constructed at startup and stays registered for the whole session.
- Probably also affected, but not verified: closing a floating window with **Esc** calls `QDialog.reject()`, which hides it without a `closeEvent`, so it would stay registered too.

### What makes it print a lot: echo

`preferences.echoAllCommands` was `True`. Invoking `Edit/Select All` from the menu emitted **2** Script Editor `textChanged` signals, so each echoed UI action pays the full copy twice.

## Measurements

The test session had a synthetic 2,800-node script (400 chains of CheckerBoard → Grade → Blur → Transform → ColorCorrect → Crop → Dot).

**Printing 300 lines of about 110 characters** from a QTimer callback, so they went through Nuke's real `SESysStdOut`. Two `textChanged` connections, KnobScripter pane closed but still registered:

| Condition | Script Editor size | Time for 300 prints |
|---|---|---|
| KnobScripter registered | 33 KB | **1,780 ms** (~6 ms per line) |
| `config.all_knobscripters` emptied (control) | 65 KB | **41 ms** |
| KnobScripter registered again | 98 KB | **3,089 ms** (~10 ms per line) |

That's about 43× slower at only 33 KB, even though the control run had the larger document.

**The cost of one `setPlainText` + scroll** on an off-screen `ScriptOutputWidget`, per connection per KnobScripter. The visible widget is slower than this.

| Script Editor size | ms per update |
|---|---|
| 30 KB | 1.3 |
| 100 KB | 1.6 |
| 500 KB | 10.1 |
| 1 MB | 20.7 |
| 3 MB | 74.1 |

With 2-3 connections and a few MB of Script Editor output (a long session, a big script printing load warnings, echo on), each echoed action costs hundreds of ms.

## Ruled out (measured)

| Suspect | Result |
|---|---|
| Idle polling (timers, updateUI) | Pane open and visible, about 1 s idle: no KnobScripter Python ran, no events on its widgets, max event-loop gap 18 ms (same as closed). |
| DAG work | 150 timer-driven actions (select/deselect all, select and move a node, change frame) on 2,800 nodes: **313 ms with the pane open vs 343 ms closed**. That's noise, and zero KnobScripter calls. |
| `PythonPanel` knobChanged callback | `nukescripts/panels.py` `addToPane()` calls `nuke.addKnobChanged(PythonPanelKnobChanged, nodeClass='PanelNode', ...)` and **never removes it**, which leaks one Panel and one PanelNode per pane open. But Nuke already ships `'*'` knobChanged callbacks (localisation prefs), so Python's `_doCallbacks` already runs on every knob change. The extra entry costs only a dict lookup for non-PanelNode classes. Negligible. |
| Editor line numbers and highlighting | `updateRequest → updateLineNumberArea` runs on cursor blink and `highlightCurrentLine` runs on cursor moves. These are tiny and only happen while the editor has focus. |

## Smaller issues (not performance)

- `utils.relistAllKnobScripterPanes` (`utils.py:173-176`) removes from `config.all_knobscripters` while iterating over it, so it can skip closed panes.
- The pane's buttons set app-wide-ish shortcuts (F5, Ctrl+R, Ctrl+F, Ctrl+Backspace) that can take over Nuke's own while the pane is shown.

## Suggested fixes (for upstream or a fork)

1. Connect the Script Editor `textChanged` signal **once** per session (module-level guard), not once per widget.
2. Append only the **new** text (track the last length, `insertPlainText` at the end) instead of `setPlainText(everything)`.
3. Skip KnobScripters that aren't visible, and catch up on `showEvent`.
4. Unregister the pane when its tab closes (`hideEvent` / `destroyed`), and handle `reject()` for floating windows.

## Quick check in a real slow session

Paste into the Script Editor:

```python
from KnobScripter import utils, config
from PySide2 import QtCore
se = utils.findSEConsole()
print(len(se.document().toPlainText()), "chars |",
      se.receivers(QtCore.SIGNAL("textChanged()")), "hooks |",
      len(config.all_knobscripters), "KS registered")
```

If the character count is large, clearing the Script Editor output or turning echo off should restore speed immediately. That confirms the diagnosis.

## How it was found (method, for the diagnostic tool)

These techniques worked, and are the seed for `nuke-whyslow`:

1. **Static scan** of the tool for things that hook into Nuke or Qt: `nuke.add*` callbacks, `QTimer`, `installEventFilter`, `*.connect(` on **Nuke-owned** widgets (here, the Script Editor output), `registerWidgetAsPanel`, `allWidgets()`, overridden `*Event` methods.
2. **Inventory of live callbacks:** dump `nuke.callbacks.knobChangeds`, `updateUIs`, `autolabels`, `onCreates` (and so on) with the module and function name of each entry, and flag duplicates and leaked `PanelNode` entries.
3. **Inventory of signal receivers on Nuke-owned widgets:** `widget.receivers(SIGNAL("textChanged()"))` on the Script Editor output. Counts above Nuke's baseline mean third-party hooks.
4. **A main-thread profiler** (`sys.setprofile`) over a time window: call counts and inclusive time per `file:function`, short paths for `nukescripts`, `nuke_internal` and plugin dirs.
5. **A Qt event census:** an app-wide `eventFilter` counting `(event type, receiver class)`.
6. **An event-loop stall meter:** a 10 ms `QTimer`, recording the gaps between ticks (average, max, count over 50 ms).
7. **A/B with a timer-driven workload** (select, move, frame change) so the event loop runs between steps, compared with the suspect present and absent.
8. **A print benchmark through the real `SESysStdOut`** (from a QTimer callback), with and without the suspect registered. This is what exposed the cause.

Gotchas hit along the way:
- Programmatic DAG actions don't trigger everything a real mouse drag does. Echo output only appears for menu or shortcut actions (`findItem(...).invoke()` reproduces it).
- Nuke's status bar (`FnStatusBar.updateStatusBar`, using psutil) is the baseline idle Python load, about 14 ms per second. Don't mistake it for a suspect.
- Stock `autolabel` runs once per node when the DAG redraws (2,802 calls, about 490 ms here). It's worth knowing as a baseline in big scripts.
