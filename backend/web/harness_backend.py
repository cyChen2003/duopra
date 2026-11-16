"""Browser-scoped official DeepSeek Harness, isolated from native speech state."""
import collections
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

NAME = 'assistant.query'
DEFAULT_MODEL = 'deepseek-v4-flash'
COOKIE = 'duplex_harness_owner'
SCHEMA = {
    'name': NAME, 'version': '1', 'read_only': True, 'runtime_role': 'meta',
    'description': ('Delegate the complete already-heard task to the configured background assistant when '
        'the visible direct tools cannot complete it. It can read local reference documents, search records, '
        'calculate amounts and convert timezones or units. The only argument is task, one natural-language '
        'string containing every active goal, supplied value and dependency. Do not split business fields '
        'into arguments. Ask the user first if an essential fact is missing. '
        '后台委派：task中写全已听到的需求、参数、改口和结果依赖；仅有task这一个参数。'),
    'excluded': 'No shell commands, arbitrary files, write operations, or private tool names are exposed to the foreground.',
    'parameters': {'type': 'object', 'properties': {'task': {'type': 'string', 'minLength': 1,
        'maxLength': 1600, 'multiline': True, 'x-duplex-complete-task': True}},
        'required': ['task'], 'additionalProperties': False},
    'returns': {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['done', 'need_info', 'error']},
        'answer': {'type': 'string', 'multiline': True},
        'missing_information': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['status', 'answer', 'missing_information'], 'additionalProperties': False},
}
TOOLS = ['workspace_list', 'workspace_search', 'workspace_read', 'math_arithmetic', 'time_convert', 'unit_convert']


def validated_config(value, previous=None):
    if not isinstance(value, dict) or set(value) - {'enabled', 'protocol', 'base_url', 'api_key', 'model', 'deadline_seconds', 'clear_key'}:
        raise ValueError('Unsupported backend configuration fields')
    old = previous or {}
    enabled = value.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError('enabled must be boolean')
    raw_base=value.get('base_url', '')
    if not isinstance(raw_base,str) or any(ord(c)<32 for c in raw_base):
        raise ValueError('Invalid Base URL')
    base = raw_base.strip().rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or any(ord(c) < 33 for c in base)):
        raise ValueError('Use an HTTP(S) Base URL without embedded credentials, query or fragment')
    from security import allowed_backend
    allowed_backend(base)
    protocol = value.get('protocol', 'deepseek')
    if protocol not in ('deepseek', 'openai-compatible'):
        raise ValueError('Unsupported model API protocol')
    # Official Harness uses DeepSeek's Messages protocol. Its public endpoint
    # is /anthropic, whereas OpenAI-compatible endpoints retain their own path.
    if protocol == 'deepseek' and parsed.hostname == 'api.deepseek.com' and not parsed.path.strip('/'):
        base += '/anthropic'
    model = value.get('model', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', model):
        raise ValueError('Enter the exact API model name')
    key = value.get('api_key', '')
    if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 for c in key):
        raise ValueError('Invalid API key')
    previous_url=urlsplit(old.get('base_url', ''))
    if not key and not value.get('clear_key') and (previous_url.scheme, previous_url.netloc) == (parsed.scheme, parsed.netloc):
        key = old.get('api_key', '')
    deadline = value.get('deadline_seconds', 180)
    if type(deadline) is not int or not 30 <= deadline <= 600:
        raise ValueError('Backend deadline must be 30–600 seconds')
    return dict(enabled=enabled, protocol=protocol, base_url=base, api_key=key,
                model=model, deadline_seconds=deadline)


def public_config(value):
    if not value:
        return dict(configured=False, enabled=True, protocol='deepseek',
                    base_url='https://api.deepseek.com/anthropic', model=DEFAULT_MODEL, has_key=False)
    return {k:v for k,v in value.items() if k != 'api_key'} | dict(
        configured=True, has_key=bool(value.get('api_key')))


class BackendStore:
    def __init__(self):
        self.owners = {}; self.lock = threading.RLock()

    def identity(self, cookie):
        with self.lock:
            now = time.time()
            self.owners = {k:v for k,v in self.owners.items() if now-v['touched'] < 12*3600}
            if cookie not in self.owners:
                if len(self.owners) >= 64:
                    empty = [k for k,v in self.owners.items() if v['config'] is None]
                    if empty:
                        self.owners.pop(min(empty, key=lambda k:self.owners[k]['touched']))
                    else:
                        raise ValueError('Too many active backend configuration owners')
                cookie = secrets.token_hex(24)
                self.owners[cookie] = dict(config=None, events=collections.deque(maxlen=256), seq=0, touched=now)
            self.owners[cookie]['touched'] = now
            return cookie

    def existing(self, cookie):
        """Passive polling must never allocate an owner or a credential slot."""
        with self.lock:
            owner = self.owners.get(cookie)
            if owner is None:
                return None
            if time.time()-owner['touched'] >= 12*3600:
                self.owners.pop(cookie, None)
                return None
            owner['touched'] = time.time()
            return cookie

    def get(self, cookie):
        with self.lock:
            if self.existing(cookie) is None:
                return None
            return copy.deepcopy(self.owners.get(cookie, {}).get('config'))

    def save(self, cookie, value):
        with self.lock:
            self.owners[cookie]['config'] = validated_config(value, self.get(cookie))
            return public_config(self.get(cookie))

    def event(self, cookie, value):
        with self.lock:
            owner = self.owners.get(cookie)
            if owner is None:
                return
            owner['seq'] += 1
            owner['events'].append(dict(value, sequence=owner['seq'], at_ms=time.time_ns()//1000000))

    def events(self, cookie, after=0):
        with self.lock:
            return copy.deepcopy([e for e in self.owners.get(cookie, {}).get('events', []) if e['sequence'] > after])


class HarnessExecutor:
    def __init__(self, original, store, cookie, *, origin='model', generation=None):
        self.original=original; self.mode=original.mode; self.store=store; self.cookie=cookie
        self.origin=origin; self.generation=generation or (lambda:0)
        self.jobs={}; self.lock=threading.Lock(); self.closed=False

    def event(self, task_id, kind, **data):
        self.store.event(self.cookie, dict(type=kind, task_id=task_id, origin=self.origin, **data))

    def execute(self, name, arguments, **kwargs):
        if name != NAME:
            return self.original.execute(name, arguments, **kwargs)
        from tool_runtime import validate
        validate(arguments, SCHEMA['parameters'])
        cfg=self.store.get(self.cookie)
        if not cfg or not cfg['enabled']:
            raise ValueError('Background assistant is not configured for this browser')
        if not cfg.get('api_key'):
            raise ValueError('请先保存后台 API Key，再执行测试。')
        from harness_runtime import runtime_status
        if not runtime_status()['ready']:
            raise ValueError('后台运行环境不可用：SDK 或执行程序缺失，请联系部署者。')
        task_id=secrets.token_hex(12); begun=time.monotonic(); generation=self.generation()
        self.event(task_id, 'backend_started', task=arguments['task'], model=cfg['model'],
                   harness='official_deepseek_harness', generation=generation)
        worker=Path(__file__).resolve().parent.parent/'harness/worker.py'
        python=os.environ.get('DUPLEX_HARNESS_PYTHON', sys.executable)
        command=[python, '-S', '-u', str(worker)] if os.environ.get('DUPLEX_HARNESS_DEPS') else [python, '-u', str(worker)]
        from security import worker_environment
        process=subprocess.Popen(command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env=worker_environment(),
            start_new_session=True)
        with self.lock:
            self.jobs[task_id]=(process, generation)
        def terminate():
            if process.poll() is None:
                try:os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:pass
        timer=threading.Timer(cfg['deadline_seconds'], terminate); timer.daemon=True; timer.start()
        result=None; failure=None; calls=[]
        try:
            process.stdin.write(json.dumps(dict(task=arguments['task'], config=cfg), ensure_ascii=False)+'\n')
            process.stdin.close()
            for line in process.stdout:
                if cfg.get('api_key'):
                    line=line.replace(cfg['api_key'], '[REDACTED]')
                value=json.loads(line)
                if value['type']=='harness_event':
                    event=value['event']; self.event(task_id, 'backend_trace', event=event)
                    if event.get('type')=='tool/call':calls.append(event['data'])
                elif value['type']=='harness_result':result=value['result']
                elif value['type']=='harness_error':failure=value['code']
            process.wait(timeout=5)
            if result is None:
                result=dict(status='error', answer='后台未完成本次任务，请检查API配置或执行轨迹。', missing_information=[])
            elapsed=time.monotonic()-begun
            self.event(task_id, 'backend_finished', result=result, seconds=elapsed,
                       error_code=failure, tools_called=len(calls), generation=generation)
            return dict(source='deepseek_harness', is_fixture=False, result=result,
                elapsed_ms=round(elapsed*1000), provenance=dict(task_id=task_id, origin=self.origin,
                    harness='official_deepseek_harness', sdk_version='0.2.1rc2', model=cfg['model'],
                    reference_workspace='local_demo_reference', read_only=True, tools_called=calls))
        finally:
            timer.cancel(); terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
            with self.lock:self.jobs.pop(task_id, None)

    def sync_generation(self, current):
        with self.lock:
            stale=[p for p,g in self.jobs.values() if g != current]
        for process in stale:
            if process.poll() is None:
                try:os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:pass

    def close(self):
        self.closed=True; self.sync_generation(None)


def session_catalog(base, store, cookie):
    value=store.get(cookie)
    if not value or not value.get('enabled'):
        return base, []
    catalog=copy.copy(base); catalog.tools=copy.deepcopy(base.tools)
    catalog.tools[NAME]=dict(copy.deepcopy(SCHEMA), domain='assistant', split='runtime',
        source='deepseek_harness', title='后台助手', fixture={})
    catalog.sha256=hashlib.sha256(json.dumps(catalog.tools, sort_keys=True).encode()).hexdigest()
    return catalog, [copy.deepcopy(SCHEMA)]


def install_routes(app, owner, catalog, *, cookie_name=COOKIE):
    from fastapi import Request, HTTPException
    from fastapi.responses import JSONResponse
    from tool_runtime import Executor
    import asyncio
    store=BackendStore()
    def identity(request, create=False):
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'Origin rejected')
        cookie=request.cookies.get(cookie_name)
        if not create:
            return store.existing(cookie)
        try:return store.identity(cookie)
        except ValueError:raise HTTPException(429, 'Too many configured browsers; retry after an existing configuration expires')
    @app.get('/api/harness/config')
    async def configuration(request:Request):
        cookie=identity(request, create=True)
        value=public_config(store.get(cookie))
        from harness_runtime import runtime_status
        from personal_client import connection_mode
        runtime=await asyncio.to_thread(runtime_status)
        response=JSONResponse(dict(value, engine='official_deepseek_harness', sdk_version='0.2.1rc2',
            runtime=runtime, default_model=DEFAULT_MODEL, business_tools=catalog.cards(),
            personal_available=connection_mode()!='disabled',
            web_tools=['web_search','web_fetch'] if value['protocol']=='deepseek' else [],
            tools=TOOLS, return_protocol=dict(status=['done','need_info','error'], answer='string', missing_information=['string']),
            secret_storage='server_memory_per_browser_12h', original_session_unchanged=True))
        response.set_cookie(cookie_name, cookie, httponly=True, samesite='strict', max_age=12*3600,
                            secure=urlsplit(request.headers.get('origin','')).scheme=='https')
        response.headers['Cache-Control']='no-store'; return response
    @app.post('/api/harness/config')
    async def configure(request:Request):
        cookie=identity(request)
        if cookie is None:raise HTTPException(409, 'Reopen backend settings before saving')
        if request.headers.get('x-duplex-harness-request') != '1':raise HTTPException(403, 'Explicit configuration required')
        if owner.active is not None:raise HTTPException(409, 'End the current voice session before changing its backend')
        try:value=store.save(cookie, await request.json())
        except (ValueError,TypeError,AttributeError):raise HTTPException(400, 'Invalid Base URL, model, key, protocol or deadline')
        return dict(value, engine='official_deepseek_harness', secret_returned=False)
    @app.get('/api/harness/events')
    async def events(request:Request, after:int=0):
        cookie=identity(request); return dict(events=store.events(cookie, after), manual_is_not_student_success=True)
    @app.post('/api/harness/test')
    async def test(request:Request):
        cookie=identity(request)
        if cookie is None:raise HTTPException(409, 'Open backend settings before testing')
        if request.headers.get('x-duplex-harness-request') != '1':raise HTTPException(403, 'Explicit manual test required')
        body=await request.json(); task=body.get('task')
        if not isinstance(task, str) or not 1 <= len(task) <= 1600:raise HTTPException(400, 'Enter a bounded task')
        executor=HarnessExecutor(Executor(catalog), store, cookie, origin='manual')
        try:result=await asyncio.to_thread(executor.execute, NAME, {'task':task})
        except ValueError as error:raise HTTPException(400, str(error)) from None
        finally:executor.close()
        return dict(origin='manual', injected_into_model=False, payload=result)
    return store
