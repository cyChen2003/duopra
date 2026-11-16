import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/tools'), str(ROOT/'backend')]
from fastapi.testclient import TestClient
from server import create_app
from preview import create_preview


class ApplicationTests(unittest.TestCase):
    def test_real_application_routes_with_cpu_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json'
            config.write_text(json.dumps({'system_prompt':'Streaming Omni Conversation.',
                'session_clock':{'enabled':True,'timezone':'Asia/Shanghai'},'tool_discovery':{'mode':'function'}}))
            owner=SimpleNamespace(ready=True,active=None,proof={'model_variant':'CPU test owner',
                'grpo_update':420,'private_path':'not-public','semantic_projector_release':{'loaded':True}},
                pool=ThreadPoolExecutor(max_workers=1))
            with TestClient(create_app(str(config),'private-test-checkpoint',directory,owner)) as client:
                self.assertEqual(client.get('/').status_code,200)
                self.assertTrue(client.get('/healthz').json()['ready'])
                proof=client.get('/api/model-proof').json()
                self.assertNotIn('private_path',proof['proof'])
                self.assertTrue(proof['proof']['final_projector_loaded'])
                self.assertEqual(len([t for t in client.get('/api/tools').json()['tools'] if not t['custom']]),68)
                self.assertEqual(client.get('/api/status').json()['upload_limit_mb'],10)
                self.assertNotIn('private-test-checkpoint',client.get('/api/config').text)
            self.assertNotIn('private_path',(Path(directory)/'MODEL_READY.json').read_text())

    def test_unloaded_owner_fails_before_worker_deadlock(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json';config.write_text('{"system_prompt":"Streaming Omni Conversation."}')
            pool=ThreadPoolExecutor(max_workers=1)
            owner=SimpleNamespace(ready=False,active=None,proof={},pool=pool)
            try:
                with self.assertRaisesRegex(RuntimeError,'main thread'):
                    with TestClient(create_app(str(config),'test',directory,owner)):pass
            finally:pool.shutdown()

    def test_preview_cannot_claim_a_model(self):
        with TestClient(create_preview(),base_url='http://127.0.0.1:23479') as client:
            self.assertEqual(client.get('/').status_code,200)
            self.assertFalse(client.get('/api/model-proof').json()['ready'])
            with client.websocket_connect('ws://127.0.0.1:23479/ws',headers={'Origin':'http://127.0.0.1:23479'}) as ws:
                self.assertEqual(ws.receive_json()['code'],'preview_only')

    def test_compose_secret_paths_match_contract(self):
        text=(ROOT/'compose.nvidia.yaml').read_text()
        self.assertIn('target: inventory.json',text)
        self.assertIn('target: access.json',text)
        self.assertNotIn('ports: ["8000',text)


if __name__=='__main__':unittest.main()
