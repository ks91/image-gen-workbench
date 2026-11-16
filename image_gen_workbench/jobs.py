from .i18n import UserError
import json
import io
import os
import shutil
import tempfile
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


class StorageError(UserError):
    status_code = 507


class DeleteError(UserError):
    status_code = 409


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
                                   ('steps', 12, 1, 100), ('seed', None, 0, 2**32 - 1)]:
        value = data.get(key, default)
        if key == 'seed' and value is None:
            value = secrets.randbelow(2**32)
        if type(value) is not int or not low <= value <= high:
            raise UserError('error_integer', field=key, low=low, high=high)
        if key in ('width', 'height') and value % 32:
            raise UserError('error_dimensions')
        spec[key] = value
    cfg = data.get('cfg', 1)
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

    def __init__(self, directory, engine, max_storage_bytes=10 * 1024**3, min_free_bytes=1024**3, max_records=10000):
        self.root = Path(directory).resolve()
        self.images = self.root / 'images'
        self.images.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'history.sqlite3'
        self.engine = engine
        self.lock = threading.RLock()
        self.max_storage_bytes = max_storage_bytes
        self.min_free_bytes = min_free_bytes
        self.max_records = max_records
        self.reservations = {}
        self.events = {}
        self.queue = queue.Queue()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY)')
        self.recover_deletions()
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

    def page(self, number=1, favorites=False):
        page_size = 24
        where = "WHERE json_extract(body, '$.favorite') = 1" if favorites else ''
        with self.connect() as db:
            # Count and rows must describe the same snapshot while generation continues.
            db.execute('BEGIN')
            total = db.execute(f'SELECT COUNT(*) FROM jobs {where}').fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            number = min(number, pages)
            rows = db.execute(f'SELECT body FROM jobs {where} ORDER BY rowid DESC LIMIT ? OFFSET ?',
                              (page_size, (number - 1) * page_size))
            items = [json.loads(row[0]) for row in rows]
        return dict(items=items, total=total, page=number, pages=pages, page_size=page_size)

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

    def recover_deletions(self):
        # A staged PNG is restored if its DB transaction did not commit; otherwise discard it.
        for staged in self.images.glob('*.png.deleting'):
            image_id = staged.name.removesuffix('.png.deleting')
            if len(image_id) != 32 or any(c not in '0123456789abcdef' for c in image_id):
                continue
            with self.connect() as db:
                exists = db.execute('SELECT id FROM jobs WHERE id=? UNION ALL SELECT id FROM uploads WHERE id=?',
                                    (image_id, image_id)).fetchone()
            if exists:
                os.replace(staged, self.images / f'{image_id}.png')
            else:
                staged.unlink()

    def _delete_records(self, ids, table):
        # Caller holds self.lock; table is selected internally, never from a request.
        staged = []
        try:
            with self.connect() as db:
                for image_id in ids:
                    original = self.images / f'{image_id}.png'
                    if original.exists():
                        temporary = self.images / f'{image_id}.png.deleting'
                        os.replace(original, temporary)
                        staged.append((original, temporary))
                    db.execute(f'DELETE FROM {table} WHERE id=?', (image_id,))
        except (OSError, sqlite3.Error):
            for original, temporary in staged:
                os.replace(temporary, original)
            raise DeleteError('error_delete_failed') from None
        try:
            for _, temporary in staged:
                temporary.unlink()
        except OSError:
            raise DeleteError('error_delete_cleanup') from None

    def delete(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job_id in self.events or job['status'] in ('queued', 'running'):
                raise DeleteError('error_delete_active')
            with self.connect() as db:
                referenced = db.execute("SELECT 1 FROM jobs WHERE json_extract(body, '$.spec.source_id')=? LIMIT 1",
                                        (job_id,)).fetchone()
            if referenced:
                raise DeleteError('error_delete_referenced')
            self._delete_records([job_id], 'jobs')

    def delete_unused_uploads(self, keep=None):
        if keep is not None and (len(keep) != 32 or any(c not in '0123456789abcdef' for c in keep)):
            raise UserError('error_source_id')
        with self.lock:
            with self.connect() as db:
                ids = [row[0] for row in db.execute(
                    "SELECT id FROM uploads WHERE id NOT IN "
                    "(SELECT json_extract(body, '$.spec.source_id') FROM jobs "
                    "WHERE json_extract(body, '$.spec.source_id') IS NOT NULL)")
                       if row[0] != keep]
            self._delete_records(ids, 'uploads')
            return len(ids)

    def check_storage(self, incoming=0, *, exclude=None, new_records=0):
        # Call under self.lock so concurrent uploads and output saves share one budget.
        paths = list(self.images.iterdir()) + list(self.root.glob('history.sqlite3*'))
        used = sum(p.stat().st_size for p in paths if p.is_file())
        reserved = sum(size for job_id, size in self.reservations.items() if job_id != exclude)
        if used + reserved + incoming > self.max_storage_bytes:
            raise StorageError('error_storage_limit')
        if shutil.disk_usage(self.root).free < self.min_free_bytes + reserved + incoming:
            raise StorageError('error_disk_space')
        if new_records:
            with self.connect() as db:
                count = db.execute('SELECT (SELECT COUNT(*) FROM jobs) + (SELECT COUNT(*) FROM uploads)').fetchone()[0]
            if count + new_records > self.max_records:
                raise StorageError('error_record_limit')

    def save_image(self, image, image_id, *, pnginfo=None, new_records=0):
        buffer = io.BytesIO()
        image.save(buffer, format='PNG', pnginfo=pnginfo)
        payload = buffer.getvalue()
        # Leave room for the associated SQLite update and its rollback journal.
        self.check_storage(len(payload) + 256 * 1024, exclude=image_id, new_records=new_records)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.images, suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
            os.replace(temporary, self.images / f'{image_id}.png')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def upload(self, image):
        with self.lock:
            image_id = uuid.uuid4().hex
            self.save_image(image, image_id, new_records=1)
            try:
                with self.connect() as db:
                    db.execute('INSERT INTO uploads VALUES (?)', (image_id,))
            except Exception:
                (self.images / f'{image_id}.png').unlink(missing_ok=True)
                raise
            return image_id

    def submit(self, data):
        spec = validate_spec(data)
        count = data.get('num_images', 1)
        if type(count) is not int or not 1 <= count <= 8:
            raise UserError('error_integer', field='num_images', low=1, high=8)
        with self.lock:
            if spec['source_id']:
                try:
                    self.image_path(spec['source_id'])
                except KeyError:
                    raise UserError('error_source_missing') from None
            if len(self.events) + count > 20:
                raise UserError('error_queue')
            reservation = 5 * spec['width'] * spec['height'] + 1024**2
            self.check_storage(count * reservation, new_records=count)
            batch_id = uuid.uuid4().hex
            batch = []
            for index in range(count):
                job = dict(id=uuid.uuid4().hex,
                           spec={**spec, 'seed': (spec['seed'] + index) % 2**32},
                           batch_id=batch_id, batch_index=index + 1, batch_size=count,
                           status='queued', created_at=time.time(), progress=0,
                           phase='phase_queued', error=None, image_url=None,
                           favorite=False, notes='', engine=self.engine.name)
                batch.append(job)
            # Persist the entire batch before making any item available to the worker.
            with self.connect() as db:
                db.executemany('INSERT INTO jobs VALUES (?,?)',
                               [(job['id'], json.dumps(job, ensure_ascii=False)) for job in batch])
            for job in batch:
                self.reservations[job['id']] = reservation
                self.events[job['id']] = threading.Event()
                self.queue.put(job['id'])
        # Preserve the original single-job response fields for existing clients.
        return {**batch[0], 'job_ids': [job['id'] for job in batch]}

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
                    self.save_image(result, job_id, pnginfo=info)
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
                    self.reservations.pop(job_id, None)
                self.queue.task_done()

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        if self.worker.is_alive():
            self.queue.put(None)
            self.worker.join(timeout=5)
