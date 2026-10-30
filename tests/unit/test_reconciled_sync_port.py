from data_sync_etl.application.reconciled_sync import ReconciledSync


def test_application_reconciled_sync_delegates_scope_and_mode_to_port():
    class Runner:
        def __init__(self):
            self.call = None

        def run(self, stream, full=False):
            self.call = (stream, full)
            return {'status': 'PENDING'}

    runner = Runner()
    service = ReconciledSync(runner)

    assert service.run('subjects', full=True) == {'status': 'PENDING'}
    assert runner.call == ('subjects', True)
