from data_sync_etl.ports.reconciled_sync import ReconciledSyncPort


class ReconciledSync:
    """Application entry point for bounded reconciliation repairs."""

    def __init__(self, runner: ReconciledSyncPort):
        self.runner = runner

    def run(self, stream: str, full: bool = False):
        return self.runner.run(stream, full=full)
