"""Shared message catalog; persisted jobs are localized only when presented."""
import json
import re
from pathlib import Path

from flask import request

MESSAGES = json.loads((Path(__file__).parent / 'static' / 'messages.json').read_text(encoding='utf-8'))


def language():
    saved = request.cookies.get('workbench_language')
    if saved in MESSAGES:
        return saved
    preferred = request.accept_languages.best_match(['en', 'ja', 'en-US', 'ja-JP'], default='en')
    return preferred.split('-')[0]


def translate(key, lang='en', **params):
    return MESSAGES.get(lang, MESSAGES['en']).get(key, key).format(**params)


class UserError(ValueError):
    def __init__(self, key, **params):
        self.key, self.params = key, params
        super().__init__(translate(key, **params))


class GenerationError(RuntimeError):
    def __init__(self, key):
        self.key, self.params = key, {}
        super().__init__(translate(key))


def localize_job(original, lang):
    job = dict(original)
    phase = job.get('phase', '')
    phase_key = phase if phase.startswith('phase_') else None
    params = job.get('phase_params', {})
    # Read histories written before localization without altering them.
    if not phase_key:
        phase_key = next((k for k, v in MESSAGES['ja'].items() if k.startswith('phase_') and v == phase), None)
        match = re.fullmatch(r'生成中 (\d+)/(\d+)', phase)
        if match:
            phase_key, params = 'phase_generating', dict(step=int(match[1]), total=int(match[2]))
    phase_key = phase_key or ('phase_loading' if job['status'] == 'running' else 'phase_' + job['status'])
    job.update(phase_key=phase_key, phase_params=params, phase=translate(phase_key, lang, **params))
    error_key = job.get('error_key')
    if not error_key and job.get('error'):
        message = job['error'].removeprefix('RuntimeError: ')
        error_key = next((k for k, v in MESSAGES['ja'].items() if k.startswith('error_') and v == message), None)
    if error_key:
        job['error_key'] = error_key
        job['error'] = translate(error_key, lang, **job.get('error_params', {}))
    return job
