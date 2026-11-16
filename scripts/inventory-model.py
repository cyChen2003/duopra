"""Pin local model bytes; this records integrity, not publisher authenticity."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.model).resolve(strict=True)
    output = Path(args.output).resolve()
    if output.is_relative_to(root):
        parser.error('Inventory must be stored outside the model directory')
    if output.exists():
        parser.error('Inventory already exists; do not silently repin changed model bytes')
    provenance = json.loads((root / 'PROVENANCE.json').read_text())
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            parser.error('Model may not contain symlinks')
        if not path.is_file():
            continue
        value = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                value.update(block)
        files[str(path.relative_to(root))] = value.hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(dict(schema='duopra.standalone.v1', update=provenance['grpo_update'],
                       actor_sha256=provenance['actor_sha256'], files=files), stream, indent=2)
        stream.write('\n')
    print(f'Pinned {len(files)} files; update {provenance["grpo_update"]}')


if __name__ == '__main__':
    main()
