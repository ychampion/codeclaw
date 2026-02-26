"""Background job queue for non-blocking TUI operations."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from queue import Queue
from typing import Any, Callable
from uuid import uuid4

from .types import JobEvent, JobInfo, utc_now_iso


class JobContext:
    """Context object passed to background tasks."""

    def __init__(self, job_id: str, manager: "JobManager", cancel_event: threading.Event) -> None:
        self.job_id = job_id
        self._manager = manager
        self._cancel_event = cancel_event

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def progress(self, value: float, message: str = "") -> None:
        self._manager._update_progress(self.job_id, value=value, message=message)


class JobManager:
    """Thread-backed job manager with pollable event stream."""

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="codeclaw-tui")
        self._lock = threading.Lock()
        self._jobs: dict[str, JobInfo] = {}
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._events: Queue[JobEvent] = Queue()

    def submit(self, name: str, fn: Callable[[JobContext], Any]) -> JobInfo:
        job_id = uuid4().hex[:8]
        created = utc_now_iso()
        info = JobInfo(id=job_id, name=name, status="queued", created_at=created, message="queued")
        cancel_event = threading.Event()
        with self._lock:
            self._jobs[job_id] = info
            self._cancel_events[job_id] = cancel_event
        self._events.put(JobEvent(job_id=job_id, kind="queued", message=f"{name} queued"))
        future = self._executor.submit(self._run_job, job_id, fn)
        with self._lock:
            self._futures[job_id] = future
        return replace(info)

    def _run_job(self, job_id: str, fn: Callable[[JobContext], Any]) -> None:
        cancel_event = self._cancel_events[job_id]
        with self._lock:
            job = self._jobs[job_id]
            self._jobs[job_id] = replace(
                job,
                status="running",
                started_at=utc_now_iso(),
                message="running",
            )
        self._events.put(JobEvent(job_id=job_id, kind="started", message="started", progress=0.0))

        ctx = JobContext(job_id=job_id, manager=self, cancel_event=cancel_event)
        try:
            result = fn(ctx)
            if cancel_event.is_set():
                with self._lock:
                    job = self._jobs[job_id]
                    self._jobs[job_id] = replace(
                        job,
                        status="cancelled",
                        finished_at=utc_now_iso(),
                        message="cancelled",
                    )
                self._events.put(JobEvent(job_id=job_id, kind="cancelled", message="cancelled", progress=1.0))
                return

            with self._lock:
                job = self._jobs[job_id]
                self._jobs[job_id] = replace(
                    job,
                    status="success",
                    finished_at=utc_now_iso(),
                    progress=1.0,
                    message="completed",
                )
            payload = {"result": result} if result is not None else {}
            self._events.put(JobEvent(job_id=job_id, kind="success", message="completed", progress=1.0, payload=payload))
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                self._jobs[job_id] = replace(
                    job,
                    status="error",
                    finished_at=utc_now_iso(),
                    message="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            self._events.put(
                JobEvent(
                    job_id=job_id,
                    kind="error",
                    message=f"{type(exc).__name__}: {exc}",
                    progress=1.0,
                )
            )

    def _update_progress(self, job_id: str, value: float, message: str) -> None:
        pct = max(0.0, min(1.0, float(value)))
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = replace(job, progress=pct, message=message or job.message)
        self._events.put(JobEvent(job_id=job_id, kind="progress", message=message, progress=pct))

    def poll_events(self) -> list[JobEvent]:
        items: list[JobEvent] = []
        while not self._events.empty():
            items.append(self._events.get_nowait())
        return items

    def list_jobs(self) -> list[JobInfo]:
        with self._lock:
            return [replace(job) for job in sorted(self._jobs.values(), key=lambda item: item.created_at)]

    def get_job(self, job_id: str) -> JobInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return replace(job) if job is not None else None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            future = self._futures.get(job_id)
            cancel_event = self._cancel_events.get(job_id)
            if job is None or future is None or cancel_event is None:
                return False
            if job.status in {"success", "error", "cancelled"}:
                return False
            cancelled = future.cancel()
            if cancelled:
                self._jobs[job_id] = replace(
                    job,
                    status="cancelled",
                    cancel_requested=True,
                    finished_at=utc_now_iso(),
                    message="cancelled before start",
                )
                self._events.put(JobEvent(job_id=job_id, kind="cancelled", message="cancelled before start"))
                return True
            cancel_event.set()
            self._jobs[job_id] = replace(job, cancel_requested=True, message="cancellation requested")
            self._events.put(JobEvent(job_id=job_id, kind="cancelling", message="cancellation requested"))
            return True

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in {"queued", "running"})

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

