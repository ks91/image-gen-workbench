import io
import tempfile
import threading
import time
import unittest

from PIL import Image
from image_gen_workbench.app import create_app


class RecordingEngine:
    name = 'test'

    def __init__(self):
        self.calls = []

    def generate(self, spec, source, progress, cancelled):
        self.calls.append((spec, source))
        progress(1, spec['steps'])
        if spec['prompt'] == 'fail':
            raise RuntimeError('test failure')
        return Image.new('RGB', (spec['width'], spec['height']), 'navy')


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = RecordingEngine()
        self.app = create_app(self.tmp.name, engine=self.engine)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions['jobs'].close()
        self.tmp.cleanup()

    def submit(self, **changes):
        spec = dict(prompt='A quiet garden', width=256, height=256, steps=2, seed=42)
        spec.update(changes)
        return self.client.post('/api/jobs', json=spec)

    def finish(self, job_id):
        for _ in range(200):
            job = self.client.get('/api/jobs/' + job_id).json
            if job['status'] in ('succeeded', 'failed', 'cancelled'):
                return job
            time.sleep(.01)
        self.fail('job did not finish')

    def upload(self):
        data = io.BytesIO()
        Image.new('RGB', (32, 32), 'red').save(data, 'PNG')
        data.seek(0)
        response = self.client.post('/api/uploads', data={'image': (data, 'sample.png')})
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def test_home_and_generation_persist_image_and_parameters(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        job = self.finish(response.json['id'])
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['spec']['seed'], 42)
        image = self.client.get(job['image_url'])
        self.assertEqual(image.mimetype, 'image/png')
        with Image.open(io.BytesIO(image.data)) as result:
            self.assertEqual(result.size, (256, 256))
        image.close()
        self.assertEqual(self.client.get('/api/jobs').json[0]['id'], job['id'])
        self.app.extensions['jobs'].close()
        reopened = create_app(self.tmp.name, engine=RecordingEngine())
        try:
            self.assertEqual(reopened.test_client().get('/api/jobs').json[0]['spec']['prompt'], 'A quiet garden')
        finally:
            reopened.extensions['jobs'].close()

    def test_edit_uploaded_image_then_iterate_on_output(self):
        source_id = self.upload()
        first = self.finish(self.submit(source_id=source_id).json['id'])
        second = self.finish(self.submit(source_id=first['id'], prompt='Make it snowy').json['id'])
        self.assertEqual(second['status'], 'succeeded')
        self.assertEqual(second['spec']['source_id'], first['id'])
        self.assertEqual(self.engine.calls[0][1].size, (32, 32))
        self.assertEqual(self.engine.calls[1][1].size, (256, 256))

    def test_batch_seeds_and_selecting_an_output(self):
        source = self.upload()
        response = self.submit(num_images=3, seed=2**32-1, source_id=source)
        self.assertEqual(response.status_code, 202)
        ids = response.json['job_ids']
        self.assertEqual(len(set(ids)), 3)
        results = [self.finish(i) for i in ids]
        self.assertEqual([j['spec']['seed'] for j in results], [2**32-1, 0, 1])
        self.assertEqual(len({j['batch_id'] for j in results}), 1)
        self.assertEqual([j['batch_index'] for j in results], [1, 2, 3])
        for j in results:
            self.assertEqual(j['status'], 'succeeded')
            self.assertEqual(j['batch_size'], 3)
            self.assertEqual(j['spec']['source_id'], source)
            image = self.client.get(j['image_url'])
            with Image.open(io.BytesIO(image.data)) as png:
                import json
                self.assertEqual(json.loads(png.info['generation'])['seed'], j['spec']['seed'])
            image.close()
        chosen = self.finish(self.submit(source_id=ids[1]).json['id'])
        self.assertEqual(chosen['spec']['source_id'], ids[1])
        self.assertEqual(self.engine.calls[-1][1].size, (256, 256))

    def test_invalid_batch_size_creates_no_jobs(self):
        for count in (0, 9, -1, True, 1.5, '2', None):
            with self.subTest(count=count):
                self.assertEqual(self.submit(num_images=count).status_code, 400)
        self.assertEqual(self.client.get('/api/jobs').json, [])

    def test_batch_queue_limit_is_atomic(self):
        started, release = threading.Event(), threading.Event()
        original = self.engine.generate
        def blocking(*args):
            started.set()
            release.wait(5)
            return original(*args)
        self.engine.generate = blocking
        try:
            self.submit(num_images=8)
            self.assertTrue(started.wait(2))
            self.submit(num_images=8)
            rejected = self.submit(num_images=8)
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json['error_key'], 'error_queue')
            self.assertEqual(len(self.client.get('/api/jobs').json), 16)
        finally:
            release.set()

    def test_invalid_parameters_never_reach_engine(self):
        for changes in ({'prompt': ''}, {'width': 257}, {'steps': 0}, {'seed': -1},
                        {'source_id': '../../secret'}, {'cfg': float('nan')},
                        {'steps': True}, {'prompt': ['bad']}, {'width': 99999}):
            with self.subTest(changes=changes):
                self.assertEqual(self.submit(**changes).status_code, 400)
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.client.post('/api/jobs', json=[]).status_code, 400)

    def test_failure_is_visible_and_worker_recovers(self):
        bad = self.finish(self.submit(prompt='fail').json['id'])
        self.assertEqual(bad['status'], 'failed')
        self.assertIn('test failure', bad['error'])
        self.assertEqual(self.finish(self.submit().json['id'])['status'], 'succeeded')

    def test_favorite_notes_and_missing_files(self):
        job = self.finish(self.submit().json['id'])
        response = self.client.patch('/api/jobs/' + job['id'], json={'favorite': True, 'notes': 'Better light'})
        self.assertEqual(response.status_code, 200)
        saved = self.client.get('/api/jobs/' + job['id']).json
        self.assertTrue(saved['favorite'])
        self.assertEqual(saved['notes'], 'Better light')
        self.assertEqual(self.client.get('/api/jobs/missing').status_code, 404)
        self.assertEqual(self.client.get('/images/secret.png').status_code, 404)

    def test_rejects_invalid_upload_and_cross_origin_requests(self):
        self.assertEqual(self.client.post('/api/uploads', data={'image': (io.BytesIO(b'bad'), 'x.png')}).status_code, 400)
        self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'x'}, headers={'Origin': 'https://other.example'}).status_code, 403)

    def test_interrupted_jobs_are_recovered_on_restart(self):
        job = self.finish(self.submit().json['id'])
        self.app.extensions['jobs'].update(job['id'], status='running')
        self.app.extensions['jobs'].close()
        restarted = create_app(self.tmp.name, engine=RecordingEngine())
        try:
            restored = restarted.test_client().get('/api/jobs/' + job['id']).json
            self.assertEqual(restored['status'], 'failed')
            self.assertIn('interrupted', restored['error'])
        finally:
            restarted.extensions['jobs'].close()

    def test_cancel_running_and_queued_jobs(self):
        started = threading.Event()
        release = threading.Event()
        original = self.engine.generate

        def blocking(spec, source, progress, cancelled):
            started.set()
            release.wait(3)
            return original(spec, source, progress, cancelled)

        self.engine.generate = blocking
        first = self.submit().json['id']
        self.assertTrue(started.wait(2))
        second = self.submit().json['id']
        for job_id in (first, second):
            self.assertEqual(self.client.post(f'/api/jobs/{job_id}/cancel').status_code, 200)
        release.set()
        self.assertEqual(self.finish(first)['status'], 'cancelled')
        self.assertEqual(self.finish(second)['status'], 'cancelled')
        self.assertEqual(len(self.engine.calls), 1)


if __name__ == '__main__':
    unittest.main()
