import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from filelock import FileLock, Timeout
from werkzeug.serving import make_server

from .app import create_app
from .engine import DemoEngine, QwenEngine


def launch_browser(url):
    try:
        if webbrowser.open(url):
            return
    except Exception:
        pass
    print(f'Could not open a browser automatically. Open {url} manually.', file=sys.stderr)


def run_server(app, port, open_browser=True):
    # Bind before opening the browser: an occupied port must not open another app.
    server = make_server('127.0.0.1', port, app, threaded=True)
    url = f'http://127.0.0.1:{port}'
    print(f'Image Gen Workbench — {url} (Ctrl+C to stop)', flush=True)
    try:
        if open_browser:
            threading.Thread(target=launch_browser, args=(url,),
                             daemon=True, name='open-browser').start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description='Image Gen Workbench — http://127.0.0.1:7860')
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--device', choices=['auto', 'mps', 'cuda', 'cpu'], default='auto')
    parser.add_argument('--dtype', choices=['auto', 'bfloat16', 'float16', 'float32'], default='auto')
    parser.add_argument('--model', default='Qwen/Qwen-Image-2.1', help='Hugging Face model ID or a local model directory')
    parser.add_argument('--offline', action='store_true', help='Use cached model weights only')
    parser.add_argument('--demo', action='store_true', help='Try the interface with demo images without loading a model')
    parser.add_argument('--no-browser', action='store_true', help='Start the server without opening a browser')
    parser.add_argument('--max-storage-gb', type=int, default=10, help='Managed data limit in GiB (default: 10)')
    parser.add_argument('--min-free-gb', type=int, default=1, help='Disk space to keep free in GiB (default: 1)')
    parser.add_argument('--max-records', type=int, default=10000, help='Combined history and upload limit (default: 10000)')
    args = parser.parse_args()
    if args.max_storage_gb < 1 or args.min_free_gb < 0 or args.max_records < 1:
        parser.error('Storage and record limits must be positive; minimum free space must be nonnegative.')
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535.')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(args.data_dir / '.server.lock')
    try:
        with lock.acquire(timeout=0):
            engine = DemoEngine() if args.demo else QwenEngine(args.model, args.device, args.offline, args.dtype)
            app = create_app(args.data_dir, engine=engine,
                             max_storage_bytes=args.max_storage_gb * 1024**3,
                             min_free_bytes=args.min_free_gb * 1024**3, max_records=args.max_records)
            try:
                run_server(app, args.port, open_browser=not args.no_browser)
            finally:
                app.extensions['jobs'].close()
    except Timeout:
        parser.exit(1, 'Another instance is already using this data directory.\n')


if __name__ == '__main__':
    main()
