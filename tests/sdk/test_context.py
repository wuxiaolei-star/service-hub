import logging
from pathlib import Path

import pytest
from python_hub_sdk import PluginCancelledError, PluginContext


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[dict[str, int | str | None]] = []

    def emit_progress(self, percent: int, message: str | None) -> None:
        self.events.append({"percent": percent, "message": message})


class NullLogger:
    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


def _context(
    tmp_path: Path,
    *,
    cancelled: bool = False,
    event_sink: RecordingEventSink | None = None,
) -> PluginContext:
    return PluginContext(
        job_id="job-1",
        plugin_id="example-plugin",
        plugin_version="1.2.3",
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        logger=NullLogger(),
        event_sink=event_sink or RecordingEventSink(),
        cancellation_probe=lambda: cancelled,
    )


def test_progress_emits_exact_event_dictionaries(tmp_path: Path) -> None:
    sink = RecordingEventSink()
    context = _context(tmp_path, event_sink=sink)

    context.progress(0)
    context.progress(100, "done")

    assert sink.events == [
        {"percent": 0, "message": None},
        {"percent": 100, "message": "done"},
    ]


@pytest.mark.parametrize("percent", [-1, 101])
def test_progress_rejects_percent_outside_inclusive_range(tmp_path: Path, percent: int) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError):
        context.progress(percent)


@pytest.mark.parametrize("percent", [True, 50.0, "50", None])
def test_progress_rejects_non_integer_runtime_values(tmp_path: Path, percent: object) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError):
        context.progress(percent)  # type: ignore[arg-type]


@pytest.mark.parametrize("message", ["", 1])
def test_progress_rejects_invalid_optional_messages(tmp_path: Path, message: object) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError):
        context.progress(50, message)  # type: ignore[arg-type]


def test_context_accepts_standard_logger_format_arguments(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("python_hub_sdk.compatibility")
    context = PluginContext(
        job_id="job-1",
        plugin_id="example-plugin",
        plugin_version="1.2.3",
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        logger=logger,
        event_sink=RecordingEventSink(),
        cancellation_probe=lambda: False,
    )

    with caplog.at_level(logging.INFO, logger=logger.name):
        context.logger.info("processing %s", "input.nc")

    assert "processing input.nc" in caplog.messages


def test_is_cancelled_delegates_to_probe(tmp_path: Path) -> None:
    probes = 0

    def probe() -> bool:
        nonlocal probes
        probes += 1
        return True

    context = PluginContext(
        job_id="job-1",
        plugin_id="example-plugin",
        plugin_version="1.2.3",
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        logger=NullLogger(),
        event_sink=RecordingEventSink(),
        cancellation_probe=probe,
    )

    assert context.is_cancelled() is True
    assert probes == 1


def test_check_cancelled_only_raises_when_probe_requests_it(tmp_path: Path) -> None:
    _context(tmp_path, cancelled=False).check_cancelled()

    with pytest.raises(PluginCancelledError):
        _context(tmp_path, cancelled=True).check_cancelled()


def test_output_file_resolves_below_output_root(tmp_path: Path) -> None:
    context = _context(tmp_path)

    assert context.output_file("result.zip") == (tmp_path / "output" / "result.zip").resolve()


@pytest.mark.parametrize("path", ["/tmp/result.zip", "../result.zip", "nested/../result.zip"])
def test_output_file_rejects_absolute_and_traversal_paths(tmp_path: Path, path: str) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError):
        context.output_file(path)


@pytest.mark.parametrize("path", ["C:result.zip", "C:/result.zip"])
def test_path_helpers_reject_drive_qualified_paths(tmp_path: Path, path: str) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError):
        context.output_file(path)
    with pytest.raises(ValueError):
        context.work_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "nested//result.zip",
        "nested/./result.zip",
        "nested/\x00result.zip",
        "nested\\result.zip",
        "a" * 1025,
    ],
)
def test_path_helpers_reject_non_protocol_lexical_paths(tmp_path: Path, path: str) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValueError):
        context.output_file(path)
    with pytest.raises(ValueError):
        context.work_file(path)


def test_output_file_rejects_existing_symlink_that_escapes_root(tmp_path: Path) -> None:
    context = _context(tmp_path)
    output_root = tmp_path / "output"
    outside_root = tmp_path / "outside"
    output_root.mkdir()
    outside_root.mkdir()
    try:
        (output_root / "escape").symlink_to(outside_root, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlink fixtures requires Windows symlink permission")

    with pytest.raises(ValueError):
        context.output_file("escape/result.zip")


def test_path_helpers_only_create_parents_when_requested(tmp_path: Path) -> None:
    context = _context(tmp_path)

    output_path = context.output_file("without-parent/result.zip")
    work_path = context.work_file("with-parent/scratch.txt", create_parent=True)

    assert not output_path.parent.exists()
    assert work_path.parent.is_dir()
