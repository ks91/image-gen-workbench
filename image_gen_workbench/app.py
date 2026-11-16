from .i18n import UserError, MESSAGES, language, translate, localize_job
import io
import hashlib
from pathlib import Path
import warnings
import threading
import time
from collections import deque
from flask import Flask, jsonify, render_template, request, send_file, g
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import HTTPException

from .engine import QwenEngine
from .jobs import Jobs


def create_app(data_dir='data', engine=None, **storage_limits):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=20 * 1024 * 1024,
                      WRITE_REQUESTS_PER_MINUTE=60, MAX_FORM_PARTS=10,
                      TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]'])
    jobs = Jobs(data_dir, engine or QwenEngine(), **storage_limits)
    app.extensions['jobs'] = jobs
    upload_slots = threading.BoundedSemaphore(2)
    app.extensions['upload_slots'] = upload_slots
    writes = deque()
    rate_lock = threading.Lock()

    def error_response(key, http_status, **params):
        return jsonify(error=translate(key, language(), **params), error_key=key, error_params=params), http_status

    def present(job):
        return localize_job(job, language())

    @app.after_request
    def content_language(response):
        response.headers['Content-Language'] = language()
        response.vary.update(['Cookie', 'Accept-Language'])
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.before_request
    def local_origin():
        if request.method in ('POST', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and origin != request.host_url.rstrip('/')):
                return error_response('error_origin', 403)

            with rate_lock:
                now = time.monotonic()
                while writes and writes[0] <= now - 60:
                    writes.popleft()
                if len(writes) >= app.config['WRITE_REQUESTS_PER_MINUTE']:
                    response, status = error_response('error_rate_limit', 429)
                    response.headers['Retry-After'] = '60'
                    return response, status
                writes.append(now)
        if request.endpoint == 'upload' and request.method == 'POST':
            if not upload_slots.acquire(blocking=False):
                response, status = error_response('error_upload_busy', 429)
                response.headers['Retry-After'] = '2'
                return response, status
            g.upload_slot = True
            with jobs.lock:
                jobs.check_storage(new_records=1)

    @app.teardown_request
    def release_upload_slot(error):
        if g.pop('upload_slot', False):
            upload_slots.release()

    @app.errorhandler(ValueError)
    def invalid(exc):
        if isinstance(exc, UserError):
            return error_response(exc.key, getattr(exc, 'status_code', 400), **exc.params)
        return error_response('error_bad_request', 400)

    @app.errorhandler(KeyError)
    def missing(exc):
        return error_response('error_missing', 404)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        key = {400: 'error_bad_request', 404: 'error_missing', 405: 'error_method', 413: 'error_too_large', 415: 'error_media'}.get(exc.code, 'error_http')
        return error_response(key, exc.code, **({'status': exc.code} if key == 'error_http' else {}))

    @app.get('/')
    def home():
        lang = language()
        asset_versions = {name: hashlib.sha256((Path(app.static_folder) / name).read_bytes()).hexdigest()[:16]
                          for name in ('style.css', 'i18n.js', 'app.js')}
        return render_template('index.html', engine=jobs.engine.name, is_demo=jobs.engine.name.startswith('DEMO'),
                               language=lang, messages=MESSAGES, asset_versions=asset_versions, t=lambda key: translate(key, lang))

    @app.get('/api/jobs')
    def history():
        try:
            page = int(request.args.get('page', '1'))
        except ValueError:
            raise UserError('error_page') from None
        favorites = request.args.get('favorites', '0')
        if page < 1 or favorites not in ('0', '1'):
            raise UserError('error_page')
        result = jobs.page(page, favorites == '1')
        result['items'] = [present(job) for job in result['items']]
        return jsonify(result)

    @app.post('/api/jobs')
    def generate():
        return jsonify(present(jobs.submit(request.get_json()))), 202

    @app.get('/api/jobs/<job_id>')
    def detail(job_id):
        return jsonify(present(jobs.get(job_id)))

    @app.delete('/api/jobs/<job_id>')
    def delete_job(job_id):
        jobs.delete(job_id)
        return jsonify(deleted=job_id)

    @app.delete('/api/uploads/unused')
    def delete_unused_uploads():
        return jsonify(deleted=jobs.delete_unused_uploads(request.args.get('keep')))

    @app.post('/api/jobs/<job_id>/cancel')
    def cancel(job_id):
        return jsonify(present(jobs.cancel(job_id)))

    @app.patch('/api/jobs/<job_id>')
    def annotate(job_id):
        data = request.get_json()
        if not isinstance(data, dict):
            raise UserError('error_json')
        changes = {}
        if 'favorite' in data:
            if type(data['favorite']) is not bool:
                raise UserError('error_favorite')
            changes['favorite'] = data['favorite']
        if 'notes' in data:
            if not isinstance(data['notes'], str) or len(data['notes']) > 4000:
                raise UserError('error_notes')
            changes['notes'] = data['notes']
        return jsonify(present(jobs.update(job_id, **changes)))

    @app.post('/api/uploads')
    def upload():
        file = request.files.get('image')
        if not file:
            raise UserError('error_upload_missing')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(file.stream, formats=('PNG', 'JPEG', 'WEBP')) as source:
                    if source.width * source.height > 20_000_000:
                        raise UserError('error_pixels')
                    image = ImageOps.exif_transpose(source).convert('RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise UserError('error_upload') from None
        image_id = jobs.upload(image)
        return jsonify(id=image_id, image_url=f'/images/{image_id}.png'), 201

    @app.get('/images/<image_id>.png')
    def image(image_id):
        try:
            if request.args.get('metadata') == '0':
                with Image.open(jobs.image_path(image_id), formats=('PNG',)) as source:
                    clean = source.copy()
                clean.info.clear()
                output = io.BytesIO()
                clean.save(output, format='PNG')
                output.seek(0)
                return send_file(output, mimetype='image/png', as_attachment=True,
                                 download_name=f'qwen-{image_id}-clean.png')
            return send_file(jobs.image_path(image_id), mimetype='image/png')
        except FileNotFoundError:
            raise KeyError(image_id) from None

    return app
