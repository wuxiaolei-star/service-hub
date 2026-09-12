# Task 7 Report — generic dual-runtime plugin publisher

## Summary

Implemented Task 7 only.

- Added `packages/hub-publisher` with project validation, deterministic archive creation, injected command-runner builders, and `hub-plugin build` CLI.
- Preserved NC `packages/nc-to-shp-plugin/scripts/package_build.py` as a backwards-compatible wrapper with the same arguments, package layout, and canonical filenames.
- Added focused publisher tests for project validation, legacy runtime config discovery, deterministic archives, unsafe member rejection, symlink rejection, injected conda/docker command flows, architecture validation, and NC wrapper compatibility.
- Updated root `pyproject.toml` to include `hub-publisher` on pytest/mypy paths and to declare `zstandard`.

## TDD Evidence

RED:

- `python -m pytest tests/publisher -q`
- Result: failed with `ModuleNotFoundError: No module named 'hub_publisher'`.

GREEN:

- Implemented `hub_publisher.project`, `archive`, `builder`, `cli`, and the NC wrapper.
- `python -m pytest tests/publisher tests/plugins/test_package_build.py -q`
- Result: `14 passed`.

Additional RED/GREEN refinement:

- Added a command-runner assertion that Docker digest inspection must explicitly capture stdout while other build commands do not.
- `python -m pytest tests/publisher/test_builder.py -q`
- Result before fix: failed because `docker image inspect` was not marked for stdout capture.
- Implemented `capture_stdout` on the injected runner protocol.
- Result after fix: `3 passed`.

## Verification

All commands used Python 3.12.14 from:

`C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`

Passed:

```text
python -m pytest tests/publisher tests/plugins/test_package_build.py -q
14 passed
```

```text
python -m ruff check packages/hub-publisher packages/nc-to-shp-plugin/scripts tests/publisher
All checks passed!
```

```text
python -m mypy packages/hub-publisher/src/hub_publisher
Success: no issues found in 5 source files
```

```text
python -m pytest tests/test_package_imports.py -q
1 passed
```

```text
git diff --check
exit 0
```

Attempted broader non-integration suite:

```text
python -m pytest -q
```

Result: blocked during collection by missing unrelated optional dependencies in the bundled Python environment:

- `tests/plugins/test_nc_to_shp.py`: `ModuleNotFoundError: No module named 'h5py'`
- `tests/server`: `ModuleNotFoundError: No module named 'fastapi'`

CLI help smoke checks passed with explicit source `PYTHONPATH` because the package is not installed in editable mode in this workspace:

```text
python -m hub_publisher.cli --help
python -m hub_publisher.cli build --help
```

## Real Runtime Builds

Docker and Conda commands are present on this machine:

- `docker version --format '{{.Server.Version}}'` -> `29.6.2`
- `conda --version` -> `conda 23.3.1`

Real conda-pack and Docker plugin builds were not run because the current host is Windows, while Task 7 requires native Linux target-runtime evidence. The conda builder intentionally rejects non-Linux native builds.

## Notes

- Initial baseline testing with the default `python` failed because it was Python 3.9 and could not import `typing.Self`. Verification was switched to the bundled Python 3.12 interpreter.
- The `requesting-code-review` skill normally dispatches a reviewer subagent, but this task explicitly prohibited subagents, so no reviewer subagent was spawned.
- A transient test-file placement mistake created an empty parent-level `tests/publisher` directory outside the worktree. The files were moved into the target worktree. The empty directory could not be removed due to local command policy and contains no files.
