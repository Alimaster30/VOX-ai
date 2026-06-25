import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from src.dataset_manager import now_iso


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    result: Dict[str, Any] | None = None
    error: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }


_persist_callback: Callable[[Dict[str, Any]], None] | None = None


def set_job_persistence(callback: Callable[[Dict[str, Any]], None] | None) -> None:
    global _persist_callback
    _persist_callback = callback


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(
        self,
        kind: str,
        target: Callable[[Job], Dict[str, Any] | None],
        metadata: Dict[str, Any] | None = None,
    ) -> Job:
        job = Job(job_id=uuid.uuid4().hex, kind=kind, metadata=metadata or {})
        with self._lock:
            self._jobs[job.job_id] = job
        self._persist(job)

        thread = threading.Thread(target=self._run, args=(job.job_id, target), name=f"vox-job-{kind}", daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest(self, kind: str | None = None, org_id: str | None = None) -> Job | None:
        with self._lock:
            jobs = list(self._jobs.values())
        if kind:
            jobs = [job for job in jobs if job.kind == kind]
        if org_id:
            jobs = [job for job in jobs if job.metadata.get("org_id") == org_id]
        return jobs[-1] if jobs else None

    def update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in updates.items():
                setattr(job, key, value)
            self._persist(job)

    def _persist(self, job: Job) -> None:
        if _persist_callback is None:
            return
        try:
            _persist_callback(job.to_dict())
        except Exception:
            pass

    def _run(self, job_id: str, target: Callable[[Job], Dict[str, Any] | None]) -> None:
        self.update(job_id, status="running", progress=5, message="Started", started_at=now_iso())
        started = time.time()
        try:
            job = self.get(job_id)
            if job is None:
                return
            result = target(job) or {}
            elapsed = round(time.time() - started, 2)
            self.update(
                job_id,
                status="completed",
                progress=100,
                message=f"Completed in {elapsed}s",
                result=result,
                finished_at=now_iso(),
            )
        except Exception as exc:
            self.update(
                job_id,
                status="failed",
                progress=100,
                message="Failed",
                error=str(exc),
                finished_at=now_iso(),
            )


job_manager = JobManager()
