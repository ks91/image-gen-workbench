import json
import tempfile
import unittest
from unittest.mock import patch
from image_gen_workbench.app import create_app
from image_gen_workbench.engine import DemoEngine


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, DemoEngine())
        self.jobs = self.app.extensions['jobs']
        self.client = self.app.test_client()
        with self.jobs.connect() as db:
            for i in range(55):
                job = dict(id=f'{i:032x}', status='succeeded', phase='phase_succeeded',
                           spec={'prompt': str(i)}, favorite=i % 2 == 0, notes='', error=None)
                db.execute('INSERT INTO jobs VALUES (?, ?)', (job['id'], json.dumps(job)))

    def tearDown(self):
        self.jobs.close()
        self.tmp.cleanup()

    def test_pages_are_bounded_ordered_and_do_not_load_all_jobs(self):
        with patch.object(self.jobs, 'list', side_effect=AssertionError('full history loaded')):
            pages = [self.client.get(f'/api/jobs?page={n}').json for n in (1, 2, 3)]
        self.assertEqual([len(p['items']) for p in pages], [24, 24, 7])
        self.assertEqual([j['spec']['prompt'] for p in pages for j in p['items']],
                         [str(i) for i in reversed(range(55))])
        self.assertEqual(pages[0]['total'], 55)
        self.assertEqual(pages[0]['pages'], 3)
        self.assertEqual(self.client.get('/api/jobs').json, pages[0])

    def test_favorites_are_filtered_before_paging(self):
        first = self.client.get('/api/jobs?favorites=1&page=1').json
        second = self.client.get('/api/jobs?favorites=1&page=2').json
        self.assertEqual(first['total'], 28)
        self.assertEqual(len(first['items']), 24)
        self.assertEqual(len(second['items']), 4)
        self.assertTrue(all(j['favorite'] for p in (first, second) for j in p['items']))
        for j in second['items']:
            self.jobs.update(j['id'], favorite=False)
        adjusted = self.client.get('/api/jobs?favorites=1&page=2').json
        self.assertEqual(adjusted['page'], 1)
        self.assertEqual(adjusted['pages'], 1)

    def test_empty_history_and_invalid_queries(self):
        for query in ('page=0', 'page=-1', 'page=no', 'page=1.5', 'favorites=yes'):
            self.assertEqual(self.client.get('/api/jobs?' + query).status_code, 400, query)
        with self.jobs.connect() as db:
            db.execute('DELETE FROM jobs')
        empty = self.client.get('/api/jobs?page=999').json
        self.assertEqual(empty, dict(items=[], total=0, page=1, pages=1, page_size=24))
