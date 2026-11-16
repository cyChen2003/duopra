"""Container entrypoint using the same model and server startup as Conda."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / path) for path in ('web', 'tools', 'runtime')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    from launch import configuration, check, serve
    config = configuration(os.environ.get('DUPLEX_MODEL_DIR', '/models'),
                           os.environ.get('DUPLEX_MODEL_INVENTORY', '/run/secrets/inventory.json'))
    origin = os.environ['DUPLEX_PUBLIC_ORIGIN']
    access = os.environ['DUPLEX_ACCESS_FILE']
    from security import PublicAccess
    PublicAccess(None, origin, access)
    if args.check:
        print(json.dumps(dict(check(config), access_valid=True), indent=2))
        return
    serve(config, os.environ.get('DUPLEX_RUN_DIR', '/var/lib/duopra'),
          '0.0.0.0', int(os.environ.get('DUPLEX_PORT', '8000')), origin, access)


if __name__ == '__main__':
    main()
