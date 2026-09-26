"""Structured failure classification for terminal Job outcomes (G8/B8).

Zero-migration design: the failure class is never stored. It is derived from
the data a Job row already carries — ``status`` and ``error_summary`` — with
the writing source as a tiebreaker for codes the Hub itself never emits. Two
conventions make the derivation reliable:

- The completion path normalizes every runner-reported error through
  :func:`failure_summary`, so stored summaries begin with the stable
  ``CODE: message`` prefix (matching the pre-existing ``PLUGIN_OUTPUT_MISSING:
  ...`` convention).
- The reaper and the reconcile path write fixed, recognizable summaries (see
  ``services/reaper.py`` and ``services/runner_operations.py``).

``/jobs/stats`` (``by=plugin``) consumes :func:`classify_failure` over stored
rows; the reaper records the class in its audit detail. Adding a Job column
would have required an Alembic migration for a value that is a pure function
of data already persisted, so the review plan picked derivation instead.
"""

from __future__ import annotations

import re

from python_hub_contracts import JobError, JobStatus

FAILURE_CLASS_PLUGIN = "plugin"
FAILURE_CLASS_TIMEOUT = "timeout"
FAILURE_CLASS_CANCELLED = "cancelled"
FAILURE_CLASS_RUNNER = "runner"
FAILURE_CLASS_SYSTEM = "system"
FAILURE_CLASSES: tuple[str, ...] = (
    FAILURE_CLASS_PLUGIN,
    FAILURE_CLASS_TIMEOUT,
    FAILURE_CLASS_CANCELLED,
    FAILURE_CLASS_RUNNER,
    FAILURE_CLASS_SYSTEM,
)

# Sources that write terminal Job outcomes. Stored rows do not record their
# writer, but every writer's summary is recognizable (module docstring), so
# read paths classify rows with the RUNNER_SOURCE default: an arbitrary code
# prefix can only have come from a runner-reported plugin error.
RUNNER_SOURCE = "runner"
REAPER_SOURCE = "reaper"
RECONCILE_SOURCE = "reconcile"
HUB_SOURCE = "hub"

# The Hub-side output-contract enforcement code (G6/R5) — a required output the
# plugin never produced is attributed to the plugin itself.
PLUGIN_OUTPUT_MISSING = "PLUGIN_OUTPUT_MISSING"

_TIMEOUT_ERROR_CODES = frozenset({"JOB_TIMED_OUT"})
_RUNNER_ERROR_CODES = frozenset({"RUNNER_FAILED", "HUB_RESTARTED"})

# Matches the "CODE: message" convention and bare codes such as
# "JOB_TIMED_OUT"/"HUB_RESTARTED". Prose never matches: the leading letter run
# must be followed by a colon or the end of the summary ("Docker container
# exited with status 1" therefore carries no code).
_CODE_PREFIX = re.compile(r"^([A-Z][A-Z0-9_]{2,63})(?::|\Z)")

_FAILURE_STATUSES = frozenset(
    {JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.CANCELLED}
)


def failure_summary(error: JobError | None) -> str | None:
    """Normalize a runner-reported ``JobError`` into the stored summary text.

    The completion path funnels every runner-reported error through this
    helper so stored summaries always begin with the stable error code — the
    prefix :func:`classify_failure` derives the failure class from.
    """
    if error is None:
        return None
    return f"{error.code}: {error.message}"


def classify_failure(
    status: JobStatus | str,
    error_summary: str | None,
    source: str = RUNNER_SOURCE,
) -> str:
    """Derive the failure class of one terminal-failure Job outcome.

    Rules (first match wins):

    1. ``CANCELLED`` -> ``cancelled``; ``TIMED_OUT`` -> ``timeout`` (the reaper
       and both executors only ever mean timeout at this status, whatever the
       summary says).
    2. ``FAILED`` -> decided by the ``error_summary`` code prefix:
       - ``JOB_TIMED_OUT`` -> ``timeout``;
       - ``RUNNER_FAILED`` / ``HUB_RESTARTED`` -> ``runner``;
       - ``PLUGIN_OUTPUT_MISSING`` or any ``PLUGIN_*`` -> ``plugin``;
       - any other code prefix -> ``plugin`` when the writer is the runner
         (only the plugin SDK's ``PluginError`` produces arbitrary codes;
         anything else attributes to ``system``);
       - no code prefix or no summary -> ``system``.

    ``SUCCESS`` and non-terminal statuses have no failure class; passing one
    raises ``ValueError``. Accepts the ``JobStatus`` enum or its raw string
    value as stored in the ``jobs`` table.
    """
    resolved = JobStatus(status) if isinstance(status, str) else status
    if resolved not in _FAILURE_STATUSES:
        raise ValueError(
            f"only terminal failure outcomes have a failure class, got {resolved}"
        )
    if resolved is JobStatus.CANCELLED:
        return FAILURE_CLASS_CANCELLED
    if resolved is JobStatus.TIMED_OUT:
        return FAILURE_CLASS_TIMEOUT
    match = _CODE_PREFIX.match(error_summary) if error_summary else None
    code = match.group(1) if match else None
    if code is None:
        return FAILURE_CLASS_SYSTEM
    if code in _TIMEOUT_ERROR_CODES:
        return FAILURE_CLASS_TIMEOUT
    if code in _RUNNER_ERROR_CODES:
        return FAILURE_CLASS_RUNNER
    if code == PLUGIN_OUTPUT_MISSING or code.startswith("PLUGIN_"):
        return FAILURE_CLASS_PLUGIN
    if source == RUNNER_SOURCE:
        return FAILURE_CLASS_PLUGIN
    return FAILURE_CLASS_SYSTEM
