"""Runtime context and safe filesystem helpers for plugin execution."""

from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Protocol

from .errors import PluginCancelledError


class PluginLogger(Protocol):
    """Logger interface made available to plugin implementations."""

    def debug(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...


class EventSink(Protocol):
    """Adapter for reporting plugin execution events to the host."""

    def emit_progress(self, percent: int, message: str | None) -> None: ...


class PluginContext:
    """Host-provided dependencies and per-job paths for a plugin invocation."""

    def __init__(
        self,
        *,
        job_id: str,
        plugin_id: str,
        plugin_version: str,
        input_dir: Path,
        work_dir: Path,
        output_dir: Path,
        logger: PluginLogger,
        event_sink: EventSink,
        cancellation_probe: Callable[[], bool],
    ) -> None:
        self.job_id = job_id
        self.plugin_id = plugin_id
        self.plugin_version = plugin_version
        self.input_dir = input_dir
        self.work_dir = work_dir
        self.output_dir = output_dir
        self.logger = logger
        self._event_sink = event_sink
        self._cancellation_probe = cancellation_probe

    def progress(self, percent: int, message: str | None = None) -> None:
        """Report job progress as an inclusive percentage."""
        if not 0 <= percent <= 100:
            raise ValueError("progress percent must be between 0 and 100")
        self._event_sink.emit_progress(percent, message)

    def is_cancelled(self) -> bool:
        """Return the current cancellation request state from the host."""
        return self._cancellation_probe()

    def check_cancelled(self) -> None:
        """Raise the SDK cancellation error when the host has cancelled the job."""
        if self.is_cancelled():
            raise PluginCancelledError()

    def output_file(self, path: str, *, create_parent: bool = False) -> Path:
        """Return a safely contained output file path for this job."""
        return self._contained_file(self.output_dir, path, create_parent=create_parent)

    def work_file(self, path: str, *, create_parent: bool = False) -> Path:
        """Return a safely contained work file path for this job."""
        return self._contained_file(self.work_dir, path, create_parent=create_parent)

    @staticmethod
    def _contained_file(root: Path, path: str, *, create_parent: bool) -> Path:
        requested = Path(path)
        if requested.is_absolute() or PureWindowsPath(path).drive or ".." in requested.parts:
            raise ValueError("file path must be a relative path without traversal")

        resolved_root = root.resolve()
        candidate = (resolved_root / requested).resolve(strict=False)
        PluginContext._require_contained(candidate, resolved_root)

        if create_parent:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate = candidate.resolve(strict=False)
            PluginContext._require_contained(candidate, resolved_root)

        return candidate

    @staticmethod
    def _require_contained(candidate: Path, root: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("file path must remain inside the job root") from exc
