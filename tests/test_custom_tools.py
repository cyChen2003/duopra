"""Owner-supplied tools: backend/tools/custom/*.json, next to the committed registry."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend/tools')]
from tool_runtime import Catalog, Executor, SCHEMA_KEYS, ToolError, ToolSession, custom_tool_dir

LIGHTS = {
    'name': 'home.lights',
    'description': 'Turn the lights in one room of the home on or off',
    'parameters': {'type': 'object', 'properties': {'room': {'type': 'string', 'description': 'kitchen, bedroom …'},
                                                    'on': {'type': 'boolean'}},
                   'required': ['room', 'on'], 'additionalProperties': False},
    'returns': {'type': 'object', 'properties': {'room': {'type': 'string'}, 'on': {'type': 'boolean'}},
                'required': ['room', 'on']},
    'read_only': False,
    'example': {'arguments': {'room': 'kitchen', 'on': True}, 'result': {'room': 'kitchen', 'on': True}},
}


class Endpoint:
    """A local HTTP tool server that records what it receives."""
    def __init__(self, reply=None, status=200):
        self.seen = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.seen.append({'path': self.path, 'body': body, 'tool': self.headers.get('X-Duopra-Tool'),
                                   'key': self.headers.get('Idempotency-Key')})
                data = json.dumps(reply(body) if reply else body).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            def log_message(self, *args):
                pass
        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}/lights'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


class CustomToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name, value):
        (self.dir / name).write_text(json.dumps(value) if not isinstance(value, str) else value, encoding='utf-8')

    def endpoint(self, **kw):
        server = Endpoint(**kw); self.addCleanup(server.close); return server

    def test_without_owner_tools_the_committed_identity_is_unchanged(self):
        catalog = Catalog(custom_dir=self.dir)
        self.assertEqual(len(catalog.tools), 68)
        self.assertEqual(catalog.sha256, catalog.registry['sha256'])
        self.assertEqual(catalog.custom, [])

    def test_the_directory_comes_from_the_environment(self):
        with patch.dict(os.environ, {'DUPLEX_CUSTOM_TOOLS': str(self.dir)}):
            self.assertEqual(custom_tool_dir(), self.dir)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(custom_tool_dir(), ROOT / 'backend/tools/custom')

    def test_an_owner_tool_joins_the_catalog_and_discovery(self):
        self.write('home.lights.json', {**LIGHTS, 'endpoint': 'http://127.0.0.1:8765/lights'})
        catalog = Catalog(custom_dir=self.dir)
        self.assertEqual(len(catalog.tools), 69)
        self.assertNotEqual(catalog.sha256, catalog.builtin_sha256)
        self.assertEqual(tuple(catalog.schema('home.lights')), SCHEMA_KEYS)
        self.assertEqual(catalog.discover('turn on the kitchen lights')[0]['name'], 'home.lights')
        card = next(c for c in catalog.cards() if c['name'] == 'home.lights')
        self.assertEqual((card['runtime_mode'], card['custom'], card['requires_session_authorization'], card['domain']),
                         ('custom', True, True, 'custom'))
        self.assertTrue(all(c['custom'] is False for c in catalog.cards() if c['name'] != 'home.lights'))

    def test_the_endpoint_receives_the_arguments_and_its_result_is_checked(self):
        server = self.endpoint()
        self.write('home.lights.json', {**LIGHTS, 'endpoint': server.url})
        executor = Executor(Catalog(custom_dir=self.dir))
        value = executor.execute('home.lights', {'room': 'bedroom', 'on': False}, personal_authorized=True)
        self.assertEqual(value['result'], {'room': 'bedroom', 'on': False})
        self.assertEqual((value['source'], value['is_fixture']), ('custom_endpoint', False))
        self.assertEqual(server.seen[0]['body'], {'room': 'bedroom', 'on': False})
        self.assertEqual((server.seen[0]['path'], server.seen[0]['tool']), ('/lights', 'home.lights'))
        executor.execute('home.lights', {'room': 'bedroom', 'on': True}, personal_authorized=True)
        self.assertNotEqual(server.seen[0]['key'], server.seen[1]['key'])

    def test_a_result_outside_returns_is_rejected(self):
        server = self.endpoint(reply=lambda body: {'room': body['room']})
        self.write('home.lights.json', {**LIGHTS, 'endpoint': server.url})
        with self.assertRaisesRegex(ToolError, 'result is missing required fields'):
            Executor(Catalog(custom_dir=self.dir)).execute('home.lights', {'room': 'hall', 'on': True}, personal_authorized=True)

    def test_an_endpoint_error_is_reported_not_invented(self):
        server = self.endpoint(status=500)
        self.write('home.lights.json', {**LIGHTS, 'endpoint': server.url})
        with self.assertRaises(ToolError) as caught:
            Executor(Catalog(custom_dir=self.dir)).execute('home.lights', {'room': 'hall', 'on': True}, personal_authorized=True)
        self.assertEqual(caught.exception.code, 'custom_unavailable')
        self.assertIn('HTTP 500', str(caught.exception))

    def test_a_tool_that_changes_something_needs_session_permission(self):
        server = self.endpoint()
        self.write('home.lights.json', {**LIGHTS, 'endpoint': server.url})
        catalog = Catalog(custom_dir=self.dir)
        with self.assertRaises(ToolError) as caught:
            Executor(catalog).execute('home.lights', {'room': 'hall', 'on': True})
        self.assertEqual(caught.exception.code, 'authorization_required')
        session = ToolSession('custom-test', catalog, initial_tools=('home.lights',))
        self.addCleanup(session.close)
        action = json.dumps({'op': 'call', 'call_id': 'c1', 'tool': 'home.lights', 'arguments': {'room': 'hall', 'on': True}})
        with self.assertRaises(ToolError) as caught:
            session.submit_private(action)
        self.assertEqual(caught.exception.code, 'authorization_required')
        self.assertEqual(server.seen, [])

    def test_without_an_endpoint_the_example_answers_as_a_fixture(self):
        self.write('home.lights.json', {**LIGHTS, 'read_only': True})
        value = Executor(Catalog(custom_dir=self.dir)).execute('home.lights', {'room': 'kitchen', 'on': True})
        self.assertEqual((value['result'], value['is_fixture']), ({'room': 'kitchen', 'on': True}, True))

    def test_definitions_that_cannot_be_honoured_are_rejected_by_file(self):
        cases = {
            'clash.json': ({**LIGHTS, 'name': 'weather.day'}, 'already the name'),
            'name.json': ({**LIGHTS, 'name': 'Lights'}, 'lowercase words joined by dots'),
            'pattern.json': ({**LIGHTS, 'parameters': {**LIGHTS['parameters'], 'properties': {
                'room': {'type': 'string', 'pattern': '^[a-z]+$'}, 'on': {'type': 'boolean'}}}}, 'unsupported keywords: pattern'),
            'array.json': ({**LIGHTS, 'returns': {'type': 'object', 'properties': {'rooms': {'type': 'array'}}}}, 'needs items'),
            'scheme.json': ({**LIGHTS, 'endpoint': 'ftp://127.0.0.1/lights'}, 'http'),
            'nothing.json': ({k: v for k, v in LIGHTS.items() if k != 'example'}, 'endpoint, or an example result'),
            'example.json': ({**LIGHTS, 'example': {'arguments': {'room': 'kitchen'}, 'result': LIGHTS['example']['result']}}, 'missing required fields'),
            'typo.json': ({**LIGHTS, 'endpont': 'http://127.0.0.1/lights'}, 'unknown fields: endpont'),
            'broken.json': ('{"name": ', 'not valid JSON'),
        }
        for file, (value, message) in cases.items():
            with self.subTest(file=file):
                for old in self.dir.glob('*.json'):
                    old.unlink()
                self.write(file, value)
                with self.assertRaises(ToolError) as caught:
                    Catalog(custom_dir=self.dir)
                self.assertEqual(caught.exception.code, 'custom_tool')
                self.assertIn(file, str(caught.exception))
                self.assertIn(message, str(caught.exception))

    def test_example_files_are_not_loaded(self):
        self.write('home.lights.json.example', {**LIGHTS, 'endpoint': 'http://127.0.0.1:8765/lights'})
        self.assertEqual(len(Catalog(custom_dir=self.dir).tools), 68)


if __name__ == '__main__':
    unittest.main()
