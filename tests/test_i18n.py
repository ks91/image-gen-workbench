import io
import json
import tempfile
import unittest
from image_gen_workbench.app import create_app
from image_gen_workbench.engine import DemoEngine


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, DemoEngine())
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions['jobs'].close()
        self.tmp.cleanup()

    def test_default_english_and_browser_japanese(self):
        en = self.client.get('/').get_data(as_text=True)
        ja = self.client.get('/', headers={'Accept-Language': 'ja-JP, en;q=0.5'}).get_data(as_text=True)
        self.assertIn('<html lang="en">', en)
        self.assertIn('Generate image', en)
        self.assertIn('<html lang="ja">', ja)
        self.assertIn('画像を生成する', ja)
        self.assertIn('id="language"', en)

    def test_saved_preference_overrides_browser_and_invalid_cookie_falls_back(self):
        self.client.set_cookie('workbench_language', 'en')
        self.assertIn('<html lang="en">', self.client.get('/', headers={'Accept-Language': 'ja'}).text)
        self.client.set_cookie('workbench_language', 'ja')
        self.assertIn('<html lang="ja">', self.client.get('/').text)
        self.client.set_cookie('workbench_language', '<script>')
        self.assertIn('<html lang="en">', self.client.get('/').text)

    def test_validation_and_upload_errors_in_both_languages(self):
        for language, prompt_message, upload_message in [('en', 'Enter a prompt.', 'Choose a readable image file.'),
                                                          ('ja', 'プロンプトを入力してください。', '読み込める画像ファイルを指定してください。')]:
            self.client.set_cookie('workbench_language', language)
            response = self.client.post('/api/jobs', json={'prompt': ''})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json['error'], prompt_message)
            self.assertEqual(response.json['error_key'], 'error_prompt')
            response = self.client.post('/api/uploads', data={'image': (io.BytesIO(b'bad'), 'x.png')})
            self.assertEqual(response.json['error'], upload_message)

    def test_job_translation_is_per_request_and_preserves_user_content(self):
        jobs = self.app.extensions['jobs']
        job = jobs.submit({'prompt': '赤いマグカップ', 'steps': 1, 'seed': 42})
        jobs.queue.join()
        jobs.update(job['id'], notes='Keep 日本語', status='failed', phase='失敗', error='前回の実行が中断されました。設定を再利用して再実行できます。')
        # An existing Japanese history entry should also translate without rewriting the DB.
        en = self.client.get('/api/jobs/' + job['id']).json
        self.assertEqual(en['phase'], 'Failed')
        self.assertIn('interrupted', en['error'])
        self.client.set_cookie('workbench_language', 'ja')
        ja = self.client.get('/api/jobs').json[0]
        self.assertEqual(ja['phase'], '失敗')
        self.assertIn('中断', ja['error'])
        self.assertEqual(en['spec'], ja['spec'])
        self.assertEqual(en['notes'], 'Keep 日本語')
        self.assertEqual(jobs.get(job['id'])['phase'], '失敗')

    def test_http_errors_are_localized(self):
        self.client.set_cookie('workbench_language', 'ja')
        self.assertEqual(self.client.get('/missing').json['error'], '見つかりません。')
        self.assertEqual(self.client.post('/api/jobs', data='{', content_type='application/json').status_code, 400)
        self.client.set_cookie('workbench_language', 'en')
        self.assertEqual(self.client.get('/api/jobs/missing').json['error'], 'Not found.')

    def test_catalogs_have_the_same_keys_and_placeholders(self):
        import re
        from image_gen_workbench.i18n import MESSAGES
        self.assertEqual(MESSAGES['en'].keys(), MESSAGES['ja'].keys())
        for key in MESSAGES['en']:
            self.assertEqual(set(re.findall(r'\{\w+\}', MESSAGES['en'][key])),
                             set(re.findall(r'\{\w+\}', MESSAGES['ja'][key])), key)
