import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec=importlib.util.spec_from_file_location('harness_worker',Path(__file__).resolve().parents[1]/'backend/harness/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


class FormatRecoveryTests(unittest.TestCase):
    def harness(self,replies):
        class Harness:
            def __init__(self):self.calls=[]
            def run(self,prompt,**kwargs):
                self.calls.append((prompt,kwargs['session_id']))
                return SimpleNamespace(final_response=replies[len(self.calls)-1],finish_reason='completed')
        return Harness()

    def test_format_retry_preserves_session_and_never_uses_prose_as_done(self):
        h=self.harness(['北京大学创办于1898年。','{"status":"done","answer":"北京大学创办于1898年。","missing_information":[]}'])
        with patch.object(worker,'emit')as events:
            value,_=worker.run_validated(h,'查创办年份',lambda e:None,{})
        self.assertEqual(value['answer'],'北京大学创办于1898年。')
        self.assertEqual([x[1]for x in h.calls],['delegated-task']*2)
        self.assertEqual(events.call_args.args[0]['event']['type'],'handoff/format_retry')

    def test_two_invalid_outputs_fail_closed(self):
        h=self.harness(['Done','Still done'])
        with patch.object(worker,'emit'),self.assertRaises(worker.HandoffFailure)as error:
            worker.run_validated(h,'task',lambda e:None,{})
        self.assertEqual(error.exception.code,'BackendFormatError');self.assertEqual(len(h.calls),2)

    def test_valid_error_is_not_promoted_to_success(self):
        h=self.harness(['{"status":"error","answer":"网页未能读取。","missing_information":[]}'])
        value,_=worker.run_validated(h,'task',lambda e:None,{})
        self.assertEqual(value['status'],'error');self.assertEqual(len(h.calls),1)

    def test_spoken_answer_keeps_values_and_citation_label_without_raw_url(self):
        value=worker.spoken_answer('创办于1898年；时间18:12，汇率6.6684。来源：[学校介绍](https://news.pku.edu.cn/info/5111/779361.htm)。')
        self.assertEqual(value,'创办于1898年；时间18:12，汇率6.6684。来源：学校介绍。')
        self.assertNotIn('https',worker.spoken_answer('介绍（https://example.org/page）。'))

    def test_url_only_output_is_not_a_valid_success_answer(self):
        h=self.harness(['{"status":"done","answer":"https://example.org/","missing_information":[]}',
            '{"status":"error","answer":"未能获得可回答的事实。","missing_information":[]}'])
        with patch.object(worker,'emit'):value,_=worker.run_validated(h,'task',lambda e:None,{})
        self.assertEqual(value['status'],'error');self.assertEqual(len(h.calls),2)

    def test_output_limit_does_not_start_format_retry(self):
        h=self.harness(['truncated'])
        old=h.run
        def run(prompt,**kwargs):
            result=old(prompt,**kwargs);ending['kind']='max-tokens';return result
        h.run=run;ending={}
        with self.assertRaises(worker.HandoffFailure)as error:worker.run_validated(h,'task',lambda e:None,ending)
        self.assertEqual(error.exception.code,'BackendOutputBudget');self.assertEqual(len(h.calls),1)
