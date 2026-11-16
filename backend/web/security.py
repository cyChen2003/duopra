"""Public transport policy, independent of native audio and tool execution."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

def scoped_cookie_name(name, path):
    scope = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:16]
    return f'{name}_{scope}'

def public_proof(proof):
    proof = proof or {}
    allowed = ('backend', 'model_variant', 'grpo_update', 'thinker_dtype', 'speech_dtype', 'waveform_dtype',
               'native_first_samples', 'native_subsequent_samples', 'realtime_gate_passed',
               'public_max_new_speak_tokens_per_chunk', 'text_decode_mode')
    result = {k: proof[k] for k in allowed if k in proof}
    projector = proof.get('semantic_projector_release', {})
    result['final_projector_loaded'] = projector.get('loaded') is True
    result['acoustic_acceptance_claimed'] = False
    return result


def public_message(value):
    value = dict(value)
    if value.get('type') in ('unit_trace', 'debug_tick'):
        return None
    for key in ('checkpoint', 'reference_audio', 'model_proof', 'directory', 'deployment_application'):
        value.pop(key, None)
    if value.get('type') in ('fatal_error', 'command_error'):
        value['error'] = 'Request could not be completed. Check the input or retry after ending the current session.'
    return value


def allowed_backend(base):
    url = urlsplit(base)
    hosts = {x.strip().lower() for x in os.getenv('DUPLEX_HARNESS_ALLOWED_HOSTS', 'api.deepseek.com').split(',') if x.strip()}
    try:
        port = url.port
    except ValueError:
        raise ValueError('Invalid backend port') from None
    if (url.scheme != 'https' or url.hostname not in hosts or port not in (None, 443)
            or url.username or url.password or url.query or url.fragment):
        raise ValueError('Backend must be an operator-allowlisted HTTPS origin on port 443')
    return base


def worker_environment():
    names = ('PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
             'DUPLEX_HARNESS_DEPS', 'DUPLEX_HARNESS_PYTHON', 'DUPLEX_HARNESS_ALLOWED_HOSTS',
             'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy')
    return {k: v for k, v in os.environ.items() if k in names} | {
        'CUDA_VISIBLE_DEVICES': '',
        'PYTHONDONTWRITEBYTECODE': '1', 'HOME': '/tmp',
    }


class PublicAccess:
    def __init__(self, app, origin, credentials_file=None, development=False):
        self.app = app
        self.origin = origin.rstrip('/')
        url = urlsplit(self.origin)
        if (url.scheme not in ('http', 'https') or not url.netloc or url.path or url.query or url.fragment
                or url.username or url.password or (not development and url.scheme != 'https')):
            raise ValueError('An exact HTTPS public origin is required')
        self.development = development
        self.auth_slots = asyncio.Semaphore(4)
        self.users = {}
        if not development:
            path = Path(credentials_file)
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ValueError('Access file must be private and not a symlink')
            self.users = json.loads(path.read_text())
            if not isinstance(self.users, dict) or not self.users:
                raise ValueError('Configure at least one access identity')
            for name, row in self.users.items():
                if (not isinstance(name, str) or ':' in name or not isinstance(row, dict)
                        or set(row) != {'salt', 'sha256'} or len(bytes.fromhex(row['salt'])) != 16
                        or len(bytes.fromhex(row['sha256'])) != 64):
                    raise ValueError('Invalid access credential record')

    def authorized(self, headers):
        try:
            kind, value = headers.get('authorization', '').split(' ', 1)
            if kind.lower() != 'basic':
                return False
            name, password = base64.b64decode(value, validate=True).decode().split(':', 1)
            row = self.users.get(name)
            if row is None or len(password) > 256:
                return False
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(row['salt']), n=16384, r=8, p=1).hex()
            return hmac.compare_digest(actual, row['sha256'])
        except (ValueError, UnicodeError):
            return False

    def accepts_origin(self, headers):
        if not self.development:
            return headers.get('origin') == self.origin
        try:
            origin = urlsplit(headers.get('origin', ''))
        except ValueError:
            return False
        return (origin.scheme in ('http', 'https')
                and origin.hostname in ('localhost', '127.0.0.1', '::1')
                and origin.netloc == headers.get('host')
                and not (origin.username or origin.password or origin.path or origin.query or origin.fragment))

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope['headers']}
        path = scope['path']
        health = path in ('/healthz', '/health') and scope['type'] == 'http'
        accepted = self.development or health
        if not accepted:
            async with self.auth_slots:
                accepted = await asyncio.to_thread(self.authorized, headers)
        if not accepted:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'code': 'authentication_required'}, status_code=401,
                headers={'WWW-Authenticate': 'Basic realm="Duopra", charset="UTF-8"', 'Cache-Control': 'no-store'})(scope, receive, send)
        mutation = scope['type'] == 'websocket' or scope.get('method') not in ('GET', 'HEAD', 'OPTIONS')
        if mutation and not self.accepts_origin(headers):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'code': 'origin_rejected'}, status_code=403)(scope, receive, send)
        if path.startswith(('/api/debug/', '/api/metrics', '/docs', '/openapi.json', '/redoc')):
            return await JSONResponse({'code': 'not_available'}, status_code=404)(scope, receive, send)
        maximum = 10 * 1024**2 if '/audio' in path else 32768
        used = 0
        async def bounded_receive():
            nonlocal used
            event = await receive()
            if event['type'] == 'http.request':
                used += len(event.get('body', b''))
                if used > maximum:
                    from starlette.exceptions import HTTPException
                    raise HTTPException(413, 'Request body too large')
            return event
        async def secure_send(event):
            if event['type'] == 'http.response.start':
                items = [(k, v) for k, v in event.get('headers', []) if k.lower() not in (b'server', b'cache-control')]
                items += [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                          (b'referrer-policy', b'no-referrer'), (b'x-frame-options', b'DENY'),
                          (b'permissions-policy', b'microphone=(self), camera=()')]
                if not self.development:
                    items = [(k, v + b'; Secure' if k.lower() == b'set-cookie' and b'secure' not in v.lower() else v) for k, v in items]
                event = dict(event, headers=items)
            await send(event)
        await self.app(scope, bounded_receive, secure_send)
