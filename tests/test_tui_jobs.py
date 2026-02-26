import time

from codeclaw.tui.jobs import JobManager


def test_job_manager_submit_and_complete():
    manager = JobManager(max_workers=1)

    def _task(ctx):
        ctx.progress(0.25, "starting")
        time.sleep(0.01)
        ctx.progress(1.0, "done")
        return {"ok": True}

    job = manager.submit("demo", _task)
    deadline = time.time() + 3
    while time.time() < deadline:
        info = manager.get_job(job.id)
        if info and info.status in {"success", "error", "cancelled"}:
            break
        time.sleep(0.02)

    info = manager.get_job(job.id)
    assert info is not None
    assert info.status == "success"
    events = manager.poll_events()
    assert any(event.kind == "started" for event in events)
    assert any(event.kind == "progress" for event in events)
    assert any(event.kind == "success" for event in events)
    manager.shutdown()


def test_job_manager_cancel_best_effort():
    manager = JobManager(max_workers=1)

    def _task(ctx):
        for _ in range(10):
            if ctx.cancelled:
                return "cancelled"
            time.sleep(0.01)
        return "done"

    job = manager.submit("cancel-me", _task)
    assert manager.cancel(job.id) is True

    deadline = time.time() + 3
    final = None
    while time.time() < deadline:
        info = manager.get_job(job.id)
        if info and info.status in {"success", "error", "cancelled"}:
            final = info
            break
        time.sleep(0.02)
    assert final is not None
    assert final.status in {"cancelled", "success"}
    manager.shutdown()

