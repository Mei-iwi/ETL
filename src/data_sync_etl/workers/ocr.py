from threading import Event, Thread
from time import monotonic
from typing import Any, Callable

from data_sync_etl.logging import event, flow
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


class OCRWorker:
    def __init__(
        self,
        pipeline: Any,
        on_job_terminal: Callable[[str], object] | None = None,
        reconcile: Callable[[], bool] | None = None,
        recovery_interval_seconds: int = 30,
        component_projection_worker: Any | None = None,
        component_page_executor: Any | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.on_job_terminal = on_job_terminal
        self.reconcile = reconcile
        self.recovery_interval_seconds = recovery_interval_seconds
        self.component_projection_worker = component_projection_worker
        component_owned = getattr(pipeline, 'component_owned', False) is True
        if component_owned != (component_page_executor is not None):
            raise ValueError('COMPONENT_OCR_WORKER_NOT_CONFIGURED')
        self.component_page_executor = component_page_executor
        self._next_recovery_at = 0.0
        self._tasks_since_reconcile = 0
        self._prefer_component_projection = False

    def _background_once(self) -> bool:
        self._prefer_component_projection = not self._prefer_component_projection

        def project() -> bool:
            if self.component_projection_worker is None:
                return False
            try:
                return bool(self.component_projection_worker.run_once())
            except Exception:
                # Errors may contain source paths or credentials; report only a code.
                event(stage='component_projection', action='PROJECTION_UNAVAILABLE')
                return False

        def reconcile() -> bool:
            return bool(self.reconcile()) if self.reconcile is not None else False

        if self._prefer_component_projection:
            return project() or reconcile()
        return reconcile() or project()

    def once(self, worker_id: str) -> bool:
        with flow():
            current = monotonic()
            if current >= self._next_recovery_at:
                self._next_recovery_at = current + self.recovery_interval_seconds
                recovered = self.pipeline.recover_stale()["recovered"]
                event(
                    stage="ocr",
                    action="STALE_RECOVERY",
                    detected=recovered,
                    recovered=recovered,
                )
            # Bounded fair turn for existing finalization/indexing work, even under
            # a continuous OCR backlog. No new queue or scheduler is introduced.
            if self._tasks_since_reconcile >= 5 and (self.reconcile is not None
                                                      or self.component_projection_worker is not None):
                self._tasks_since_reconcile = 0
                if self._background_once():
                    return True
            schedule_source = getattr(self.pipeline, 'schedule_authorized_source_job', None)
            if schedule_source is not None and schedule_source():
                return True
            task = self.pipeline.claim(worker_id)

            if task is None:
                return self._background_once()

            stop = Event()

            def heartbeat():
                while not stop.wait(self.pipeline.heartbeat_timeout / 3):
                    try:
                        if not self.pipeline.heartbeat(task):
                            return
                    except Exception:
                        event(
                            task_id=task["id"],
                            action="HEARTBEAT_FAILED",
                            stage="ocr",
                        )
                        return

            thread = Thread(
                target=heartbeat,
                daemon=True,
            )
            thread.start()

            image_path = None
            try:
                if self.component_page_executor is not None:
                    self.component_page_executor.execute_claimed(task)
                else:
                    result, image_path = self.pipeline.recognize(task)
                    self.pipeline.succeed(task, result, image_path)

            except Exception as exc:
                self.pipeline.fail(task, error=exc)

            finally:
                if image_path and hasattr(self.pipeline.storage, 'cloud'):
                    try:
                        from pathlib import Path
                        Path(image_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                stop.set()
                thread.join(timeout=5)

            # Kích hoạt điều phối sau khi OCR đã
            # cập nhật kết quả và trạng thái task.
            if self.on_job_terminal is not None:
                try:
                    self.on_job_terminal(task["job_id"])
                except Exception:
                    # Không làm mất kết quả OCR đã lưu.
                    # Chỉ log ngữ cảnh an toàn; exception có thể chứa văn bản hoặc URI.
                    event(stage="finalization", job_id=task["job_id"], action="CALLBACK_FAILED")

            self._tasks_since_reconcile += 1
            return True

    def run(self, worker_id: str, once: bool = False) -> bool | None:
        if once:
            return self.once(worker_id)

        with worker_lifecycle() as stop:
            try:
                while not stop.is_set():
                    tick()
                    if not self.once(worker_id):
                        stop.wait(1)
            except KeyboardInterrupt:
                return None
