#!/usr/bin/env python3
"""Clone-and-run bootstrap. Packaging commands still delegate to setuptools."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import venv


def bootstrap():
    parser = argparse.ArgumentParser(description='Set up Image Gen Workbench in a project-local .venv')
    parser.add_argument('--ui-only', action='store_true', help='Install the interface without model dependencies')
    parser.add_argument('--reuse-env', type=Path, help='Reuse dependencies from an existing environment with the same Python version (read-only)')
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or later is required.')
    root = Path(__file__).resolve().parent
    environment = root / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        venv.create(environment, with_pip=True)
    if args.reuse_env:
        reference = args.reuse_env.resolve() / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        probe = "import json,sys,sysconfig; print(json.dumps([list(sys.version_info[:2]),sysconfig.get_path('purelib')]))"
        version, site_packages = json.loads(subprocess.check_output([str(reference), '-c', probe], text=True))
        own_version, own_packages = json.loads(subprocess.check_output([str(python), '-c', probe], text=True))
        if version != own_version:
            parser.error('Run setup.py with the same Python major.minor version as the reference environment.')
        (Path(own_packages) / 'qwen-reference.pth').write_text(site_packages + '\n', encoding='utf-8')
    target = str(root) + ('' if args.ui_only else '[model]')
    subprocess.run([str(python), '-m', 'pip', 'install', '-e', target], check=True)
    print('\nSetup complete. Start with: ./run' + (' --demo' if args.ui_only else ''))
    print('Tests: .venv/bin/python -m unittest discover -s tests -v')


if __name__ == '__main__':
    if len(sys.argv) == 1 or sys.argv[1] in ('--ui-only', '--reuse-env', '--help', '-h'):
        bootstrap()
    else:
        from setuptools import setup
        setup()
