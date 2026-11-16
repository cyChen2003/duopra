"""Local NVIDIA CUDA entrypoint, listening on loopback."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / path) for path in ('web', 'tools', 'runtime')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--port', type=int, default=23479)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    from launch import configuration, check, serve
    config = configuration(args.model, args.inventory)
    if args.check:
        print(json.dumps(check(config), indent=2))
        return
    state = os.environ.get('DUPLEX_RUN_DIR', str(ROOT.parent / 'state'))
    serve(config, state, 'localhost', args.port, f'http://127.0.0.1:{args.port}', development=True)


if __name__ == '__main__':
    main()
