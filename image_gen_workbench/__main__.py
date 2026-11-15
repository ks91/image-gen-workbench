import argparse
import os
from pathlib import Path

from filelock import FileLock, Timeout

from .app import create_app
from .engine import DemoEngine, QwenEngine


def main():
    parser = argparse.ArgumentParser(description='Image Gen Workbench — http://127.0.0.1:7860')
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--device', choices=['auto', 'mps', 'cuda', 'cpu'], default='auto')
    parser.add_argument('--dtype', choices=['auto', 'bfloat16', 'float16', 'float32'], default='auto')
    parser.add_argument('--model', default='Qwen/Qwen-Image-2.1', help='Hugging Face model ID or a local model directory')
    parser.add_argument('--offline', action='store_true', help='Use cached model weights only')
    parser.add_argument('--demo', action='store_true', help='Try the interface with demo images without loading a model')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535.')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(args.data_dir / '.server.lock')
    try:
        with lock.acquire(timeout=0):
            engine = DemoEngine() if args.demo else QwenEngine(args.model, args.device, args.offline, args.dtype)
            app = create_app(args.data_dir, engine=engine)
            try:
                app.run(host='127.0.0.1', port=args.port, threaded=True, debug=False, use_reloader=False)
            finally:
                app.extensions['jobs'].close()
    except Timeout:
        parser.exit(1, 'Another instance is already using this data directory.\n')


if __name__ == '__main__':
    main()
