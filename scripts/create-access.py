"""Create a private access hash file without putting passwords in argv or logs."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
name = input('Access name (use a non-personal alias): ').strip()
password = getpass.getpass('Password (at least 16 characters): ')
if not name or ':' in name or len(password) < 16 or password != getpass.getpass('Confirm password: '):
    raise SystemExit('Invalid access name or password confirmation')
salt = secrets.token_bytes(16)
value = {name: {'salt': salt.hex(), 'sha256': hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()}}
args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(value, stream)
print('Private access file created. No credentials were printed.')
