from concurrent.futures import ThreadPoolExecutor
import importlib.util
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/tools')]
from personal_store import PersonalStore, PersonalError
from tool_runtime import Catalog, Executor, ToolError, ToolSession, canonical, validate


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'db.sqlite3'
        self.store = PersonalStore(self.path)
        self.token = self.store.bootstrap()['workspace_token']
        self.catalog = Catalog()

    def call(self, name, args, key=None, token=None):
        validate(args, self.catalog.tools[name]['parameters'])
        result = self.store.execute(token or self.token, name, args, authorized=True, request_key=key)
        validate(result, self.catalog.tools[name]['returns'], 'result')
        return result

    def event(self, **changes):
        value = {'title':'项目讨论','start':'2026-10-28T14:00+08:00','end':'2026-10-28T14:30+08:00'}
        value.update(changes)
        return value

    def test_calendar_full_lifecycle_and_reopen(self):
        created = self.call('calendar.create', self.event(), 'create1')
        event = created['item']; event_id = event['event_id']
        self.store = PersonalStore(self.path)
        result = self.call('calendar.agenda', {'calendar_id':'personal','date':'2026-10-28'})
        self.assertEqual(result['events'][0]['event_id'], event_id)
        changed = self.call('calendar.update', {'event_id':event_id,'start':'2026-10-29T15:00+08:00','end':'2026-10-29T16:00+08:00','expected_revision':1}, 'edit1')
        self.assertEqual(changed['state'], 'updated')
        self.assertEqual(self.call('calendar.agenda', {'calendar_id':'personal','date':'2026-10-28'})['count'], 0)
        self.assertEqual(self.call('calendar.agenda', {'calendar_id':'personal','date':'2026-10-29'})['count'], 1)
        self.assertTrue(self.call('calendar.delete', {'event_id':event_id}, 'delete1')['undo_available'])
        self.assertEqual(self.call('calendar.agenda', {'calendar_id':'personal','date':'2026-10-29'})['count'], 0)
        self.call('calendar.restore', {'event_id':event_id}, 'restore1')
        self.assertEqual(self.call('calendar.agenda', {'calendar_id':'personal','date':'2026-10-29'})['count'], 1)

    def test_task_lifecycle_complete_reopen_remove_restore(self):
        task = self.call('task.create', {'title':'准备材料'}, 'task-create')['item']
        self.call('task.update', {'task_id':task['task_id'],'completed':True}, 'task-done')
        self.assertEqual(self.call('task.list', {'status':'pending'})['count'], 0)
        self.assertEqual(self.call('task.list', {'status':'done'})['count'], 1)
        self.call('task.update', {'task_id':task['task_id'],'completed':False}, 'task-open')
        self.call('task.delete', {'task_id':task['task_id']}, 'task-del')
        self.assertEqual(self.call('task.list', {})['count'], 0)
        self.call('task.restore', {'task_id':task['task_id']}, 'task-restore')
        self.assertEqual(self.call('task.list', {})['count'], 1)

    def test_model_toggle_true_false_true_does_not_replay_stale_mutation(self):
        def run(token,name,args,authorized,key):
            return self.store.execute(token,name,args,authorized=authorized,request_key=key)
        executor=Executor(self.catalog,personal_scope=self.token)
        with patch('tool_runtime.personal_execute',side_effect=run):
            first=executor.execute('task.create',{'title':'模型待办'},personal_authorized=True)['result']['item']
            for completed in [True,False,True]:
                executor.execute('task.update',{'task_id':first['task_id'],'completed':completed},personal_authorized=True)
        self.assertTrue(self.call('task.list',{})['items'][0]['completed'])

    def test_owner_isolation_cannot_read_or_modify_other_ids(self):
        item = self.call('calendar.create', self.event(), 'create1')['item']
        other = self.store.bootstrap()['workspace_token']
        self.assertEqual(self.call('calendar.agenda', {'calendar_id':'personal','date':'2026-10-28'},token=other)['count'],0)
        for op in ['update','delete','restore']:
            with self.assertRaises(PersonalError):
                self.call('calendar.'+op, {'event_id':item['event_id'],'title':'wrong'} if op=='update' else {'event_id':item['event_id']}, token=other)

    def test_concurrent_retries_commit_once_and_conflicts_do_not_overwrite(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _:self.call('calendar.create',self.event(),'same-request'),range(16)))
        self.assertEqual(len({r['item']['event_id'] for r in results}),1)
        self.assertEqual(self.call('calendar.agenda',{'calendar_id':'personal','date':'2026-10-28'})['count'],1)
        with self.assertRaises(PersonalError) as error:
            self.call('calendar.create',self.event(title='different'),'same-request')
        self.assertEqual(error.exception.code,'idempotency_conflict')

    def test_stale_revision_and_empty_patch_rejected(self):
        event=self.call('calendar.create',self.event(),'create1')['item']
        self.call('calendar.update',{'event_id':event['event_id'],'title':'new','expected_revision':1},'edit1')
        with self.assertRaises(PersonalError) as error:
            self.call('calendar.update',{'event_id':event['event_id'],'title':'stale','expected_revision':1},'edit2')
        self.assertEqual(error.exception.code,'revision_conflict')
        with self.assertRaises(PersonalError):self.call('calendar.update',{'event_id':event['event_id']})

    def test_authorization_identity_and_datetime_required(self):
        for auth in [False,'true',1,None]:
            with self.assertRaises(PersonalError):self.store.execute(self.token,'task.list',{},authorized=auth)
        with self.assertRaises(PersonalError):self.store.execute('wrong','task.list',{},authorized=True)
        for changes in [{'start':'2026-10-28T14:00'},{'end':'2026-10-28T13:00+08:00'},
                        {'start':'2026-02-30T14:00+08:00'},{'title':'  '}]:
            with self.assertRaises(PersonalError):self.call('calendar.create',self.event(**changes))
        self.assertEqual(self.call('calendar.agenda',{'calendar_id':'personal','date':'2026-10-28'})['count'],0)

    def test_cross_midnight_timezone_and_empty_agenda(self):
        self.call('calendar.create',self.event(start='2026-10-28T23:30+08:00',end='2026-10-29T00:30+08:00'))
        for day in ['2026-10-28','2026-10-29']:
            self.assertEqual(self.call('calendar.agenda',{'calendar_id':'personal','date':day})['count'],1)
        self.assertEqual(self.call('calendar.agenda',{'calendar_id':'personal','date':'2026-10-30'})['starts'],'')


class ProtocolTests(unittest.TestCase):
    def test_router_preserves_complete_arguments_across_native_units(self):
        tree=ast.parse((ROOT/'backend/web/server.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='TokenRouter')
        scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),'TokenRouter-test','exec'),scope)
        class Tokenizer:
            all_special_ids=[2000000,2000001,2000002]
            added_tokens_decoder={}
            def convert_tokens_to_ids(self,t):return {'<delegate>':2000000,'</delegate>':2000001,'<|turn_eos|>':2000002}[t]
            def decode(self,ids,**kwargs):return ''.join(chr(i)for i in ids if i not in self.all_special_ids)
        router=scope['TokenRouter'](Tokenizer())
        action={'op':'call','call_id':'c2','tool':'weather.day','schema_version':'2','arguments':{'city':'上海','date':'2026-10-28'}}
        raw=json.dumps(action,ensure_ascii=False)
        chunks=[raw[:31],raw[31:72],raw[72:]]
        self.assertFalse(router.feed([2000000]+list(map(ord,chunks[0])))['actions'])
        self.assertFalse(router.feed(list(map(ord,chunks[1])))['actions'])
        parsed=router.feed(list(map(ord,chunks[2]))+[2000001])
        self.assertEqual(parsed['actions'],[action]);self.assertEqual(parsed['text'],'')

    def test_flat_discovery_is_audited_and_delivered_causally(self):
        session=ToolSession('flat',discovery_mode='function',initial_tools=['weather.day'])
        try:
            session.before_input_unit(0)
            session.submit_private(canonical({'op':'call','call_id':'c2','tool':'tools.discover','query':'美元汇率','schema_version':99}))
            self.assertEqual(session.visible,{'weather.day'})
            self.assertTrue(any(e['type']=='protocol_compatibility' for e in session.events))
            self.assertTrue(session.before_input_unit(1))
            self.assertIn('weather.day',session.visible)
        finally:session.close()

    def test_incomplete_business_calls_are_rejected(self):
        for module in [sys.modules['tool_runtime']]:
            session=module.ToolSession('comparison',discovery_mode='function',initial_tools=['weather.day'])
            try:
                session.before_input_unit(0)
                for action in [{'op':'call','call_id':'c2','tool':'weather.day','schema_version':'2'},
                               {'op':'call','call_id':'c2','arguments':{'city':'上海'},'schema_version':'2'}]:
                    with self.assertRaises(module.ToolError) as error:session.submit_private(canonical(action))
                    self.assertEqual(error.exception.code,'action_fields')
            finally:session.close()

    def test_conflicting_flat_and_nested_fields_are_not_guessed(self):
        session=ToolSession('conflict',discovery_mode='function')
        try:
            session.before_input_unit(0)
            with self.assertRaises(ToolError):session.submit_private(canonical({'op':'call','call_id':'d1','tool':'tools.discover','query':'weather','arguments':{'query':'stocks'}}))
        finally:session.close()


if __name__=='__main__':unittest.main()
