"""Opt-in real GPU smoke test: python tests/smoke_model.py (not unittest discovery)."""
import json
import time
from pathlib import Path

from image_gen_workbench.app import create_app
from image_gen_workbench.engine import QwenEngine


def main():
    app = create_app(Path('data') / 'smoke', QwenEngine(device='mps', offline=True))
    client = app.test_client()

    def generate(prompt, source_id=None):
        response = client.post('/api/jobs', json=dict(prompt=prompt, source_id=source_id,
                                                    width=512, height=512, steps=28, seed=42))
        assert response.status_code == 202, response.json
        job_id = response.json['id']
        phase = None
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            job = client.get('/api/jobs/' + job_id).json
            if job['phase'] != phase:
                print(job['status'], job['phase'], flush=True)
                phase = job['phase']
            if job['status'] in ('succeeded', 'failed', 'cancelled'):
                print(json.dumps(job, ensure_ascii=False), flush=True)
                assert job['status'] == 'succeeded', job.get('error')
                return job_id
            time.sleep(1)
        raise TimeoutError('Inference exceeded 30 minutes')

    try:
        first = generate('A red ceramic mug on a wooden table, soft daylight, photograph.')
        generate('Change the red mug to blue. Keep the table and lighting unchanged.', first)
    finally:
        app.extensions['jobs'].close()


if __name__ == '__main__':
    main()
