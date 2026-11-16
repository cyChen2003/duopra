import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'backend/web'), str(ROOT/'backend/tools'), str(ROOT/'backend/runtime')]
from security import PublicAccess, allowed_backend, public_message, public_proof, worker_environment
from starlette.testclient import TestClient
from fastapi import FastAPI, Request, WebSocket


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'access.json';salt=b'0'*16
        password='synthetic-password-for-tests'
        path.write_text(json.dumps({'demo':{'salt':salt.hex(),'sha256':hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()}}));path.chmod(0o600)
        self.auth='Basic '+base64.b64encode(('demo:'+password).encode()).decode()
        app=FastAPI()
        @app.get('/healthz')
        async def health():return {'ready':True}
        @app.get('/')
        async def index():return {'ok':True}
        @app.post('/echo')
        async def echo(request:Request):return {'length':len(await request.body())}
        @app.websocket('/ws')
        async def ws(socket:WebSocket):
            await socket.accept();await socket.send_json({'ok':True});await socket.close()
        self.client=TestClient(PublicAccess(app,'https://voice.example.org',path),base_url='https://voice.example.org')
        self.addCleanup(self.client.close)

    def test_auth_and_health(self):
        self.assertEqual(self.client.get('/').status_code,401)
        self.assertEqual(self.client.get('/healthz').status_code,200)
        response=self.client.get('/',headers={'Authorization':self.auth})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.headers['x-frame-options'],'DENY')

    def test_exact_origin_and_body_limit(self):
        auth={'Authorization':self.auth}
        self.assertEqual(self.client.post('/echo',headers=auth).status_code,403)
        headers=dict(auth,Origin='https://voice.example.org')
        self.assertEqual(self.client.post('/echo',headers=headers,content='x'*32769).status_code,413)
        self.assertEqual(self.client.post('/echo',headers=headers,content='hello').json()['length'],5)
        self.assertEqual(self.client.post('/echo',headers=dict(auth,Origin='http://voice.example.org')).status_code,403)

    def test_websocket_auth_and_origin(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/ws',headers={'Origin':'https://voice.example.org'}):pass
        with self.client.websocket_connect('/ws',headers={'Origin':'https://voice.example.org','Authorization':self.auth}) as ws:
            self.assertTrue(ws.receive_json()['ok'])

    def test_debug_exports_blocked(self):
        self.assertEqual(self.client.get('/api/debug/example/download',headers={'Authorization':self.auth}).status_code,404)

    def test_public_proof_and_messages_drop_internal_state(self):
        proof=public_proof({'model_variant':'Duopra','node':'private','checkpoint':'private','secret':'hidden'})
        self.assertNotIn('private',json.dumps(proof));self.assertNotIn('hidden',json.dumps(proof))
        self.assertIsNone(public_message({'type':'unit_trace','private_actions':['hidden']}))
        value=public_message({'type':'ready','upload_token':'session-only','checkpoint':'private'})
        self.assertEqual(value['upload_token'],'session-only');self.assertNotIn('checkpoint',value)

    def test_backend_allowlist_and_environment(self):
        with patch.dict(os.environ,{'DUPLEX_HARNESS_ALLOWED_HOSTS':'api.deepseek.com','SENSITIVE_TEST_VALUE':'hidden'}):
            for url in ('http://api.deepseek.com','https://127.0.0.1','https://example.org','https://api.deepseek.com:8443'):
                with self.assertRaises(ValueError):allowed_backend(url)
            self.assertEqual(allowed_backend('https://api.deepseek.com/anthropic'),'https://api.deepseek.com/anthropic')
            self.assertNotIn('SENSITIVE_TEST_VALUE',worker_environment())


class UploadTests(unittest.TestCase):
    def test_decoded_duration_bounded(self):
        import io
        import numpy as np
        import soundfile as sf
        from upload_audio import decode_audio
        stream=io.BytesIO();sf.write(stream,np.zeros(8000,dtype='float32'),8000,format='WAV')
        self.assertEqual(len(decode_audio(stream.getvalue())),16000)
        with self.assertRaises(Exception):decode_audio(b'not an audio file')


class LocalAccessTests(unittest.TestCase):
    def app(self):
        app=FastAPI()
        @app.post('/echo')
        async def echo():return {'ok':True}
        @app.websocket('/ws')
        async def websocket(socket:WebSocket):
            await socket.accept();await socket.send_json({'ok':True});await socket.close()
        return PublicAccess(app,'http://127.0.0.1:23479',development=True)

    def test_loopback_aliases_accept_their_own_http_and_websocket_origin(self):
        for host in ('localhost','127.0.0.1','[::1]'):
            origin=f'http://{host}:23479'
            headers={'Origin':origin,'Host':f'{host}:23479'}
            with self.subTest(host=host), TestClient(self.app(),base_url='http://127.0.0.1:23479') as client:
                self.assertEqual(client.post('/echo',headers=headers).status_code,200)
                with client.websocket_connect('/ws',headers=headers) as ws:
                    self.assertTrue(ws.receive_json()['ok'])

    def test_local_mode_still_rejects_cross_origin_requests(self):
        with TestClient(self.app(),base_url='http://localhost:23479') as client:
            for origin in ('http://example.org','http://localhost:23480','http://127.0.0.1:23479'):
                self.assertEqual(client.post('/echo',headers={'Origin':origin}).status_code,403)
        with TestClient(self.app(),base_url='http://example.org') as client:
            self.assertEqual(client.post('/echo',headers={'Origin':'http://example.org'}).status_code,403)


if __name__=='__main__':unittest.main()
