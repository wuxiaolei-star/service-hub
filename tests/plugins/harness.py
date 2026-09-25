"""Plugin conformance harness: one case = (project, inputs, params, assertions).

A conformance case runs a plugin project through the real runner protocol via
``hub_publisher.dev.run_plugin_job`` (python mode; no Docker required — the
conda-vs-docker runtime comparison stays behind the integration marker in
``tests/integration/test_dual_runtime_nc_to_shp.py``), then applies the
built-in hooks and finally the case's own assertions callback:

- built-in: ``result.json`` satisfies the job protocol contract and succeeded;
- built-in: every declared output file exists under the workspace and is
  non-empty;
- built-in: a second independent run produces the same outputs — ZIP outputs
  through the platform comparator (``hub_publisher.conformance``, including
  per-member SHA-256), other files bytewise;
- per case: the ``assertions`` callback checks plugin-specific expectations
  (manifest contents, result data, error codes, ...).

Onboarding a plugin means adding one ``ConformanceCase`` to
``test_conformance.py`` — a project directory, an input-file builder, the
parameter dict and the plugin-specific assertions; execution and the built-in
hooks are shared.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from hub_publisher.conformance import ArchiveComparison, compare_output_archives
from hub_publisher.dev import LocalJobOutcome, run_plugin_job
from hub_publisher.project import PluginProject
from python_hub_contracts import JobResult, JobStatus

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

InputBuilder = Callable[[Path], list[Path]]
RunAssertions = Callable[["PluginRun"], None]


@dataclass(frozen=True, slots=True)
class ConformanceCase:
    """One conformance scenario: a plugin project plus its expected behavior."""

    name: str
    project_dir: Path
    #: Typed parameters delivered to the plugin exactly as the Hub would.
    params: dict[str, object]
    #: Creates the case's input files under the given directory and returns them.
    build_inputs: InputBuilder
    #: Plugin-specific expectations, checked after the built-in hooks.
    assertions: RunAssertions
    #: Modules that must be importable to run this case (heavy plugin-only deps);
    #: missing modules skip the case, matching the existing plugin-test policy.
    requires: tuple[str, ...] = ()
    #: Run the job twice and compare all outputs (determinism hook).
    compare_runs: bool = True

    def skip_unless_runnable(self) -> None:
        for module_name in self.requires:
            pytest.importorskip(
                module_name, reason=f"{module_name} is a plugin-only dependency"
            )


@dataclass(frozen=True, slots=True)
class PluginRun:
    """One executed job plus the staged workspace it ran in."""

    workspace: Path
    result: JobResult

    @property
    def output_dir(self) -> Path:
        return self.workspace / "output"

    def output_file(self, path: str) -> Path:
        """Resolve one declared output path (a ``result.files[].path`` value)."""
        candidate = (self.output_dir / path).resolve()
        if not candidate.is_relative_to(self.output_dir.resolve()):
            raise ValueError(f"output path escapes the workspace: {path}")
        return candidate


@dataclass(frozen=True, slots=True)
class ConformanceOutcome:
    """Everything one case execution produced, for case-level introspection."""

    case: ConformanceCase
    runs: tuple[PluginRun, ...]
    #: ZIP-output comparisons between the two runs, keyed by declared output path.
    comparisons: dict[str, ArchiveComparison]


def run_case(case: ConformanceCase, tmp_path: Path) -> ConformanceOutcome:
    """Execute one conformance case and apply every built-in hook."""
    case.skip_unless_runnable()
    project = PluginProject.load(case.project_dir)
    _assert_params_declared(project, case.params)
    input_files = case.build_inputs(tmp_path / "inputs")
    runs = []
    for index in range(2 if case.compare_runs else 1):
        workspace = tmp_path / f"run-{index}"
        workspace.mkdir()
        outcome = run_plugin_job(
            project=project,
            workspace_root=workspace,
            params=case.params,
            inputs=input_files,
        )
        run = PluginRun(workspace=workspace, result=assert_result_contract(outcome))
        assert_outputs_present(run)
        runs.append(run)
    comparisons = assert_runs_equal(runs[0], runs[1]) if len(runs) > 1 else {}
    case.assertions(runs[0])
    return ConformanceOutcome(case=case, runs=tuple(runs), comparisons=comparisons)


def assert_result_contract(outcome: LocalJobOutcome) -> JobResult:
    """Built-in hook: result.json satisfied the protocol contract and succeeded."""
    if outcome.result is None:
        raise AssertionError(_failure_context(outcome))
    if outcome.exit_code != 0 or outcome.result.status is not JobStatus.SUCCESS:
        raise AssertionError(
            f"{_failure_context(outcome)}job did not succeed: "
            f"status={outcome.result.status.value} exit_code={outcome.exit_code}"
        )
    return outcome.result


def assert_outputs_present(run: PluginRun) -> None:
    """Built-in hook: every declared output file exists and is non-empty."""
    for item in run.result.files:
        path = run.output_file(item.path)
        assert path.is_file(), f"declared output missing: {item.path}"
        assert path.stat().st_size > 0, f"declared output is empty: {item.path}"


def assert_runs_equal(left: PluginRun, right: PluginRun) -> dict[str, ArchiveComparison]:
    """Built-in hook: two runs must declare the same outputs with equal content.

    ZIP outputs go through the platform comparator (structure, manifest and
    per-member SHA-256); any other output file must match bytewise. Returns the
    per-output ZIP comparisons.
    """
    left_files = {item.path: item for item in left.result.files}
    right_files = {item.path: item for item in right.result.files}
    assert set(left_files) == set(right_files), (
        f"declared outputs differ between runs: {sorted(left_files)} != {sorted(right_files)}"
    )
    comparisons: dict[str, ArchiveComparison] = {}
    for path in sorted(left_files):
        left_output = left.output_file(path)
        right_output = right.output_file(path)
        if zipfile.is_zipfile(left_output):
            comparison = compare_output_archives(left_output, right_output)
            assert comparison.equal, "\n".join(comparison.failure_lines())
            comparisons[path] = comparison
        else:
            assert left_output.read_bytes() == right_output.read_bytes(), (
                f"output {path} differs between runs"
            )
    return comparisons


def _assert_params_declared(project: PluginProject, params: dict[str, object]) -> None:
    declared = {parameter.name: parameter for parameter in project.manifest.parameters}
    unknown = sorted(set(params) - set(declared))
    assert not unknown, f"case params not declared in plugin.yaml: {unknown}"
    missing = sorted(
        name for name, spec in declared.items() if spec.required and name not in params
    )
    assert not missing, f"case params miss required plugin parameters: {missing}"


def _failure_context(outcome: LocalJobOutcome) -> str:
    """Collect the debuggable context of a failed run: stderr and log tails."""
    lines = [
        f"conformance run failed: workspace={outcome.workspace} job={outcome.job_id}",
        f"contract: {outcome.contract_error or '(result.json parsed)'}",
    ]
    if outcome.runner_stderr.strip():
        lines.append(f"runner stderr tail: {outcome.runner_stderr.strip()[-2000:]}")
    runner_log = outcome.workspace / "logs" / "runner.log"
    if runner_log.is_file():
        tail = runner_log.read_text("utf-8", errors="replace").splitlines()[-10:]
        lines.append("runner.log tail:\n" + "\n".join(f"  {line}" for line in tail))
    return "\n".join(lines) + "\n"
