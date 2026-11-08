"""Background render jobs: queue, status, cancellation (in memory, per server process).

* Jobs live only in this server process. They are lost, and running renders are stopped, when
  the server exits; nothing is persisted.
* At most ``max_concurrent`` renders run at once (``KDENLIVE_MCP_MAX_RENDERS``, default 1,
  maximum 4); further jobs wait in a bounded queue.
* A monitor thread starts queued jobs, finalizes finished ones (validation, atomic move,
  cleanup) and enforces each job's timeout.
* Cancellation is two-step: ``cancel_requested`` is set immediately, the state becomes
  ``cancelled`` only once the whole process group has exited and partial output is removed.
"""

from __future__ import annotations

import atexit
import itertools
import os
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.render import RenderPlan, RenderResult, RenderRun, RenderState

MAX_RENDERS_ENV = "KDENLIVE_MCP_MAX_RENDERS"
MAX_QUEUED = 20
MAX_KEPT_FINISHED = 50
POLL_INTERVAL = 0.25
PROGRESS_UNAVAILABLE = (
    "Kdenlive's command-line render does not report progress reliably; progress is only shown "
    "when kdenlive_render has written a progress line."
)


class Progress(BaseModel):
    percent: int
    frame: int
    source: str = "kdenlive_render log"


class JobInfo(BaseModel):
    job_id: str
    state: RenderState
    project: str
    output: str
    preset: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float | None = None
    queue_position: int | None = Field(default=None, description="1 = next to start.")
    cancel_requested: bool = False
    progress: Progress | None = None
    progress_note: str | None = None
    timeout_seconds: float
    result: RenderResult | None = Field(
        default=None, description="Full result (validation, log tail) once the job has ended."
    )


class JobList(BaseModel):
    jobs: list[JobInfo]
    max_concurrent: int
    notes: list[str]


_SEQUENCE = itertools.count()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


@dataclass
class _Job:
    job_id: str
    plan: RenderPlan
    timeout: float
    env: Mapping[str, str] | None
    created_at: str = field(default_factory=_now)
    seq: int = field(default_factory=lambda: next(_SEQUENCE))
    state: RenderState = RenderState.QUEUED
    run: RenderRun | None = None
    started_mono: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    timed_out: bool = False
    result: RenderResult | None = None


def max_concurrent_from_env(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(MAX_RENDERS_ENV, "1")
    try:
        return min(max(int(raw), 1), 4)
    except ValueError:
        return 1


class JobManager:
    def __init__(self, max_concurrent: int | None = None) -> None:
        self.max_concurrent = max_concurrent or max_concurrent_from_env()
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._monitor, name="render-jobs", daemon=True)
        self._thread.start()
        atexit.register(self.shutdown)

    # ------------------------------------------------------------ public API ----------
    def submit(
        self, plan: RenderPlan, timeout: float, env: Mapping[str, str] | None = None
    ) -> JobInfo:
        with self._lock:
            active = [
                j
                for j in self._jobs.values()
                if j.state in (RenderState.QUEUED, RenderState.RUNNING)
            ]
            if sum(j.state is RenderState.QUEUED for j in active) >= MAX_QUEUED:
                raise ProjectError(
                    "queue_full",
                    f"{MAX_QUEUED} render jobs are already queued.",
                    "Wait for jobs to finish or cancel some.",
                )
            if any(j.plan.output == plan.output for j in active):
                raise ProjectError(
                    "output_in_use", f"Another active job already renders to {plan.output}."
                )
            job = _Job(job_id=f"r-{uuid.uuid4().hex[:12]}", plan=plan, timeout=timeout, env=env)
            self._jobs[job.job_id] = job
            self._advance()
            return self._info(job)

    def get(self, job_id: str) -> JobInfo:
        with self._lock:
            self._advance()
            return self._info(self._find(job_id))

    def list(self) -> JobList:
        with self._lock:
            self._advance()
            jobs = sorted(self._jobs.values(), key=lambda j: j.seq, reverse=True)
            return JobList(
                jobs=[self._info(j, include_result=False) for j in jobs],
                max_concurrent=self.max_concurrent,
                notes=["Jobs are kept in memory only; they disappear when the server stops."],
            )

    def cancel(self, job_id: str) -> JobInfo:
        with self._lock:
            job = self._find(job_id)
            if job.state is RenderState.QUEUED:
                job.cancel_requested = True
                self._finish(job, cancelled=True, exit_code=None)
            elif job.state is RenderState.RUNNING:
                job.cancel_requested = True
                self._wake.set()
            return self._info(job)

    def wait(self, job_id: str, timeout: float) -> JobInfo:
        """Block until the job ends or `timeout` elapses (used by tests and sync callers)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = self.get(job_id)
            if info.state not in (RenderState.QUEUED, RenderState.RUNNING):
                return info
            time.sleep(POLL_INTERVAL)
        return self.get(job_id)

    def shutdown(self) -> None:
        """Stop every running render (server exit): no orphan processes, no partial files."""
        with self._lock:
            self._stopped = True
            for job in self._jobs.values():
                if job.state is RenderState.RUNNING and job.run and job.run.running:
                    code = job.run.running.terminate(grace=3)
                    self._finish(job, cancelled=True, exit_code=code)
                elif job.state is RenderState.QUEUED:
                    self._finish(job, cancelled=True, exit_code=None)
        self._wake.set()

    # ------------------------------------------------------------ internals -----------
    def _find(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise ProjectError(
                "unknown_job",
                f"No render job {job_id!r}.",
                "Jobs are lost when the server restarts; call list_render_jobs.",
            )
        return job

    def _monitor(self) -> None:
        while not self._stopped:
            self._wake.wait(POLL_INTERVAL)
            self._wake.clear()
            with self._lock:
                if not self._stopped:
                    self._advance()

    def _advance(self) -> None:
        """Finalize ended jobs, enforce timeouts and cancellations, start queued jobs."""
        for job in list(self._jobs.values()):
            if job.state is not RenderState.RUNNING or job.run is None or job.run.running is None:
                continue
            running = job.run.running
            code = running.poll()
            if code is not None:
                self._finish(job, cancelled=False, exit_code=code)
            elif job.cancel_requested:
                self._finish(job, cancelled=True, exit_code=running.terminate())
            elif job.started_mono and time.monotonic() - job.started_mono > job.timeout:
                job.timed_out = True
                self._finish(job, cancelled=True, exit_code=running.terminate())
        running_count = sum(j.state is RenderState.RUNNING for j in self._jobs.values())
        for job in sorted(self._jobs.values(), key=lambda j: j.seq):
            if running_count >= self.max_concurrent or self._stopped:
                break
            if job.state is RenderState.QUEUED and self._start(job):
                running_count += 1
        self._prune()

    def _start(self, job: _Job) -> bool:
        job.run = RenderRun(job.plan, job.env)
        try:
            job.run.start()
        except OSError as exc:
            job.run.cleanup()
            job.state, job.finished_at = RenderState.FAILED, _now()
            job.result = RenderResult(
                state=RenderState.FAILED,
                project=str(job.plan.project_path),
                project_sha256=job.plan.project_sha256,
                sequence=job.plan.sequence,
                output=str(job.plan.output),
                preset=job.plan.preset.name,
                process_completed=False,
                error=f"Cannot start Kdenlive: {exc.strerror or exc}.",
            )
            return False
        job.state, job.started_at, job.started_mono = RenderState.RUNNING, _now(), time.monotonic()
        return True

    def _finish(self, job: _Job, *, cancelled: bool, exit_code: int | None) -> None:
        if job.run is not None:
            job.result = job.run.finish(
                exit_code if exit_code is not None else -1, cancelled=cancelled
            )
        else:  # never started
            job.result = RenderResult(
                state=RenderState.CANCELLED,
                project=str(job.plan.project_path),
                project_sha256=job.plan.project_sha256,
                sequence=job.plan.sequence,
                output=str(job.plan.output),
                preset=job.plan.preset.name,
                process_completed=False,
                error="Cancelled before it started.",
            )
        if job.timed_out:
            job.result.state = RenderState.FAILED
            job.result.error = f"Render exceeded {job.timeout:g}s and was stopped."
        job.state = job.result.state
        job.finished_at = _now()

    def _prune(self) -> None:
        done = sorted(
            (
                j
                for j in self._jobs.values()
                if j.state not in (RenderState.QUEUED, RenderState.RUNNING)
            ),
            key=lambda j: j.finished_at or "",
        )
        for job in done[: max(len(done) - MAX_KEPT_FINISHED, 0)]:
            del self._jobs[job.job_id]

    def _info(self, job: _Job, include_result: bool = True) -> JobInfo:
        queued = sorted(
            (j for j in self._jobs.values() if j.state is RenderState.QUEUED),
            key=lambda j: j.seq,
        )
        position = next((i + 1 for i, j in enumerate(queued) if j is job), None)
        progress = None
        note = None
        if job.state is RenderState.RUNNING and job.run is not None:
            measured = job.run.progress()
            if measured:
                progress = Progress(percent=measured[0], frame=measured[1])
            else:
                note = PROGRESS_UNAVAILABLE
        elapsed = None
        if job.started_mono is not None:
            end = job.result.elapsed_seconds if job.result and job.result.elapsed_seconds else None
            elapsed = end if end is not None else round(time.monotonic() - job.started_mono, 3)
        return JobInfo(
            job_id=job.job_id,
            state=job.state,
            project=str(job.plan.project_path),
            output=str(job.plan.output),
            preset=job.plan.preset.name,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            elapsed_seconds=elapsed,
            queue_position=position,
            cancel_requested=job.cancel_requested,
            progress=progress,
            progress_note=note,
            timeout_seconds=job.timeout,
            result=job.result if include_result else None,
        )
