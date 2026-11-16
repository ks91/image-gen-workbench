import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from image_gen_workbench.app import create_app
from image_gen_workbench.engine import DemoEngine


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, DemoEngine())
        self.client = self.app.test_client()
        self.jobs = self.app.extensions['jobs']

    def tearDown(self):
        self.jobs.close()
        self.tmp.cleanup()

    def generate(self, **changes):
        spec = dict(prompt='x', width=256, height=256, steps=1)
        spec.update(changes)
        job = self.jobs.submit(spec)
        self.jobs.queue.join()
        return self.jobs.get(job['id'])

    def test_delete_removes_png_history_and_frees_record_capacity(self):
        job = self.generate()
        self.jobs.max_records = 1
        self.jobs.max_storage_bytes = 1  # Cleanup must remain possible above the storage limit.
        response = self.client.delete('/api/jobs/' + job['id'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.jobs.images / (job['id'] + '.png')).exists())
        self.assertEqual(self.client.get('/api/jobs/' + job['id']).status_code, 404)
        self.assertEqual(self.client.get(job['image_url']).status_code, 404)
        self.assertEqual(self.jobs.page()['total'], 0)
        self.jobs.max_storage_bytes = 10 * 1024**3
        self.assertEqual(self.generate()['status'], 'succeeded')

    def test_referenced_parent_is_protected_until_child_deleted(self):
        parent = self.generate()
        child = self.generate(source_id=parent['id'])
        response = self.client.delete('/api/jobs/' + parent['id'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error_key'], 'error_delete_referenced')
        self.assertTrue((self.jobs.images / (parent['id'] + '.png')).exists())
        self.assertEqual(self.client.delete('/api/jobs/' + child['id']).status_code, 200)
        self.assertEqual(self.client.delete('/api/jobs/' + parent['id']).status_code, 200)

    def test_unused_upload_cleanup_keeps_references_and_current_selection(self):
        used, selected, unused = [self.jobs.upload(Image.new('RGB', (16, 16))) for _ in range(3)]
        self.generate(source_id=used)
        response = self.client.delete('/api/uploads/unused?keep=' + selected)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['deleted'], 1)
        self.assertFalse((self.jobs.images / (unused + '.png')).exists())
        self.assertTrue((self.jobs.images / (used + '.png')).exists())
        self.assertTrue((self.jobs.images / (selected + '.png')).exists())

    def test_failed_jobs_and_missing_files_can_be_removed(self):
        job = self.generate()
        (self.jobs.images / (job['id'] + '.png')).unlink()
        self.jobs.update(job['id'], status='failed', image_url=None)
        self.assertEqual(self.client.delete('/api/jobs/' + job['id']).status_code, 200)
        self.assertEqual(self.client.delete('/api/jobs/' + job['id']).status_code, 404)

    def test_active_job_and_queued_source_are_protected(self):
        parent = self.generate()
        started, release = threading.Event(), threading.Event()
        original = self.jobs.engine.generate
        def blocked(*args):
            started.set(); release.wait(5)
            return original(*args)
        self.jobs.engine.generate = blocked
        try:
            job = self.jobs.submit(dict(prompt='x', steps=1, width=256, height=256, source_id=parent['id']))
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.delete('/api/jobs/' + job['id']).status_code, 409)
            self.assertEqual(self.client.delete('/api/jobs/' + parent['id']).status_code, 409)
        finally:
            release.set()

    def test_file_error_keeps_history_and_image(self):
        job = self.generate()
        with patch('image_gen_workbench.jobs.os.replace', side_effect=OSError('cannot rename')):
            self.assertEqual(self.client.delete('/api/jobs/' + job['id']).status_code, 409)
        self.assertEqual(self.jobs.get(job['id'])['status'], 'succeeded')
        self.assertTrue((self.jobs.images / (job['id'] + '.png')).exists())

    def test_foreign_origin_cannot_delete(self):
        job = self.generate()
        self.assertEqual(self.client.delete('/api/jobs/' + job['id'], headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.jobs.get(job['id'])['status'], 'succeeded')

    def test_database_failure_restores_staged_image(self):
        job = self.generate()
        with self.jobs.lock, patch.object(self.jobs, 'connect') as connect:
            connect.return_value.__enter__.return_value.execute.side_effect = sqlite3.OperationalError('db full')
            with self.assertRaisesRegex(ValueError, 'Deletion failed'):
                self.jobs._delete_records([job['id']], 'jobs')
        self.assertEqual(self.jobs.get(job['id'])['status'], 'succeeded')
        self.assertTrue((self.jobs.images / (job['id'] + '.png')).exists())
        self.assertEqual(list(self.jobs.images.glob('*.deleting')), [])

    def test_restart_restores_uncommitted_deletion_and_finishes_committed_one(self):
        kept = self.generate()
        deleted = self.generate()
        self.jobs.close()
        for job in (kept, deleted):
            original = self.jobs.images / (job['id'] + '.png')
            original.rename(str(original) + '.deleting')
        with self.jobs.connect() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (deleted['id'],))
        reopened = create_app(self.tmp.name, DemoEngine())
        try:
            restored = reopened.extensions['jobs']
            self.assertTrue((restored.images / (kept['id'] + '.png')).exists())
            self.assertFalse((restored.images / (deleted['id'] + '.png')).exists())
            self.assertEqual(list(restored.images.glob('*.deleting')), [])
        finally:
            reopened.extensions['jobs'].close()
