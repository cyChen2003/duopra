from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/tools')]
from fastapi.testclient import TestClient
from server import create_app


class ConnectedFeatureTests(unittest.TestCase):
    def test_calendar_http_lifecycle_and_harness_runtime_status(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DUPLEX_PERSONAL_DB': folder+'/personal.sqlite3'}):
            config = Path(folder)/'config.json'
            config.write_text(json.dumps({'system_prompt':'Streaming Omni Conversation.'}))
            owner = SimpleNamespace(ready=True, active=None, proof={}, pool=ThreadPoolExecutor(max_workers=1))
            with patch('harness_runtime.runtime_status', return_value={'ready': True, 'sdk_version':'0.2.1rc2'}), \
                    TestClient(create_app(str(config),'test',folder,owner)) as client:
                self.assertTrue(client.get('/api/personal/profile').json()['available'])
                headers={'x-duplex-personal-request':'1'}
                def call(name, args):
                    response=client.post('/api/personal/execute',headers=headers,json={'tool':name,'arguments':args})
                    self.assertEqual(response.status_code,200,response.text)
                    return response.json()['payload']['result']
                item=call('calendar.create',{'title':'验收日程','start':'2026-11-10T14:00+08:00','end':'2026-11-10T15:00+08:00'})['item']
                self.assertEqual(call('calendar.agenda',{'calendar_id':'personal','date':'2026-11-10'})['count'],1)
                call('calendar.delete',{'event_id':item['event_id']})
                self.assertEqual(call('calendar.agenda',{'calendar_id':'personal','date':'2026-11-10'})['count'],0)
                value=client.get('/api/harness/config').json()
                self.assertTrue(value['runtime']['ready'])
                self.assertTrue(value['personal_available'])
                self.assertEqual(len([t for t in value['business_tools'] if not t['custom']]),68)
                self.assertEqual(value['model'],value['default_model'])
                self.assertNotIn('api_key',value)
