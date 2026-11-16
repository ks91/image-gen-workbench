import io
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from image_gen_workbench.app import create_app
from image_gen_workbench.engine import DemoEngine


def png():
    data = io.BytesIO()
    Image.new('RGB', (16, 16)).save(data, 'PNG')
    data.seek(0)
    return {'image': (data, 'test.png')}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, DemoEngine())
        self.client = self.app.test_client()
        self.jobs = self.app.extensions['jobs']

    def tearDown(self):
        self.jobs.close()
        self.tmp.cleanup()

    def test_cancelled_jobs_count_until_drained(self):
        started, release = threading.Event(), threading.Event()
        original = self.jobs.engine.generate
        def blocked(*args):
            started.set()
            release.wait(5)
            return original(*args)
        self.jobs.engine.generate = blocked
        spec = dict(prompt='x', width=256, height=256, steps=1)
        try:
            self.jobs.submit(spec)
            self.assertTrue(started.wait(2))
            for _ in range(19):
                job = self.jobs.submit(spec)
                self.jobs.cancel(job['id'])
            with self.assertRaisesRegex(ValueError, 'Too many pending jobs'):
                self.jobs.submit(spec)
            self.assertEqual(len(self.jobs.events), 20)
        finally:
            release.set()
        self.jobs.queue.join()
        self.assertEqual(self.jobs.submit(spec)['status'], 'queued')

    def test_security_headers_on_success_and_error(self):
        for url in ('/', '/api/jobs', '/missing'):
            response = self.client.get(url)
            self.assertIn("frame-ancestors 'none'", response.headers.get('Content-Security-Policy', ''))
            self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')
            self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
        self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'x'},
                         headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)

    def test_rate_limit_is_shared_and_recovers(self):
        self.app.config['WRITE_REQUESTS_PER_MINUTE'] = 2
        with patch('image_gen_workbench.app.time.monotonic', return_value=10):
            self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 201)
            self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 201)
            response = self.app.test_client().post('/api/uploads', data=png())
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['Retry-After'], '60')
        with patch('image_gen_workbench.app.time.monotonic', return_value=71):
            self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 201)

    def test_storage_and_free_space_refuse_without_leaving_files(self):
        self.jobs.max_storage_bytes = 1
        response = self.client.post('/api/uploads', data=png())
        self.assertEqual(response.status_code, 507)
        self.assertEqual(list(self.jobs.images.iterdir()), [])
        self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'x'}).status_code, 507)
        self.assertEqual(self.jobs.list(), [])
        self.jobs.max_storage_bytes = 10 * 1024**3
        with patch('image_gen_workbench.jobs.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 507)
        self.assertEqual(list(self.jobs.images.iterdir()), [])

    def test_upload_concurrency_slot_released_after_bad_upload(self):
        slots = self.app.extensions['upload_slots']
        self.assertTrue(slots.acquire(False))
        self.assertTrue(slots.acquire(False))
        try:
            self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 429)
        finally:
            slots.release(); slots.release()
        self.assertEqual(self.client.post('/api/uploads', data={'image': (io.BytesIO(b'bad'), 'x')}).status_code, 400)
        self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 201)
        self.assertTrue(slots.acquire(False)); self.assertTrue(slots.acquire(False))
        slots.release(); slots.release()

    def test_record_limit_preserves_existing_images_and_history(self):
        self.jobs.max_records = 1
        first = self.client.post('/api/uploads', data=png())
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.client.post('/api/uploads', data=png()).status_code, 507)
        self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'x'}).status_code, 507)
        response = self.client.get(first.json['image_url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        response.close()
        self.assertEqual(len(list(self.jobs.images.iterdir())), 1)

    def test_output_rechecks_capacity_and_releases_reservation_on_failure(self):
        started, release = threading.Event(), threading.Event()
        original = self.jobs.engine.generate
        def blocked(*args):
            started.set(); release.wait(5)
            return original(*args)
        self.jobs.engine.generate = blocked
        try:
            job = self.jobs.submit(dict(prompt='x', width=256, height=256, steps=1))
            self.assertTrue(started.wait(2))
            self.assertIn(job['id'], self.jobs.reservations)
            self.jobs.max_storage_bytes = 1
        finally:
            release.set()
        self.jobs.queue.join()
        failed = self.jobs.get(job['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['error_key'], 'error_storage_limit')
        self.assertEqual(list(self.jobs.images.iterdir()), [])
        self.assertEqual(self.jobs.reservations, {})
        self.jobs.max_storage_bytes = 10 * 1024**3
        next_job = self.jobs.submit(dict(prompt='x', width=256, height=256, steps=1))
        self.jobs.queue.join()
        self.assertEqual(self.jobs.get(next_job['id'])['status'], 'succeeded')
        self.assertEqual(self.jobs.reservations, {})

    def test_failed_atomic_upload_leaves_no_partial_file(self):
        with patch('image_gen_workbench.jobs.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.jobs.upload(Image.new('RGB', (16, 16)))
        self.assertEqual(list(self.jobs.images.iterdir()), [])
        with self.jobs.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0], 0)
