from threading import Event, Thread
from time import monotonic
from typing import Any, Callable

from data_sync_etl.logging import event, flow


class OCRWorker:
    def __init__(
        self,
        pipeline: Any,
        on_job_terminal: Callable[[str], object] | None = None,
        reconcile: Callable[[], bool] | None = None,
        recovery_interval_seconds: int = 30,
    ) -> None:
        self.pipeline = pipeline
        self.on_job_terminal = on_job_terminal
        self.reconcile = reconcile
        self.recovery_interval_seconds = recovery_interval_seconds
        self._next_recovery_at = 0.0

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
            task = self.pipeline.claim(worker_id)

            if task is None:
                return self.reconcile() if self.reconcile is not None else False

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

            try:
                result, image_path = self.pipeline.recognize(task)

                self.pipeline.succeed(
                    task,
                    result,
                    image_path,
                )

            except Exception:
                self.pipeline.fail(task)

            finally:
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

            return True

    def run(self, worker_id: str, once: bool = False) -> bool | None:
        if once:
            return self.once(worker_id)

        stop = Event()

        try:
            while True:
                if not self.once(worker_id):
                    stop.wait(1)
        except KeyboardInterrupt:
            return None
