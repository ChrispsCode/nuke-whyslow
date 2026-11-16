"""Nuke progress bar for scans, so a slow session shows something is happening."""

from __future__ import annotations

from whyslow import logger


class NukeProgress:
    """Callable progress reporter for runner.run_checks, backed by nuke.ProgressTask.

    Does nothing outside the GUI. Use as a context manager: the bar only closes
    when the ProgressTask object is destroyed (nuke-dev api-quirks).
    """

    def __init__(self, title: str):
        self._task = None
        try:
            import nuke
            if nuke.GUI:
                self._task = nuke.ProgressTask(title)
        except Exception as error:
            logger.warning(f"No progress bar: {error}")

    def __call__(self, fraction: float, message: str) -> bool:
        """Update the bar; returns True if the user pressed Cancel."""
        if self._task is None:
            return False
        self._task.setMessage(message)
        self._task.setProgress(int(fraction * 100))
        return bool(self._task.isCancelled())

    def close(self) -> None:
        if self._task is not None:
            task, self._task = self._task, None
            del task

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
