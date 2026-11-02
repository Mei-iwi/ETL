"""Legacy Master text access behind an explicit boundary.

Existing legacy data remains writable until a complete component pipeline is
available; this adapter must not be supplied to a new component-owned job.
"""


class LegacyOCRText:
    def insert_page(self, repo, values):
        return repo.insert('ocr_page_results', values)

    def page_by_task(self, repo, task_id):
        rows = repo.find('ocr_page_results', {'page_task_id': task_id}, limit=1)
        return rows[0] if rows else None

    def set_normalized(self, repo, result_id, text, updated_at):
        repo.update('ocr_page_results', result_id, {'normalized_text': text, 'updated_at': updated_at})

    def set_metadata(self, repo, result_id, metadata):
        repo.update('ocr_page_results', result_id, {'result_metadata': metadata})

    def unit_text(self, repo, unit_id):
        return repo.get('content_unit_texts', unit_id)

    def insert_unit_text(self, repo, unit_id, result_id, text):
        repo.insert('content_unit_texts', {'content_unit_id': unit_id, 'page_result_id': result_id, 'text': text})

    def update_unit_text(self, repo, unit_id, result_id, text):
        repo.update('content_unit_texts', unit_id, {'page_result_id': result_id, 'text': text})

    def delete_unit_text(self, repo, unit_id):
        repo.delete('content_unit_texts', {'content_unit_id': unit_id})
