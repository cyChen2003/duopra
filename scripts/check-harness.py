#!/usr/bin/env python3
"""Check the installed Harness runtime and a local, scripted model/tool round trip.

No API credentials or external model are used. The protocol fixture requests real
math and business adapters; this is an integration check, not model evaluation.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/harness')]
from security import worker_environment
from worker import validate_result


def runtime_check(python, dependencies):
    env = worker_environment()
    if dependencies:
        env['DUPLEX_HARNESS_DEPS'] = dependencies
    request = dict(task='runtime check', runtime_check=True,
                   config=dict(protocol='deepseek', base_url='https://api.deepseek.com/anthropic',
                               api_key='', model='deepseek-v4-flash'))
    command = [python] + (['-S'] if dependencies else []) + [str(ROOT/'backend/harness/worker.py')]
    result = subprocess.run(command, input=json.dumps(request)+'\n', env=env,
                            text=True, capture_output=True, timeout=90)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if result.returncode or not any(e.get('type') == 'harness_runtime_ready' for e in events):
        raise RuntimeError('Harness runtime/plugin initialization failed: '+result.stdout)


class ModelFixture(BaseHTTPRequestHandler):
    responses = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        tools = [m for m in request['messages'] if m['role'] == 'tool']
        if tools:
            self.responses[:] = [json.loads(m['content']) for m in tools]
            delta = {'content': json.dumps(dict(status='done', answer='The product is 110.4; London is UTC+0.', missing_information=[]))}
            finish = 'stop'
        else:
            delta = {'role': 'assistant', 'tool_calls': [
                {'index': 0, 'id': 'fixture_math', 'type': 'function', 'function': {
                    'name': 'math_arithmetic', 'arguments': json.dumps(dict(a=138, b=0.8, operation='multiply'))}},
                {'index': 1, 'id': 'fixture_timezone', 'type': 'function', 'function': {
                    'name': 'front__timezone__offset', 'arguments': json.dumps(dict(city='London', instant='2026-11-11T12:00Z'))}},
            ]}
            finish = 'tool_calls'
        chunks = [dict(id='fixture', object='chat.completion.chunk', created=0, model='fixture',
                       choices=[dict(index=0, delta=delta, finish_reason=None)]),
                  dict(id='fixture', object='chat.completion.chunk', created=0, model='fixture',
                       choices=[dict(index=0, delta={}, finish_reason=finish)])]
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def tool_roundtrip():
    from deepseek_harness import DeepSeekHarness
    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='duopra-harness-check-') as temporary:
            folder = Path(temporary)
            bundle = ROOT/'backend/harness'
            patch = [
                {'id': name, 'disabled': True} for name in
                ('persistent-bash', 'persistent-pwsh', 'tool-fs', 'tool-working-directory', 'llm-deepseek')
            ] + [{'insert': [
                {'id': 'check-readonly', 'name': str(bundle/'readonly_tools.mjs'),
                 'config': {'workspaceRoot': temporary}},
                {'id': 'check-business', 'name': str(bundle/'frontend_tools.mjs'),
                 'config': {'bundleRoot': str(bundle), 'python': sys.executable}},
                {'id': 'check-provider', 'name': '@deepseek-ai/dsh-llm-pi-ai',
                 'config': {'providers': {'local-fixture': {'api': 'openai-completions',
                     'baseURL': f'http://127.0.0.1:{server.server_port}/v1',
                     'apiKeyEnv': 'DUPLEX_FIXTURE_KEY', 'models': [{'id': 'fixture',
                         'contextWindow': 32768, 'maxTokens': 1024, 'input': ['text']}]}}}},
            ]}]
            patchfile = folder/'patch.json'
            patchfile.write_text(json.dumps(patch))
            with DeepSeekHarness(profile='sdk-minimal', provider='local-fixture', model='fixture',
                                 cwd=temporary, runtime_cwd=temporary, dsh_home=str(folder/'home'),
                                 patches=(str(patchfile),), initialize_timeout_seconds=60,
                                 request_timeout_seconds=30, env={'DUPLEX_FIXTURE_KEY': 'fixture-only'}) as harness:
                result = harness.run('Run the scripted integration check.', session_id='fixture-check')
            value = validate_result(json.loads(result.final_response))
            assert value['status'] == 'done', value
            assert len(ModelFixture.responses) == 2, ModelFixture.responses
            assert any(abs(x.get('value', 0)-110.4) < 1e-9 for x in ModelFixture.responses), ModelFixture.responses
            assert any(x.get('result', {}).get('offset_minutes') == 0 and x.get('is_fixture') is False
                       for x in ModelFixture.responses), ModelFixture.responses
            return [e['data'] for e in result.events if e.get('type') == 'tool/call']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-only', action='store_true')
    args = parser.parse_args()
    default_dependencies = Path(sys.prefix)/'harness-deps'
    dependencies = os.environ.get('DUPLEX_HARNESS_DEPS', str(default_dependencies) if default_dependencies.is_dir() else '')
    runtime_check(os.environ.get('DUPLEX_HARNESS_PYTHON', sys.executable), dependencies)
    if dependencies:
        sys.path.insert(0, dependencies)
    calls = [] if args.runtime_only else tool_roundtrip()
    print(json.dumps(dict(runtime_ready=True, sdk_version='0.2.1rc2',
                         model_provider='not_called' if args.runtime_only else 'local_scripted_fixture',
                         external_api_verified=False, tool_calls=len(calls)), indent=2))


if __name__ == '__main__':
    main()
