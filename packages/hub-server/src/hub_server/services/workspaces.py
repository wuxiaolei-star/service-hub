"""Immutable Job workspace preparation and final output registration."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, timedelta
from pathlib import Path, PurePosixPath
from typing import Final
from uuid import uuid4

from python_hub_contracts import (
    JobRuntimeSpec,
    RuntimeDirectories,
    RuntimeExecution,
    RuntimeInputFile,
    RuntimeInputValue,
    RuntimeJob,
    RuntimePlugin,
)
from sqlalchemy.orm import object_session

from hub_server.errors import HubError
from hub_server.models import FileRecord, Job, JobFile
from hub_server.storage import LocalStorage

_CHUNK_SIZE_BYTES: Final = 1024 * 1024
_LOGICAL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DEFAULT_TEMPORARY_MAX_AGE: Final = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class StagedWorkspace:
    """One prepared-but-not-yet-visible Job workspace waiting for install."""

    temporary: Path
    destination_relative: str
    spec: JobRuntimeSpec


@contextmanager
def _no_autoflush(job: Job) -> Iterator[None]:
    """Suppress autoflush while an unpersisted Job drives read-only lazy loads.

    Building the runtime spec walks ``job.plugin_build.plugin_version`` with
    lazy loading; the implicit autoflush would try to cascade the not-yet-added
    Job into the session mid-read and log SAWarning noise. The owning session
    (located through the attached build) is only borrowed for reads here.
    """
    session = object_session(job) or object_session(job.plugin_build)
    if session is None:
        yield
        return
    with session.no_autoflush:
        yield


class JobWorkspaceService:
    """Prepare controlled Job directories and persist verified final outputs."""

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def stage(self, job: Job, input_files: list[FileRecord]) -> StagedWorkspace:
        """Copy inputs and build the runtime spec without holding a DB write lock.

        Input files can be large, so this phase must run outside the Job creation
        transaction: it only requires ``job.job_key`` and ``job.created_at`` to be
        assigned and never touches the database. Call :meth:`install` inside the
        transaction to make the workspace visible atomically.
        """
        if job.job_key is None or job.created_at is None:
            raise ValueError("job must have a job_key and created_at before staging")
        destination_relative = f"jobs/{job.job_key}"
        temporary = self._storage.create_temporary_directory("jobs")
        try:
            for child in ("input", "work", "output", "logs"):
                (temporary / child).mkdir()
            with _no_autoflush(job):
                runtime_inputs = self._prepare_inputs(temporary, job.inputs_json, input_files)
                spec = self._runtime_spec(job, runtime_inputs)
            document = spec.model_dump_json(indent=2)
            (temporary / "job.json").write_text(document, encoding="utf-8", newline="\n")
        except Exception:
            self._storage.discard_temporary_directory(temporary)
            raise
        return StagedWorkspace(
            temporary=temporary, destination_relative=destination_relative, spec=spec
        )

    def install(self, staged: StagedWorkspace, job: Job) -> Path:
        """Atomically rename the staged workspace and write back Job metadata."""
        try:
            workspace = self._storage.install_directory(
                staged.temporary, staged.destination_relative
            )
        except Exception:
            self._storage.discard_temporary_directory(staged.temporary)
            raise
        job.workspace_path = staged.destination_relative
        job.job_json = staged.spec.model_dump(mode="json")
        return workspace

    def discard(self, staged: StagedWorkspace) -> None:
        """Discard a staged workspace that will not be installed."""
        self._storage.discard_temporary_directory(staged.temporary)

    def sweep_stale_temporaries(self, *, max_age: timedelta = _DEFAULT_TEMPORARY_MAX_AGE) -> int:
        """Remove ``.tmp-`` directories under ``jobs/`` older than ``max_age``.

        A crash between :meth:`stage` and :meth:`install` leaves its staged copy
        behind; this startup sweep bounds the orphan storage those leaks consume.
        Returns the number of removed directories.
        """
        jobs_root = self._storage.open_relative("jobs")
        if not jobs_root.exists():
            return 0
        cutoff = time.time() - max_age.total_seconds()
        removed = 0
        for child in jobs_root.iterdir():
            if not child.name.startswith(".tmp-") or not child.is_dir():
                continue
            try:
                if child.stat().st_mtime <= cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed

    def resolve_output(self, workspace: Path, relative_path: str) -> Path:
        """Resolve a runner path only when its final target stays below output/."""
        normalized = self._normalize_relative_path(relative_path)
        jobs_root = self._storage.open_relative("jobs")
        workspace_root = workspace.resolve()
        try:
            workspace_root.relative_to(jobs_root)
        except ValueError as error:
            raise ValueError("workspace must stay below Hub jobs storage") from error
        output_root = (workspace_root / "output").resolve()
        candidate = (output_root / PurePosixPath(normalized)).resolve()
        try:
            candidate.relative_to(output_root)
        except ValueError as error:
            raise ValueError("output path must stay below the Job output directory") from error
        return candidate

    def register_output(
        self,
        job: Job,
        *,
        logical_name: str,
        relative_path: str,
        mime_type: str | None = None,
    ) -> FileRecord:
        """Hash one final output completely before committing its metadata snapshot."""
        if not _LOGICAL_NAME_PATTERN.fullmatch(logical_name):
            raise ValueError("output logical name must be a protocol identifier")
        session = object_session(job)
        if job.id is None or session is None:
            raise ValueError("job must be attached to a persistence session")
        expected_workspace = f"jobs/{job.job_key}"
        if job.workspace_path != expected_workspace:
            raise ValueError("job workspace has not been prepared")
        workspace = self._storage.open_relative(expected_workspace)
        output = self.resolve_output(workspace, relative_path)
        if not output.is_file():
            raise HubError(
                code="JOB_OUTPUT_NOT_FOUND",
                message="Job 输出文件不存在",
                status_code=422,
            )
        size_bytes, sha256 = self._hash_file(output)
        if size_bytes <= 0:
            raise HubError(
                code="JOB_OUTPUT_INVALID",
                message="Job 输出文件为空",
                status_code=422,
            )

        normalized = self._normalize_relative_path(relative_path)
        filename = PurePosixPath(normalized).name
        record = FileRecord(
            file_key=f"file_{uuid4().hex}",
            scope="JOB",
            role="OUTPUT",
            logical_name=logical_name,
            original_filename=filename,
            relative_path=self._storage.relative_path(output),
            extension=Path(filename).suffix.lower() or None,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            status="AVAILABLE",
        )
        association = JobFile(
            job=job,
            file_record=record,
            role="OUTPUT",
            logical_name=logical_name,
            file_snapshot_json={
                "file_id": record.file_key,
                "name": filename,
                "path": f"output/{normalized}",
                "size": size_bytes,
                "extension": record.extension,
                "sha256": sha256,
            },
        )
        try:
            session.add_all([record, association])
            session.commit()
        except Exception as error:
            session.rollback()
            raise HubError(
                code="UNEXPECTED_ERROR",
                message="保存 Job 输出元数据失败",
                status_code=500,
            ) from error
        return record

    def _prepare_inputs(
        self,
        workspace: Path,
        inputs_json: Mapping[str, object],
        input_files: list[FileRecord],
    ) -> dict[str, RuntimeInputValue]:
        records: dict[str, FileRecord] = {}
        for record in input_files:
            if record.file_key in records:
                raise ValueError("input file records must be unique")
            records[record.file_key] = record

        copied: dict[str, RuntimeInputFile] = {}
        destination_names: dict[str, str] = {}
        runtime_inputs: dict[str, RuntimeInputValue] = {}
        for logical_name, raw_value in inputs_json.items():
            file_keys = self._input_file_keys(raw_value)
            runtime_files: list[RuntimeInputFile] = []
            for file_key in file_keys:
                try:
                    record = records[file_key]
                except KeyError as error:
                    raise self._file_not_found(file_key) from error
                runtime_file = copied.get(file_key)
                if runtime_file is None:
                    runtime_file = self._copy_input(workspace, record, destination_names)
                    copied[file_key] = runtime_file
                runtime_files.append(runtime_file)
            runtime_inputs[logical_name] = (
                runtime_files[0] if isinstance(raw_value, str) else runtime_files
            )

        if set(records) != set(copied):
            raise ValueError("input files must exactly match the Job input snapshot")
        return runtime_inputs

    def _copy_input(
        self,
        workspace: Path,
        record: FileRecord,
        destination_names: dict[str, str],
    ) -> RuntimeInputFile:
        if record.status != "AVAILABLE":
            raise self._file_not_found(record.file_key)
        name = self._safe_filename(record.logical_name)
        previous_file_key = destination_names.setdefault(name, record.file_key)
        if previous_file_key != record.file_key:
            raise ValueError("input logical filenames must be unique")
        source = self._storage.open_relative(record.relative_path)
        if not source.is_file():
            raise self._file_not_found(record.file_key)
        size_bytes, sha256 = self._hash_file(source)
        if size_bytes != record.size_bytes or sha256 != record.sha256:
            raise HubError(
                code="FILE_INTEGRITY_MISMATCH",
                message="输入文件完整性校验失败",
                status_code=422,
            )
        extension = record.extension or Path(name).suffix.lower()
        if not extension:
            raise ValueError("input files must have an extension")
        destination = workspace / "input" / name
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)
        return RuntimeInputFile(
            id=record.file_key,
            name=name,
            path=f"input/{name}",
            size=size_bytes,
            extension=extension,
            sha256=sha256,
        )

    @staticmethod
    def _runtime_spec(
        job: Job, inputs: dict[str, RuntimeInputValue]
    ) -> JobRuntimeSpec:
        created_at = job.created_at
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            created_at = created_at.replace(tzinfo=UTC)
        plugin_version = job.plugin_build.plugin_version
        return JobRuntimeSpec(
            protocol_version="1.0",
            job=RuntimeJob(id=job.job_key, created_at=created_at),
            plugin=RuntimePlugin(
                id=plugin_version.plugin.plugin_key,
                version=plugin_version.version,
                build_id=job.plugin_build.build_key,
            ),
            params=job.params_json,
            inputs=inputs,
            directories=RuntimeDirectories(
                input="input", work="work", output="output", logs="logs"
            ),
            execution=RuntimeExecution(timeout=job.timeout_seconds),
        )

    @staticmethod
    def _input_file_keys(value: object) -> list[str]:
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list) and value and all(
            isinstance(item, str) and item for item in value
        ):
            return value
        raise ValueError("Job inputs must contain a file ID or non-empty file ID list")

    @staticmethod
    def _safe_filename(filename: str) -> str:
        normalized = filename.replace("\\", "/")
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\x00" in normalized
        ):
            raise ValueError("input logical name must be a safe filename")
        return normalized

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        if not isinstance(relative_path, str):
            raise ValueError("output path must be a relative protocol path")
        normalized = relative_path.replace("\\", "/")
        if (
            not normalized
            or "\x00" in normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise ValueError("output path must be a relative protocol path")
        path = PurePosixPath(normalized)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("output path must not contain unsafe segments")
        normalized = path.as_posix()
        if len(normalized) > 1024:
            raise ValueError("output path is too long")
        return normalized

    @staticmethod
    def _hash_file(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as payload:
            before = os.fstat(payload.fileno())
            while chunk := payload.read(_CHUNK_SIZE_BYTES):
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(payload.fileno())
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise HubError(
                code="FILE_INTEGRITY_MISMATCH",
                message="文件在完整性校验期间发生变化",
                status_code=422,
            )
        return size, digest.hexdigest()

    @staticmethod
    def _file_not_found(file_key: str) -> HubError:
        return HubError(
            code="FILE_NOT_FOUND",
            message=f"文件 {file_key} 不存在",
            status_code=404,
        )
