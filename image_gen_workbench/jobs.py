from .i18n import UserError
import json
import math
import queue
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from PIL import Image


class Cancelled(Exception):
    pass


def validate_spec(data):
    if not isinstance(data, dict):
        raise UserError('error_json')
    spec = {}
    for key, default in [('prompt', ''), ('negative_prompt', '')]:
        value = data.get(key, default)
        if not isinstance(value, str) or len(value) > 12000:
            raise UserError('error_string', field=key)
        spec[key] = value.strip()
    if not spec['prompt']:
        raise UserError('error_prompt')
    for key, default, low, high in [('width', 1024, 256, 2048), ('height', 1024, 256, 2048),
                                   ('steps', 28, 1, 100), ('seed', None, 0, 2**32 - 1)]:
        value = data.get(key, default)
        if key == 'seed' and value is None:
            value = secrets.randbelow(2**32)
        if type(value) is not int or not low <= value <= high:
            raise UserError('error_integer', field=key, low=low, high=high)
        if key in ('width', 'height') and value % 32:
            raise UserError('error_dimensions')
        spec[key] = value
    cfg = data.get('cfg', 3.5)
    if type(cfg) not in (int, float) or not math.isfinite(cfg) or not 1 <= cfg <= 10:
        raise UserError('error_cfg')
    spec['cfg'] = cfg
    source_id = data.get('source_id')
    if source_id is not None and (not isinstance(source_id, str) or len(source_id) != 32 or
                                  any(c not in '0123456789abcdef' for c in source_id)):
        raise UserError('error_source_id')
    spec['source_id'] = source_id
    return spec


class Jobs:
    """One worker owns the model; SQLite is the durable job history."""

    def __init__(self, directory, engine):
        self.root = Path(directory).resolve()
        self.images = self.root / 'images'
        self.images.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'history.sqlite3'
        self.engine = engine
        self.lock = threading.RLock()
        self.events = {}
        self.queue = queue.Queue()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY)')
        for job in self.list():
            if job['status'] in ('queued', 'running'):
                self.update(job['id'], status='failed', phase='phase_failed', error_key='error_interrupted', error='The previous run was interrupted.')
        self.worker = threading.Thread(target=self.run, daemon=True, name='image-worker')
        self.worker.start()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM jobs ORDER BY rowid DESC')]

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row[0])

    def update(self, job_id, **changes):
        with self.lock, self.connect() as db:
            job = self.get(job_id)
            job.update(changes)
            db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job_id))
            return job

    def image_path(self, image_id):
        if not isinstance(image_id, str) or len(image_id) != 32 or any(c not in '0123456789abcdef' for c in image_id):
            raise KeyError(image_id)
        with self.connect() as db:
            upload = db.execute('SELECT id FROM uploads WHERE id=?', (image_id,)).fetchone()
        if not upload and self.get(image_id)['status'] != 'succeeded':
            raise KeyError(image_id)
        path = self.images / f'{image_id}.png'
        if not path.is_file():
            raise KeyError(image_id)
        return path

    def upload(self, image):
        image_id = uuid.uuid4().hex
        image.save(self.images / f'{image_id}.png')
        with self.connect() as db:
            db.execute('INSERT INTO uploads VALUES (?)', (image_id,))
        return image_id

    def submit(self, data):
        spec = validate_spec(data)
        if spec['source_id']:
            try:
                self.image_path(spec['source_id'])
            except KeyError:
                raise UserError('error_source_missing') from None
        with self.lock:
            if sum(not e.is_set() for e in self.events.values()) >= 20:
                raise UserError('error_queue')
            job_id = uuid.uuid4().hex
            job = dict(id=job_id, spec=spec, status='queued', created_at=time.time(),
                       progress=0, phase='phase_queued', error=None, image_url=None,
                       favorite=False, notes='', engine=self.engine.name)
            with self.connect() as db:
                db.execute('INSERT INTO jobs VALUES (?,?)', (job_id, json.dumps(job, ensure_ascii=False)))
            self.events[job_id] = threading.Event()
            self.queue.put(job_id)
        return job

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job['status'] in ('queued', 'running'):
                self.events[job_id].set()
                self.update(job_id, phase='phase_cancelling')
            return self.get(job_id)

    def run(self):
        while (job_id := self.queue.get()) is not None:
            event = self.events[job_id]
            started = time.monotonic()
            try:
                if event.is_set():
                    raise Cancelled()
                job = self.update(job_id, status='running', phase='phase_loading')
                source = None
                if job['spec']['source_id']:
                    with Image.open(self.image_path(job['spec']['source_id'])) as image:
                        source = image.convert('RGB')

                def progress(step, total):
                    if event.is_set():
                        raise Cancelled()
                    self.update(job_id, progress=step / total, phase='phase_generating', phase_params=dict(step=step, total=total))

                result = self.engine.generate(job['spec'], source, progress, event)
                with self.lock:
                    if event.is_set():
                        raise Cancelled()
                    from PIL.PngImagePlugin import PngInfo
                    info = PngInfo()
                    info.add_text('generation', json.dumps(job['spec'], ensure_ascii=False))
                    result.save(self.images / f'{job_id}.png', pnginfo=info)
                    self.update(job_id, status='succeeded', phase='phase_succeeded', progress=1,
                                image_url=f'/images/{job_id}.png', elapsed=round(time.monotonic() - started, 2))
            except Cancelled:
                self.update(job_id, status='cancelled', phase='phase_cancelled')
            except Exception as exc:
                self.update(job_id, status='failed', phase='phase_failed', error=f'{type(exc).__name__}: {exc}',
                            error_key=getattr(exc, 'key', None), error_params=getattr(exc, 'params', {}))
            finally:
                with self.lock:
                    self.events.pop(job_id, None)
                self.queue.task_done()

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        if self.worker.is_alive():
            self.queue.put(None)
            self.worker.join(timeout=5)
