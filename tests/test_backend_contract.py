import copy
import json
from pathlib import Path
import sys
import unittest
import os
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/tools')]
from harness_backend import BackendStore, validated_config, public_config, session_catalog, SCHEMA, NAME
from tool_runtime import Catalog, ToolSession, ToolError, validate, model_backend_value


class ConfigSecurityTests(unittest.TestCase):
    def setUp(self):
        policy=patch.dict(os.environ,{'DUPLEX_HARNESS_ALLOWED_HOSTS':'api.deepseek.com,another.example,self-hosted.example'})
        policy.start();self.addCleanup(policy.stop)

    def config(self, **values):
        return dict(base_url='https://api.deepseek.com',model='deepseek-v4-flash',
                    protocol='deepseek',api_key='private-fixture-key',**values)

    def test_key_is_write_only_and_browser_scoped(self):
        store=BackendStore();a=store.identity(None);b=store.identity(None)
        public=store.save(a,self.config())
        self.assertNotIn('api_key',public);self.assertNotIn('private-fixture-key',json.dumps(public))
        self.assertTrue(public['has_key']);self.assertIsNone(store.get(b))

    def test_changing_origin_does_not_forward_old_credential(self):
        old=validated_config(self.config())
        new=validated_config(dict(base_url='https://another.example/v1',model='new-model',api_key=''),old)
        self.assertEqual(new['api_key'],'')
        same=validated_config(dict(base_url=old['base_url'],model='new-model',api_key=''),old)
        self.assertEqual(same['api_key'],old['api_key'])
        with self.assertRaises(ValueError):
            validated_config(dict(base_url=old['base_url'].replace('https:','http:'),model='new-model',api_key=''),old)

    def test_credentials_cannot_be_put_in_url_or_config_metadata(self):
        for url in ['https://'+'name:secret@example.com','https://example.com?key=secret',
                    'file:///private/demo.key','https://example.com/#token','https://example.com/\n']:
            with self.subTest(url=url),self.assertRaises(ValueError):
                validated_config(dict(base_url=url,model='m'))
        with self.assertRaises(ValueError):validated_config(dict(self.config(),workspace='/root'))

    def test_invalid_model_and_deadline_are_rejected(self):
        for model in ['','bad\nmodel','model with spaces']:
            with self.assertRaises(ValueError):validated_config(dict(self.config(),model=model))
        for seconds in [0,601,True,'180']:
            with self.assertRaises(ValueError):validated_config(dict(self.config(),deadline_seconds=seconds))

    def test_owner_event_streams_do_not_cross(self):
        store=BackendStore();a=store.identity(None);b=store.identity(None)
        store.event(a,dict(type='backend_started',task_id='one',origin='manual'))
        self.assertEqual(len(store.events(a)),1);self.assertEqual(store.events(b),[])
        self.assertEqual(store.events(a,after=1),[])

    def test_missing_or_stale_cookie_polling_never_allocates(self):
        store=BackendStore()
        for _ in range(1000):
            self.assertIsNone(store.existing(None));self.assertIsNone(store.existing('old-server-cookie'))
            self.assertEqual(store.events(None),[])
        self.assertEqual(store.owners,{})
        cookie=store.identity(None)
        self.assertEqual(store.existing(cookie),cookie)
        self.assertEqual(len(store.owners),1)

    def test_unconfigured_slots_can_be_reclaimed_but_configured_owners_are_kept(self):
        store=BackendStore();protected=store.identity(None);store.save(protected,self.config())
        for _ in range(128):store.identity(None)
        self.assertEqual(len(store.owners),64)
        self.assertEqual(store.get(protected)['api_key'],'private-fixture-key')

    def test_native_and_compatible_endpoints_remain_distinct(self):
        self.assertEqual(validated_config(self.config())['base_url'], 'https://api.deepseek.com/anthropic')
        self.assertEqual(validated_config(dict(self.config(), protocol='openai-compatible'))['base_url'],
                         'https://api.deepseek.com')
        self.assertEqual(validated_config(dict(self.config(), base_url='https://self-hosted.example/messages'))['base_url'],
                         'https://self-hosted.example/messages')


class HelperProtocolTests(unittest.TestCase):
    def setUp(self):
        self.base=Catalog();self.store=BackendStore();self.cookie=self.store.identity(None)

    def configured(self):
        self.store.save(self.cookie,dict(base_url='https://api.deepseek.com',model='deepseek-v4-flash',api_key='secret-value'))
        return session_catalog(self.base,self.store,self.cookie)

    def test_repeated_malformed_calls_stop_feeding_back_until_new_speech(self):
        session = ToolSession('voice', self.base, initial_tools=['timezone.offset'])
        self.addCleanup(session.close)
        session.before_input_unit(0)
        feedback = []
        for unit in range(1, 9):
            # Changing call_id and alternating parser errors must not keep the loop alive.
            code = 'action_fields' if unit % 2 else 'nested_private_span'
            session.report_error(ToolError(code, 'Invalid tool call'), action={'call_id': f'c{unit}'})
            feedback.extend(session.before_input_unit(unit))
        self.assertEqual(len(feedback), 3)
        self.assertIn('stop_retrying', feedback[-1])
        self.assertEqual(sum(e['type'] == 'rejected' for e in session.events), 3)
        session.submit_private(json.dumps({'op': 'call', 'call_id': 'valid', 'tool': 'timezone.offset',
            'arguments': {'city': '上海', 'instant': '2026-11-12T10:00:00Z'}}))
        self.assertTrue(session.pending)
        session.begin_user_speech()
        session.report_error(ToolError('action_fields', 'A new invalid tool call'))
        self.assertTrue(any('action_fields' in value for value in session.before_input_unit(9)))

    def test_hidden_tool_recovery_uses_discovery_then_configured_harness_on_no_match(self):
        catalog, fixed = self.configured()
        session = ToolSession('voice', catalog, initial_tools=['weather.day'],
                              discovery_mode='function', fixed_meta_schemas=fixed)
        self.addCleanup(session.close)
        session.before_input_unit(0)
        call = {'op': 'call', 'call_id': 'quote', 'tool': 'stock.quote', 'arguments': {'symbol': 'AAPL'}}
        with self.assertRaises(ToolError) as error:
            session.submit_private(json.dumps(call))
        self.assertEqual(error.exception.code, 'schema_not_visible')
        session.report_error(error.exception, action=call)
        feedback, = session.before_input_unit(1)
        observed = json.loads(feedback.removeprefix('<backend>').removesuffix('</backend>'))
        retry = observed['error']['suggested_action']
        self.assertEqual(retry['tool'], 'tools.discover')
        self.assertFalse(session.pending)
        session.submit_private(json.dumps(retry))
        session.before_input_unit(2)
        self.assertIn('stock.quote', session.visible)
        session.submit_private(json.dumps({
            'op': 'call', 'call_id': 'no-match', 'tool': 'tools.discover',
            'arguments': {'query': 'zxqvzzzz'},
        }))
        feedback, = session.before_input_unit(3)
        observed = json.loads(feedback.removeprefix('<backend>').removesuffix('</backend>'))
        self.assertEqual(observed['error']['fallback_tool'], NAME)

    def test_unconfigured_backend_is_not_presented_as_available(self):
        catalog,fixed=session_catalog(self.base,self.store,self.cookie)
        self.assertIs(catalog,self.base);self.assertEqual(fixed,[]);self.assertNotIn(NAME,catalog.tools)

    def test_query_has_exactly_one_task_argument(self):
        for args in [dict(city='Shanghai',date='2026-11-06'),dict(task='查天气',city='Shanghai'),dict(task='')]:
            with self.assertRaises(ToolError):validate(args,SCHEMA['parameters'])
        validate(dict(task='完整任务\n包含用户给出的全部信息。'),SCHEMA['parameters'])
        validate(dict(task='字'*1600),SCHEMA['parameters'])
        with self.assertRaises(ToolError):validate(dict(task='字'*1601),SCHEMA['parameters'])

    def test_business_string_limits_stay_unchanged(self):
        with self.assertRaises(ToolError):validate('字'*1001,dict(type='string'))

    def test_backend_names_and_credentials_do_not_enter_helper_schema(self):
        catalog,fixed=self.configured();text=json.dumps(fixed,ensure_ascii=False)
        self.assertNotIn('secret-value',text)
        self.assertNotIn('workspace_read',text)
        self.assertNotIn('math_arithmetic',text)
        self.assertNotIn(NAME,self.base.tools)

    def test_backend_trace_is_ui_only_not_model_observation(self):
        value={'call_id':'q1','payload':{'source':'deepseek_harness',
            'result':{'status':'done','answer':'六十二点一元','missing_information':[]},
            'provenance':{'tools_called':[{'name':'workspace_read','arguments':{'path':'travel_policy.md'}}]}}}
        observed=model_backend_value(value)
        self.assertNotIn('workspace_read',json.dumps(observed))
        self.assertNotIn('provenance',observed['payload'])
        self.assertEqual(observed['payload']['result'],value['payload']['result'])
        self.assertIn('provenance',value['payload'])

    def test_fixed_helper_does_not_consume_or_replace_eight_hotbar_slots(self):
        catalog,fixed=self.configured();names=sorted(n for n in self.base.tools if not n.startswith(('task.','calendar.')))[:8]
        session=ToolSession('meta-test',catalog,initial_tools=names,discovery_mode='function',fixed_meta_schemas=fixed)
        try:
            self.assertEqual(len(session.hot_tools),8);self.assertIn(NAME,session.visible)
            session.expose(names);observations=session.before_input_unit(0)
            self.assertTrue(any(NAME in x for x in observations))
            self.assertNotIn('secret-value',''.join(observations))
            session.expose([]);session.before_input_unit(1)
            self.assertEqual(session.hot_tools,set());self.assertIn(NAME,session.visible)
        finally:session.close()


if __name__=='__main__':unittest.main()
