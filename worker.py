import argparse
import logging
import os
import time

from src.logging_config import configure_logging
from src.persistence import claim_next_queued_job, record_audit_event, update_persisted_job
from src.dataset_manager import now_iso
from src.worker_tasks import process_dataset_for_org


SUPPORTED_KINDS = {"dataset_processing"}
WORKER_LOG_PATH = configure_logging("vox-worker")
logger = logging.getLogger(__name__)


def run_job(job: dict) -> None:
    job_id = job["job_id"]
    metadata = job.get("metadata") or {}
    org_id = metadata.get("org_id") or job.get("org_id")
    if not org_id:
        raise RuntimeError("Queued job is missing org_id metadata")
    logger.info("Starting job %s kind=%s org_id=%s", job_id, job["kind"], org_id)

    def progress(progress_value: int, message: str) -> None:
        update_persisted_job(job_id, progress=progress_value, message=message)

    if job["kind"] == "dataset_processing":
        result = process_dataset_for_org(org_id, progress_callback=progress)
    else:
        raise RuntimeError(f"Unsupported job kind: {job['kind']}")

    update_persisted_job(
        job_id,
        status="completed",
        progress=100,
        message="Completed",
        result=result,
        error=None,
        finished_at=now_iso(),
    )
    record_audit_event(
        event_type="worker_job_completed",
        org_id=org_id,
        details={"job_id": job_id, "kind": job["kind"]},
    )
    logger.info("Completed job %s kind=%s org_id=%s", job_id, job["kind"], org_id)


def run_once(worker_id: str) -> bool:
    job = claim_next_queued_job(worker_id, kinds=SUPPORTED_KINDS)
    if job is None:
        return False

    try:
        run_job(job)
    except Exception as exc:
        metadata = job.get("metadata") or {}
        update_persisted_job(
            job["job_id"],
            status="failed",
            progress=100,
            message="Failed",
            error=str(exc),
            finished_at=now_iso(),
        )
        record_audit_event(
            event_type="worker_job_failed",
            org_id=metadata.get("org_id"),
            details={"job_id": job["job_id"], "kind": job["kind"], "error": str(exc)},
        )
        logger.error("Worker job failed: %s", job["job_id"], exc_info=True)
        raise
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the VOX background worker.")
    parser.add_argument("--once", action="store_true", help="Process one queued job and exit.")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Delay between queue checks.")
    parser.add_argument("--worker-id", default=os.environ.get("VOX_WORKER_ID", "vox-worker-1"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"VOX worker started: {args.worker_id}")
    logger.info("VOX worker started worker_id=%s log_file=%s", args.worker_id, WORKER_LOG_PATH)
    while True:
        processed = run_once(args.worker_id)
        if args.once:
            print("Processed one job." if processed else "No queued jobs.")
            return
        if not processed:
            time.sleep(max(0.2, args.poll_seconds))


if __name__ == "__main__":
    main()
