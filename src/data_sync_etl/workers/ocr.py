from threading import Event, Thread

from data_sync_etl.logging import event, flow


class OCRWorker:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def once(self, worker_id):
        with flow():
            task = self.pipeline.claim(worker_id)
            if task is None:
                return False
            stop = Event()

            def heartbeat():
                while not stop.wait(self.pipeline.heartbeat_timeout / 3):
                    try:
                        if not self.pipeline.heartbeat(task):
                            return
                    except Exception:
                        event(task_id=task["id"], action="HEARTBEAT_FAILED", stage="ocr")
                        return

            thread = Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                result, image_path = self.pipeline.recognize(task)
                self.pipeline.succeed(task, result, image_path)
            except Exception:
                self.pipeline.fail(task)
            finally:
                stop.set()
                thread.join(timeout=5)
            return True

    def run(self, worker_id, once=False):
        if once:
            return self.once(worker_id)
        stop = Event()
        try:
            while True:
                if not self.once(worker_id):
                    stop.wait(1)
        except KeyboardInterrupt:
            return None
