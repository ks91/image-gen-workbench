"""Read the application's generation metadata without loading a model or server."""
import argparse
import json
import sys
import warnings

from PIL import Image


def main(argv=None):
    parser = argparse.ArgumentParser(description='Show Image Gen Workbench generation metadata in a PNG.')
    parser.add_argument('image', help='Path to a PNG file (read-only)')
    args = parser.parse_args(argv)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(args.image, formats=('PNG',)) as image:
                # Load through IEND: PNG text chunks may follow the pixel data.
                image.load()
                generation = image.info.get('generation')
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        print(f'Could not read PNG: {error}', file=sys.stderr)
        return 1
    if generation is None:
        print('No generation metadata found. Other metadata fields are not checked.')
        return 0
    try:
        settings = json.loads(generation)
    except (ValueError, TypeError):
        print('Invalid generation metadata: expected JSON.', file=sys.stderr)
        return 1
    print(json.dumps(settings, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
