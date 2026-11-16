"""Privacy-oriented release scan. Reports locations only, never matching values."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '.venv', '.conda', 'node_modules', '__pycache__', '.secrets', '.pytest_cache'}
PATTERNS = {
    'access_token': re.compile(r'(?:github' + r'_pat_[A-Za-z0-9_]{25,}|gh[pousr]_[A-Za-z0-9]{25,}|sk-[A-Za-z0-9_-]{24,})'),
    'private_key': re.compile(r'-----BEGIN (?:OPENSSH|RSA|EC|DSA|PRIVATE).*KEY-----'),
    'private_mount': re.compile(r'/(?:root/venus[^\s\"\']*|taijifs[^\s\"\']*)'),
    'internal_domain': re.compile(r'\b(?:[a-z0-9-]+\.)+(?:oa|woa)\.com\b|mirrors\.tencent\.com'),
    'credential_url': re.compile(r'https?://[^\s/\"\']+:[^\s/@\"\']+@'),
}
BLOCKED = {'.pt', '.pth', '.safetensors', '.sqlite', '.sqlite3', '.db', '.wav', '.flac', '.mp3', '.ogg', '.mp4', '.pem', '.key', '.jsonl', '.log', '.zip', '.gz'}
BINARY = {'.woff2', '.png'}


def scan(paths, staged=False):
    findings = []
    for path in paths:
        relative = path.relative_to(ROOT)
        if not staged and any(x in IGNORED for x in relative.parts):
            continue
        if path.is_symlink():
            findings.append({'file': str(relative), 'reason': 'symlink'})
            continue
        if (path.suffix in BLOCKED or path.name in ('access.json', 'release.json')
                or path.name.startswith('.env') and path.name != '.env.example'
                or any(x in ('private', 'models', 'state', 'runs', 'logs', '.codex', '.ssh', '.codebuddy') for x in relative.parts)):
            findings.append({'file': str(relative), 'reason': 'private_or_runtime_artifact'})
            continue
        if path.suffix in BINARY:
            if not str(relative).startswith('frontend/static/'):
                findings.append({'file': str(relative), 'reason': 'unexpected_binary'})
            continue
        try:
            raw = subprocess.check_output(['git', '-C', str(ROOT), 'show', ':' + str(relative)]) if staged else path.read_bytes()
            text = raw.decode('utf-8')
        except (UnicodeError, OSError, subprocess.CalledProcessError):
            findings.append({'file': str(relative), 'reason': 'unreadable_or_binary'})
            continue
        for line, content in enumerate(text.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if pattern.search(content):
                    findings.append({'file': str(relative), 'line': line, 'reason': category})
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    if args.staged:
        names = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']).decode().split('\0')
        paths = [ROOT / p for p in names if p]
    else:
        names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).decode().split('\0')
        paths = [ROOT / name for name in names if name and ((ROOT / name).is_file() or (ROOT / name).is_symlink())]
    findings = scan(paths, args.staged)
    print(json.dumps({'passed': not findings, 'findings': findings,
                      'scope': 'Pattern and file-type checks; manual privacy/licensing review remains required'}, indent=2))
    raise SystemExit(bool(findings))


if __name__ == '__main__':
    main()
