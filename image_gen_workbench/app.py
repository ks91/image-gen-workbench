from .i18n import UserError, MESSAGES, language, translate, localize_job
import warnings
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import HTTPException

from .engine import QwenEngine
from .jobs import Jobs


def create_app(data_dir='data', engine=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=20 * 1024 * 1024,
                      TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]'])
    jobs = Jobs(data_dir, engine or QwenEngine())
    app.extensions['jobs'] = jobs

    def error_response(key, http_status, **params):
        return jsonify(error=translate(key, language(), **params), error_key=key, error_params=params), http_status

    def present(job):
        return localize_job(job, language())

    @app.after_request
    def content_language(response):
        response.headers['Content-Language'] = language()
        response.vary.update(['Cookie', 'Accept-Language'])
        return response

    @app.before_request
    def local_origin():
        if request.method in ('POST', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return error_response('error_origin', 403)

    @app.errorhandler(ValueError)
    def invalid(exc):
        if isinstance(exc, UserError):
            return error_response(exc.key, 400, **exc.params)
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
        return render_template('index.html', engine=jobs.engine.name, is_demo=jobs.engine.name.startswith('DEMO'),
                               language=lang, messages=MESSAGES, t=lambda key: translate(key, lang))

    @app.get('/api/jobs')
    def history():
        return jsonify([present(job) for job in jobs.list()])

    @app.post('/api/jobs')
    def generate():
        return jsonify(present(jobs.submit(request.get_json()))), 202

    @app.get('/api/jobs/<job_id>')
    def detail(job_id):
        return jsonify(present(jobs.get(job_id)))

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
                with Image.open(file.stream) as source:
                    if source.width * source.height > 20_000_000:
                        raise UserError('error_pixels')
                    image = ImageOps.exif_transpose(source).convert('RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise UserError('error_upload') from None
        image_id = jobs.upload(image)
        return jsonify(id=image_id, image_url=f'/images/{image_id}.png'), 201

    @app.get('/images/<image_id>.png')
    def image(image_id):
        return send_file(jobs.image_path(image_id), mimetype='image/png')

    return app
