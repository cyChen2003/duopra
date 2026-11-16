import ast
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/tools')]
from personal_harness import normalize
from personal_client import PERSONAL_TOOLS
from personal_store import PersonalStore, PersonalError
from tool_runtime import Catalog, Executor, ToolSession, ToolError, canonical


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PersonalStore(Path(self.tmp.name)/'test.sqlite3')
        self.token=self.store.bootstrap()['workspace_token'];self.catalog=Catalog()
        self.executor=Executor(self.catalog,personal_scope=self.token)
        self.session=ToolSession('isolated-qa',self.catalog,executor=self.executor,
            initial_tools=['calendar.agenda','calendar.create','calendar.update','calendar.delete','calendar.restore','task.create','task.update','task.list'])
        self.session.authorize_personal(True)
        self.session.before_input_unit(0);self.addCleanup(self.session.close)
        self.patch=patch('tool_runtime.personal_execute',side_effect=lambda token,name,args,auth,key:
            self.store.execute(token,name,args,authorized=auth,request_key=key))
        self.patch.start();self.addCleanup(self.patch.stop)

    def call(self,name,args,op=None):
        # Outer and argument order may change: no textual ordering check.
        action={'arguments':args,'tool':name,'op':op or name.split('.')[1],
                'call_id':'qa'+str(self.session.execution_count)}
        self.session.submit_private(canonical(action),self.session.unit_index)
        deadline=time.monotonic()+2
        while self.session.pending and time.monotonic()<deadline:
            time.sleep(.005);self.session.before_input_unit(self.session.unit_index+1)
        self.assertFalse(self.session.pending)
        event=[e for e in self.session.events if e['type'] in ('completed','failed')][-1]
        self.assertEqual(event['type'],'completed',event)
        return event['payload']['result']

    def test_all_ten_operations_preserve_real_ids_and_store(self):
        e=self.call('calendar.create',{'title':'QA event','end':'2026-11-04T11:00+08:00','start':'2026-11-04T10:00+08:00'})['item']
        self.assertEqual(self.call('calendar.agenda',{'date':'2026-11-04','calendar_id':'personal'})['events'][0]['event_id'],e['event_id'])
        changed=self.call('calendar.update',{'title':'QA revised','event_id':e['event_id'],'expected_revision':1})
        self.assertEqual(changed['item']['title'],'QA revised')
        self.call('calendar.delete',{'event_id':e['event_id']})
        self.call('calendar.restore',{'event_id':e['event_id']})
        t=self.call('task.create',{'title':'QA task'})['item']
        self.call('task.update',{'completed':True,'task_id':t['task_id']})
        self.assertTrue(self.call('task.list',{'status':'done'})['items'][0]['completed'])
        self.session.expose(['calendar.agenda','calendar.create','calendar.update','task.create','task.update','task.list','task.delete','task.restore'])
        self.session.before_input_unit(self.session.unit_index+1)
        self.call('task.delete',{'task_id':t['task_id']})
        self.call('task.restore',{'task_id':t['task_id']})
        audit=[e for e in self.session.events if e['type']=='protocol_compatibility' and e.get('adapter')=='personal_operation_to_call']
        self.assertGreaterEqual(len(audit),10)
        self.assertTrue(all(not e['business_arguments_changed'] for e in audit))

    def test_mismatched_and_untyped_aliases_rejected_without_normalization(self):
        for tool,op in [('calendar.create','update'),('weather.day','update'),([], 'update'),({},'update'),('task.update',None)]:
            a={'tool':tool,'op':op,'arguments':{}}
            self.assertEqual(normalize(a),(a,None))
        a={'tool':'task.update','op':'update','arguments':{},'changes':{}}
        self.assertEqual(normalize(a),(a,None))

    def test_no_auth_and_hidden_tool_still_rejected(self):
        self.session.authorize_personal(False)
        with self.assertRaises(ToolError) as c:self.call('task.create',{'title':'forbidden'})
        self.assertEqual(c.exception.code,'authorization_required')
        self.session.authorize_personal(True);self.session.visible.remove('task.create')
        with self.assertRaises(ToolError) as c:self.call('task.create',{'title':'hidden'})
        self.assertEqual(c.exception.code,'schema_not_visible')

    def test_redundant_known_outer_field_only_if_exactly_equal(self):
        a={'op':'call','tool':'calendar.create','arguments':{'calendar_id':'personal'},'calendar_id':'personal'}
        fixed,audit=normalize(a,self.catalog.tools)
        self.assertEqual(fixed['arguments'],a['arguments']);self.assertNotIn('calendar_id',fixed)
        self.assertEqual(audit['removed_redundant_outer_fields'],['calendar_id'])
        for x in [dict(a,calendar_id='work'),dict(a,unknown=True),dict(a,arguments={})]:
            self.assertEqual(normalize(x,self.catalog.tools),(x,None))

    def test_field_error_feedback_names_the_actual_problem(self):
        a={'op':'call','call_id':'bad','tool':'calendar.create','calendar_id':'work','arguments':{'calendar_id':'personal'}}
        try:self.session.submit_private(canonical(a))
        except ToolError as e:self.session.report_error(e,action=a)
        error=self.session.outbox[-1]['value']
        self.assertIn('calendar_id',error['error']['message'])
        self.assertTrue(error['call_contract']['business_fields_only_inside_arguments'])

    def test_agenda_write_arguments_prompt_discovery_without_executing(self):
        session = ToolSession('agenda-replay', self.catalog, executor=self.executor,
                              initial_tools=['calendar.agenda'], discovery_mode='function')
        self.addCleanup(session.close)
        session.before_input_unit(0)
        action = {'op': 'call', 'call_id': 'c3', 'tool': 'calendar.agenda',
                  'arguments': {'calendar_id': 'personal', 'date': '2026-11-13',
                                'starts': '10:00', 'title': '开会'}}
        with self.assertRaises(ToolError) as rejected:
            session.submit_private(canonical(action))
        self.assertEqual(rejected.exception.code, 'unknown_argument')
        session.report_error(rejected.exception, action=action)
        feedback, = session.before_input_unit(1)
        error = json.loads(feedback.removeprefix('<backend>').removesuffix('</backend>'))['error']
        self.assertIn('read-only', error['message'])
        self.assertEqual(error['suggested_action']['arguments'], {'query': 'calendar.create'})
        self.assertFalse(session.pending)
        session.submit_private(canonical(error['suggested_action']))
        session.before_input_unit(2)
        self.assertIn('calendar.create', session.visible)
        session.report_error(rejected.exception, action=action)
        feedback, = session.before_input_unit(3)
        error = json.loads(feedback.removeprefix('<backend>').removesuffix('</backend>'))['error']
        self.assertIn('use calendar.create', error['message'])
        self.assertNotIn('suggested_action', error)
        with self.assertRaises(ToolError) as unauthorized:
            session.submit_private(canonical({
                'op': 'call', 'call_id': 'create', 'tool': 'calendar.create',
                'arguments': {'title': '开会', 'start': '2026-11-13T10:00+08:00',
                              'end': '2026-11-13T11:00+08:00'},
            }))
        self.assertEqual(unauthorized.exception.code, 'authorization_required')

    def test_observed_redundant_calendar_field_executes_once_with_real_result(self):
        a={'op':'call','tool':'calendar.create','schema_version':'1','calendar_id':'personal',
           'arguments':{'calendar_id':'personal','title':'QA','start':'2026-11-04T10:00+08:00','end':'2026-11-04T11:00+08:00'}}
        self.session.submit_private(canonical(a))
        for _ in range(100):
            time.sleep(.005);self.session.before_input_unit(self.session.unit_index+1)
            if not self.session.pending:break
        done=[e for e in self.session.events if e['type']=='completed']
        self.assertEqual(len(done),1);self.assertEqual(done[0]['payload']['result']['item']['calendar_id'],'personal')
        self.assertEqual(self.store.execute(self.token,'calendar.agenda',{'date':'2026-11-04','calendar_id':'personal'},authorized=True)['count'],1)

    def test_unknown_field_empty_patch_and_missing_id_not_repaired(self):
        for args,code in [({'title':'x'},'missing_argument'),({'task_id':'not-inferred'},'empty_update'),
                          ({'task_id':'x','changes':{'title':'x'}},'unknown_argument'),
                          ({'task_id':'x','completed':'true'},'schema_type')]:
            with self.assertRaises(ToolError) as c:self.call('task.update',args)
            self.assertEqual(c.exception.code,code)
        self.assertFalse(self.session.pending)

    def test_host_metadata_recovery_after_exact_alias(self):
        self.session.submit_private(canonical({'op':'create','tool':'task.create','arguments':{'title':'metadata only'}}))
        for _ in range(100):
            time.sleep(.005);self.session.before_input_unit(self.session.unit_index+1)
            if not self.session.pending:break
        self.assertFalse(self.session.pending)
        self.assertTrue(any(e.get('adapter')=='host_allocated_correlation_id' for e in self.session.events))

    def test_schema_copy_is_not_mutated_and_id_source_explicit(self):
        s=self.catalog.schema('calendar.update')
        self.assertIn('payload.result.item.event_id',s['parameters']['properties']['event_id']['description'])
        self.assertIn('op=call',s['description']);self.assertIn('changes',s['description'])
        self.assertEqual(self.catalog.schema('calendar.update')['description'],s['description'])
        self.assertNotEqual(self.catalog.tools['calendar.update']['description'],s['description'])


class RouterTests(unittest.TestCase):
    def router(self):
        tree=ast.parse((ROOT/'backend/web/server.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TokenRouter')
        scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),'TokenRouter','exec'),scope)
        class Tokens:
            all_special_ids=[2000000,2000001,2000002];added_tokens_decoder={}
            def convert_tokens_to_ids(self,s):return {'<delegate>':2000000,'</delegate>':2000001,'<|turn_eos|>':2000002}[s]
            def decode(self,ids,**kw):return ''.join(chr(i) for i in ids if i not in self.all_special_ids)
        return scope['TokenRouter'](Tokens())

    def test_multi_unit_and_json_field_reordering(self):
        a={'arguments':{'title':'new','task_id':'tk_test'},'tool':'task.update','op':'update','call_id':'x'}
        raw=canonical(a);r=self.router()
        self.assertFalse(r.feed([2000000]+list(map(ord,raw[:23])))['actions'])
        got=r.feed(list(map(ord,raw[23:]))+[2000001,2000002])
        self.assertEqual(got['actions'],[a]);self.assertEqual(got['text'],'')
        self.assertFalse(got['error_details'])

    def test_unclosed_does_not_execute_or_expose_private_body(self):
        raw=canonical({'op':'update','tool':'calendar.update','arguments':{'event_id':'secret-id','title':'private-title'}})
        for body,code in [(raw,'delegate_close_missing'),(raw[:-2],'private_json_incomplete')]:
            got=self.router().feed([2000000]+list(map(ord,body))+[2000002])
            self.assertFalse(got['actions']);self.assertEqual(got['text'],'')
            self.assertEqual(got['error_details'][0]['code'],code)
            self.assertNotIn('secret-id',canonical(got['error_details']))
            self.assertNotIn('private-title',canonical(got['error_details']))

    def test_feedback_is_causally_injected_and_not_execute(self):
        s=ToolSession('unclosed',initial_tools=['calendar.update']);self.addCleanup(s.close)
        s.before_input_unit(0)
        got=self.router().feed([2000000]+list(map(ord,'{"op":"call"'))+[2000002])
        d=got['error_details'][0]
        s.report_error(ToolError(d['code'],d['message']),action=d['action_reference'])
        obs=s.before_input_unit(1)
        self.assertTrue(any('incomplete_action_executed' in canonical(o) for o in obs))
        self.assertFalse(s.pending)
        self.assertTrue(any(e['type']=='rejected' and e['code']=='private_json_incomplete' for e in s.events))

    def test_nested_delegate_retry_waits_for_turn_end(self):
        router = self.router()
        session = ToolSession('nested', initial_tools=['calendar.create'])
        self.addCleanup(session.close)
        session.before_input_unit(0)
        opened, closed, eos = 2000000, 2000001, 2000002
        result = router.feed([opened, ord('{'), opened])
        self.assertEqual(result['actions'], [])
        detail, = result['error_details']
        self.assertEqual(detail['code'], 'nested_private_span')
        session.report_error(ToolError(detail['code'], detail['message']),
                             turn_quarantined=router.quarantined)
        feedback, = session.before_input_unit(1)
        error = json.loads(feedback.removeprefix('<backend>').removesuffix('</backend>'))['error']
        self.assertTrue(error['wait_for_turn_eos'])
        self.assertIn('before retrying', error['retry_guidance'])
        action = {'op': 'call', 'tool': 'calendar.create', 'arguments': {'title': 'QA event'}}
        tokens = [opened, *map(ord, canonical(action)), closed]
        premature = router.feed(tokens)
        self.assertEqual((premature['actions'], premature['text']), ([], ''))
        self.assertTrue(router.feed([eos])['ended'])
        self.assertEqual(router.feed(tokens)['actions'], [action])


if __name__=='__main__':unittest.main(verbosity=2)
