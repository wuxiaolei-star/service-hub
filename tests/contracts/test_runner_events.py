import pytest
from python_hub_contracts import LogEvent, ProgressEvent, RunnerEventParseError, parse_runner_line


def test_parse_progress_event() -> None:
    event = parse_runner_line(
        '@@HUB@@{"protocol_version":"1.0","type":"progress",'
        '"percent":50,"message":"处理中"}'
    )

    assert isinstance(event, ProgressEvent)
    assert event.percent == 50


def test_plain_stdout_is_not_an_event() -> None:
    assert parse_runner_line("ordinary plugin output") is None


@pytest.mark.parametrize("percent", [-1, 101])
def test_progress_percent_must_be_within_protocol_bounds(percent: int) -> None:
    with pytest.raises(RunnerEventParseError) as error:
        parse_runner_line(
            f'@@HUB@@{{"protocol_version":"1.0","type":"progress",'
            f'"percent":{percent},"message":"working"}}'
        )

    assert error.value.code == "RUNNER_EVENT_INVALID"


def test_parse_log_event_with_supported_level() -> None:
    event = parse_runner_line(
        '@@HUB@@{"protocol_version":"1.0","type":"log",'
        '"level":"INFO","message":"读取NC完成"}'
    )

    assert isinstance(event, LogEvent)
    assert event.level.value == "INFO"


def test_log_event_rejects_unsupported_level() -> None:
    with pytest.raises(RunnerEventParseError) as error:
        parse_runner_line(
            '@@HUB@@{"protocol_version":"1.0","type":"log",'
            '"level":"TRACE","message":"too verbose"}'
        )

    assert error.value.code == "RUNNER_EVENT_INVALID"


@pytest.mark.parametrize(
    "line",
    [
        "@@HUB@@not-json",
        '@@HUB@@{"protocol_version":"1.0","type":"artifact"}',
    ],
)
def test_invalid_prefixed_protocol_line_raises_stable_error(line: str) -> None:
    with pytest.raises(RunnerEventParseError) as error:
        parse_runner_line(line)

    assert error.value.code == "RUNNER_EVENT_INVALID"
