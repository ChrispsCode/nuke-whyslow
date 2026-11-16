"""
whyslow.logger
==============

Drop-in logging utility that routes messages through Python's standard
`logging` module while:

  - Tagging every line with a configurable prefix (see config.py:
    LOG_PREFIX_STYLE) -- either the full dotted location
    (whyslow.module.Class.function) or a short fixed tag (whyslow).
  - Sending output through nuke.tprint() / nuke.error() / nuke.warning()
    so it shows up correctly in both the terminal and Nuke's Script
    Editor / error console, with Nuke's own native styling untouched.
  - Optionally colorizing the *terminal* copy only, via a `color=`
    kwarg on the call itself (default: no color, plain text).

Usage
-----
    from whyslow import logger

    logger.info("Loaded 12 VDB elements")
    logger.warning("Cache is stale, rebuilding")
    logger.error("Failed to parse EXR header", color=True)
    logger.debug("x=%s y=%s", x, y)

Each call automatically picks up the calling module (and class, and
function) name, so there is nothing extra to pass at the call site
beyond the message (and optional color flag).

Level and prefix style are both set in config.py. To override the level
for just one module while developing:

    import logging
    from whyslow import logger
    logger.set_level(logging.DEBUG, "bbox_warning")
"""

from __future__ import annotations

import inspect
import logging
import sys

from whyslow import config

try:
    import nuke
    _HAS_NUKE = True
except ImportError:
    _HAS_NUKE = False


# ---------------------------------------------------------------------------
# ANSI color codes (terminal only -- never applied to the Nuke console)
# ---------------------------------------------------------------------------

class _Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"


_LEVEL_COLOR = {
    logging.DEBUG: _Ansi.GREY,
    logging.INFO: _Ansi.CYAN,
    logging.WARNING: _Ansi.YELLOW,
    logging.ERROR: _Ansi.RED,
    logging.CRITICAL: _Ansi.BOLD + _Ansi.RED,
}

_NAMED_COLORS = {
    "red": _Ansi.RED,
    "green": _Ansi.GREEN,
    "yellow": _Ansi.YELLOW,
    "blue": _Ansi.BLUE,
    "magenta": _Ansi.MAGENTA,
    "cyan": _Ansi.CYAN,
    "grey": _Ansi.GREY,
    "gray": _Ansi.GREY,
    "bold_red": _Ansi.BOLD + _Ansi.RED,
}


PREFIX_TAG = "whyslow"
_PACKAGE_NAME = "whyslow"


# ---------------------------------------------------------------------------
# Formatter: builds the prefix string, with optional ANSI color wrapping.
# Prefix shape depends on config.LOG_PREFIX_STYLE:
#   "full"  -> [whyslow.module.Class.function] LEVEL: message
#   "short" -> [whyslow] LEVEL: message
# ---------------------------------------------------------------------------

class _Formatter(logging.Formatter):

    def __init__(self, colorize: bool = False):
        super().__init__()
        self.colorize = colorize

    def format(self, record: logging.LogRecord) -> str:
        style = getattr(config, "LOG_PREFIX_STYLE", "short")

        if style == "full":
            location = getattr(record, "ws_location", "?")
            prefix = f"{PREFIX_TAG}.{location}"
        else:
            prefix = PREFIX_TAG

        base = f"[{prefix}] {record.levelname}: {record.getMessage()}"

        if not self.colorize:
            return base

        color = getattr(record, "ws_color", None)
        if color is None:
            return base

        return f"{color}{base}{_Ansi.RESET}"


# ---------------------------------------------------------------------------
# Handler: terminal (stdout/stderr), with color support
# ---------------------------------------------------------------------------

class _TerminalHandler(logging.StreamHandler):
    """Plain terminal handler. WARNING and above go to stderr.

    Deliberately targets sys.__stdout__ / sys.__stderr__ -- the original
    streams saved by Python at interpreter startup -- rather than
    sys.stdout / sys.stderr. Nuke's GUI reassigns the latter to feed its
    Script Editor console, which doesn't interpret ANSI escape codes; if
    this handler wrote there, colored output would (a) never reach the
    real terminal and (b) show up as raw "[31m" junk in the Script Editor.
    Using the __stdout__/__stderr__ originals keeps this handler talking
    to the actual terminal no matter what Nuke does to sys.stdout/stderr.
    """

    def __init__(self):
        super().__init__(stream=sys.__stdout__)
        self.setFormatter(_Formatter(colorize=True))

    def emit(self, record: logging.LogRecord) -> None:
        stream = sys.__stderr__ if record.levelno >= logging.WARNING else sys.__stdout__
        if stream is None:
            return
        self.stream = stream
        super().emit(record)


# ---------------------------------------------------------------------------
# Handler: routes into Nuke's console / Script Editor via nuke.* calls.
# Deliberately never colorized -- Nuke applies its own native styling
# for nuke.error / nuke.warning, and we don't want to fight that.
# ---------------------------------------------------------------------------

class NukeConsoleHandler(logging.Handler):
    """Routes log records to nuke.tprint/.warning/.error/.critical.

    Silently no-ops if `nuke` isn't importable (e.g. running unit tests
    outside of Nuke) -- the terminal handler already covers that case.
    """

    def __init__(self):
        super().__init__()
        self.setFormatter(_Formatter(colorize=False))

    def emit(self, record: logging.LogRecord) -> None:
        if not _HAS_NUKE:
            return

        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return

        if record.levelno >= logging.CRITICAL:
            nuke.critical(msg)
        elif record.levelno >= logging.ERROR:
            nuke.error(msg)
        elif record.levelno >= logging.WARNING:
            nuke.warning(msg)

        # all levels should always print to the Script Editor, so 
        # let's just use print() for everything
        print(msg)


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------

_ROOT_NAME = "WHYSLOW"
_root_logger = logging.getLogger(_ROOT_NAME)
_root_logger.setLevel(getattr(config, "LOG_LEVEL", logging.INFO))
_root_logger.propagate = False

if not _root_logger.handlers:
    _root_logger.addHandler(_TerminalHandler())
    _root_logger.addHandler(NukeConsoleHandler())


def _caller_frame_info(skip_frames: int = 2):
    """Walk the stack to find the actual calling frame, skipping frames
    internal to this logger module itself.

    Returns (module_path, module_short, qualified_name):
      - module_path: full dotted path, e.g. "whyslow.checks.bbox_warning"
                     (used for the displayed prefix -- lets you navigate
                     straight to the file from a log line)
      - module_short: last component only, e.g. "loaded_module"
                     (used for set_level(level, "loaded_module") overrides,
                     so you don't have to type the full path to silence
                     one module)
      - qualified_name: "Class.function" if called from inside a method
                     (detected via `self`/`cls` in the frame's locals),
                     otherwise just "function"
    """
    frame = inspect.currentframe()
    try:
        for _ in range(skip_frames):
            if frame.f_back is None:
                break
            frame = frame.f_back

        func_name = frame.f_code.co_name
        module = inspect.getmodule(frame)
        module_name = module.__name__ if module else frame.f_globals.get("__name__", "?")
        # Keep the FULL dotted path (e.g. "whyslow.checks.bbox_warning")
        # rather than trimming to the last component -- this is what lets you
        # go straight from a log line to the exact file, especially once
        # multiple subfolders have similarly-named files.
        module_path = module_name

        # Detect class name, if this call happened inside a method.
        # `self` -> instance method, `cls` -> classmethod. Both are normal
        # local variables in the frame, so this is a cheap, safe check.
        class_name = None
        local_vars = frame.f_locals
        if "self" in local_vars:
            class_name = type(local_vars["self"]).__name__
        elif "cls" in local_vars:
            cls_val = local_vars["cls"]
            class_name = cls_val.__name__ if isinstance(cls_val, type) else None

        if class_name:
            qualified_name = f"{class_name}.{func_name}"
        else:
            qualified_name = func_name

        module_short = module_path.rsplit(".", 1)[-1]
        return module_path, module_short, qualified_name
    finally:
        del frame


def _logger_for_module(module_short: str) -> logging.Logger:
    """Get (or create) the child logger for a given calling module.

    Child loggers have no level set by default, so they fall through to
    the effective level of WHYSLOW (the root logger) unless overridden via
    set_level(level, module=...). They propagate up to _root_logger's
    handlers, so handlers are only ever registered once.
    """
    child = logging.getLogger(f"{_ROOT_NAME}.{module_short}")
    child.propagate = True
    return child


def _resolve_color(level: int, color) -> str | None:
    """Translate the `color` argument into an ANSI code, or None for off.

    color=False / None -> no color (default)
    color=True          -> level-default color
    color="red" etc.    -> explicit named color
    """
    if not color:
        return None
    if color is True:
        return _LEVEL_COLOR.get(level, _Ansi.CYAN)
    if isinstance(color, str):
        return _NAMED_COLORS.get(color.lower(), _LEVEL_COLOR.get(level, _Ansi.CYAN))
    return None


def _log(level: int, msg: str, *args, color=False, **kwargs) -> None:
    module_path, module_short, qualified_name = _caller_frame_info(skip_frames=3)

    # Strip the leading package name from the displayed path so we don't get
    # a redundant "whyslow.whyslow.load.loaded_module...." prefix --
    # the "whyslow" tag already represents the package itself.
    package_prefix = f"{_PACKAGE_NAME}."
    if module_path.startswith(package_prefix):
        display_path = module_path[len(package_prefix):]
    else:
        display_path = module_path

    location = f"{display_path}.{qualified_name}"
    extra = {
        "ws_location": location,
        "ws_color": _resolve_color(level, color),
    }
    target_logger = _logger_for_module(module_short)
    target_logger.log(level, msg, *args, extra=extra, **kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def debug(msg: str, *args, color=False, **kwargs) -> None:
    _log(logging.DEBUG, msg, *args, color=color, **kwargs)


def info(msg: str, *args, color=False, **kwargs) -> None:
    _log(logging.INFO, msg, *args, color=color, **kwargs)


def warning(msg: str, *args, color=False, **kwargs) -> None:
    _log(logging.WARNING, msg, *args, color=color, **kwargs)


warn = warning


def error(msg: str, *args, color=False, **kwargs) -> None:
    _log(logging.ERROR, msg, *args, color=color, **kwargs)


def critical(msg: str, *args, color=False, **kwargs) -> None:
    _log(logging.CRITICAL, msg, *args, color=color, **kwargs)


def set_level(level: int, module: str | None = None) -> None:
    """Set verbosity globally, or for one module's logger only.

    Examples:
        set_level(logging.DEBUG)                # everything verbose
        set_level(logging.WARNING, "bbox_warning") # quiet one module only
    """
    if module is None:
        _root_logger.setLevel(level)
    else:
        logging.getLogger(f"{_ROOT_NAME}.{module}").setLevel(level)