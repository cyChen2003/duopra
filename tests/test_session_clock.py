"""Date facts reach both models; calendar writes keep the supplied ISO date."""
import base64
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend/web'),str(ROOT/'backend/tools'),str(ROOT/'backend/runtime')]
from deployment_model import serving_config
from temporal_context import SessionClock, clock_payload
from server import create_app
from test_voice_interrupt import ScriptedOwner


class SessionClockTests(unittest.TestCase):
    now=datetime(2026,11,12,3,tzinfo=timezone.utc)

    def config(self):
        return serving_config({'update':420},'/unused/model',ROOT/'deploy/nvidia/profile.json')

    def test_tomorrow_is_explicitly_one_calendar_day_after_today(self):
        context=clock_payload(self.now)['session_context']
        self.assertEqual(context['current_date'],'2026-11-12')
        self.assertEqual(context['timezone'],'Asia/Shanghai')
        self.assertEqual(context['relative_dates'],{
            'today':'2026-11-12','tomorrow':'2026-11-13','day_after_tomorrow':'2026-11-14'})

    def test_clock_refreshes_at_shanghai_midnight(self):
        clock=SessionClock(enabled=True)
        def context(unit,now):
            text,=clock.before_input_unit(unit,now)
            return json.loads(text.removeprefix('<backend>').removesuffix('</backend>'))['session_context']
        before=context(0,datetime(2026,11,12,15,59,tzinfo=timezone.utc))
        after=context(1,datetime(2026,11,12,16,tzinfo=timezone.utc))
        self.assertEqual(before['current_date'],'2026-11-12')
        self.assertEqual(after['current_date'],'2026-11-13')
        self.assertEqual(after['relative_dates']['tomorrow'],'2026-11-14')
        self.assertEqual(clock.before_input_unit(2,datetime(2026,11,12,17,tzinfo=timezone.utc)),[])

    def test_first_audio_unit_receives_date_before_tools_without_changing_system(self):
        with tempfile.TemporaryDirectory() as directory:
            config=self.config();owner=ScriptedOwner([('',True)])
            observations=[];original=owner.step
            def observe(wave,values):
                observations.extend(values)
                return original(wave,values)
            owner.step=observe
            with patch('temporal_context.clock_payload',return_value=clock_payload(self.now)), \
                    TestClient(create_app(config,'test',directory,owner)) as client:
                with client.websocket_connect('/ws?initial_tools=[]') as ws:
                    while ws.receive_json()['type']!='ready':pass
                    ws.send_json({'type':'audio','format':'pcm16','sample_rate':16000,
                                  'data':base64.b64encode(bytes(16560*2)).decode()})
                    while ws.receive_json()['type']!='timing':pass
                    ws.send_json({'type':'stop_session'})
            first=json.loads(observations[0].removeprefix('<backend>').removesuffix('</backend>'))
            self.assertEqual(first['session_context']['relative_dates']['tomorrow'],'2026-11-13')
            self.assertEqual(config['system_prompt'],'Streaming Omni Conversation.')

    def test_harness_system_prompt_uses_the_same_host_date_facts(self):
        spec=importlib.util.spec_from_file_location('clock_test_worker',ROOT/'backend/harness/worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        captured={}
        class Harness:
            def __init__(self,**kwargs):
                captured['env_prompt']=kwargs['env']['DSH_SYSTEM_PROMPT']
                entries=json.loads(Path(kwargs['patches'][0]).read_text())
                captured['persona']=next(item['config']['personaPrefix'] for item in entries if item.get('id')=='system-prompt')
            def __enter__(self):return self
            def __exit__(self,*args):pass
        request=dict(task='帮我创建一个明天九点的日程，提醒我去开会',runtime_check=True,
                     config=dict(base_url='https://api.deepseek.com/anthropic',api_key='',model='test',protocol='deepseek'))
        modules={'deepseek_harness':SimpleNamespace(DeepSeekHarness=Harness),
                 'deepseek_harness_runtime':SimpleNamespace(bundled_runtime_path=lambda:None)}
        with patch.dict(sys.modules,modules), patch.object(worker.sys,'stdin',io.StringIO(json.dumps(request))), \
                patch.object(worker,'emit'), patch('temporal_context.clock_payload',return_value=clock_payload(self.now)):
            worker.main()
        self.assertEqual(captured['env_prompt'],captured['persona'])
        self.assertIn('"tomorrow":"2026-11-13"',captured['env_prompt'])

    def test_calendar_http_keeps_explicit_dates_through_save_and_agenda(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ,{'DUPLEX_PERSONAL_DB':str(Path(directory)/'personal.sqlite3')}):
            owner=ScriptedOwner([])
            with TestClient(create_app(self.config(),'test',directory,owner)) as client:
                self.assertTrue(client.get('/api/personal/profile').json()['available'])
                headers={'x-duplex-personal-request':'1'}
                for date in ('2026-11-12','2026-11-13','2026-11-14'):
                    args={'title':'Date regression','start':date+'T09:00+08:00','end':date+'T10:00+08:00'}
                    created=client.post('/api/personal/execute',headers=headers,
                                        json={'tool':'calendar.create','arguments':args})
                    self.assertEqual(created.status_code,200,created.text)
                    self.assertEqual(created.json()['payload']['result']['item']['start'],args['start'])
                    agenda=client.post('/api/personal/execute',headers=headers,
                                       json={'tool':'calendar.agenda','arguments':{'calendar_id':'personal','date':date}})
                    items=agenda.json()['payload']['result']['events']
                    self.assertEqual([item['start'] for item in items],[args['start']])
