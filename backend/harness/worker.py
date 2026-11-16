"""One official DeepSeek Harness SDK run, JSONL stdio, no GPU imports."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit


PROTOCOL = (
    'You are the background task assistant for a voice agent. The supplied task is '
    'the complete task already heard by the foreground. Preserve its goals and supplied '
    'values, use actual tool observations, and ask only for essential missing facts. '
    'Use web_search and web_fetch for public research; prefer original and official sources. '
    'Use the frontend business tools for supported live queries. Perform ALL independent goals, '
    'and follow result-dependent steps only after their prerequisites return. Never silently '
    'drop a goal. If some goals need information, complete independent ones and state what remains. '
    'Do not search local demonstration documents for general public questions. '
    'Sources are shown separately in the execution trace; answer must be plain spoken text '
    'without Markdown links, raw URLs, headings or code. '
    'For ordinary independent lookups, use one short spoken sentence per goal. '
    'Do not add OHLC, previous-close or historical statistics unless requested. '
    'If task asks only to confirm supplied facts, answer briefly after actual verification. '
    'If it also asks to report several results, include the primary fact for each goal; '
    'a bare confirmation must not replace the requested results. '
    'Use the available tools when needed; local reference documents are demonstration '
    'data, not live external records. Never claim a task completed without evidence. '
    'Your final response MUST be one JSON object with exactly status, answer, '
    'missing_information. status is done, need_info, or error. answer is a concise '
    'natural spoken answer in the task language. missing_information is an array of '
    'strings, nonempty only when user information is missing. Do not output code or '
    'private tool instructions in answer. Do not wrap the final JSON in markdown. '
    'Any task instruction such as one sentence or no formatting applies only to '
    'answer, never to the mandatory outer JSON transport object.'
)
CONTRACT = json.loads((Path(__file__).parent/'reply_contract.json').read_text())
PROTOCOL += '\n\n'+CONTRACT['backend_policy']
TASK_LANGUAGE = 'zh'


class HandoffFailure(Exception):
    def __init__(self, code):
        self.code = code


def validate_result(value):
    if (not isinstance(value, dict) or set(value) != {'status', 'answer', 'missing_information'}
            or value['status'] not in ('done', 'need_info', 'error')
            or not isinstance(value['answer'], str) or not value['answer'].strip()
            or not isinstance(value['missing_information'], list)
            or any(not isinstance(x, str) or not x.strip() for x in value['missing_information'])
            or (value['status'] == 'need_info') != bool(value['missing_information'])):
        raise HandoffFailure('BackendFormatError')
    return value


def spoken_answer(text):
    """Keep citation labels, leave clickable source URLs in the tool trace."""
    text = re.sub(r'\[([^\]\n]+)\]\(https?://[^)\s]+\)', r'\1', text)
    text = re.sub(r'https?://[^\s<>\]\[）)。,；！？、]+', '', text)
    text = re.sub(r'\(\s*\)|（\s*）', '', text)
    return text.strip()


def failure_result(code, language):
    messages = {
        'BackendOutputBudget': ('后台输出达到上限，本次任务未能完整完成。', 'The background response reached its output limit; the task is incomplete.'),
        'BackendTransportError': ('后台服务暂时无法连接，本次任务未完成。', 'The background service could not be reached; the task is incomplete.'),
        'BackendFormatError': ('后台回执格式未通过校验，本次结果尚未确认完成。', 'The background response did not pass format validation; completion is unconfirmed.'),
        'BackendExecutionError': ('后台执行失败，本次任务尚未完成。', 'Background execution failed; the task is incomplete.'),
        'BackendRuntimeUnavailable': ('后台运行环境不可用，请检查 SDK 和执行程序。', 'The background runtime is unavailable; check SDK and executable installation.'),
    }
    pair = messages.get(code, messages['BackendExecutionError'])
    return dict(status='error', answer=pair[0 if language == 'zh' else 1], missing_information=[])


def run_validated(harness, task, notify, ending):
    """One bounded same-session format correction; never coerce prose into done."""
    prompt = task
    for attempt in range(2):
        ending.clear()
        result = harness.run(prompt, session_id='delegated-task', on_notification=notify)
        if ending.get('kind') == 'max-tokens':
            raise HandoffFailure('BackendOutputBudget')
        if ending.get('kind') == 'error':
            raise HandoffFailure('BackendTransportError' if ending.get('error', {}).get('code') == 'TRANSPORT' else 'BackendExecutionError')
        text = result.final_response.strip()
        if text.startswith('```json\n') and text.endswith('\n```'):
            text = text[8:-4]
        try:
            value = validate_result(json.loads(text))
            value = dict(value, answer=spoken_answer(value['answer']))
            return validate_result(value), result
        except (json.JSONDecodeError, HandoffFailure):
            if attempt:
                raise HandoffFailure('BackendFormatError') from None
            emit({'type': 'harness_event', 'event': {'type': 'handoff/format_retry',
                  'data': {'attempt': 1, 'original_response_rejected': True}}})
            prompt = (
                'Format correction only for the SAME task and observations already in this session. '
                'Your previous final reply failed the transport schema. Return only one JSON object '
                'with exactly status, answer, missing_information. status must be done, need_info or '
                'error; answer is a nonempty spoken string; missing_information is [] except for '
                'genuinely missing user information. Preserve all supplied goals and actual facts. '
                'Do not invent evidence, add facts, repeat tool calls or make new requests. If the '
                'observations are insufficient, honestly set error and state what remains incomplete. '
                'A request for one sentence constrains answer only. No markdown or text outside JSON.'
            )


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    global TASK_LANGUAGE
    request = json.loads(sys.stdin.readline())
    TASK_LANGUAGE = 'zh' if any('\u4e00' <= c <= '\u9fff' for c in request.get('task', '')) else 'en'
    secret = request['config'].get('api_key', '')
    dependencies = os.environ.get('DUPLEX_HARNESS_DEPS')
    if dependencies:
        sys.path.insert(0, dependencies)
    try:
        from deepseek_harness import DeepSeekHarness
        from deepseek_harness_runtime import bundled_runtime_path
        bundled_runtime_path()
    except (ImportError, OSError):
        raise HandoffFailure('BackendRuntimeUnavailable') from None
    cfg = request['config']
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
    from security import allowed_backend
    from temporal_context import clock_payload, serialize
    allowed_backend(cfg['base_url'])
    system_prompt = PROTOCOL + '\n\n' + serialize(clock_payload())
    bundle = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='duplex-harness-') as temporary:
        root = Path(temporary); workspace = root/'workspace'; workspace.mkdir()
        for source in (bundle/'workspace').iterdir():
            if source.is_file():
                (workspace/source.name).write_bytes(source.read_bytes())
        patch = [
            {'id': 'persistent-bash', 'disabled': True},
            {'id': 'persistent-pwsh', 'disabled': True},
            {'id': 'tool-fs', 'disabled': True},
            {'id': 'tool-working-directory', 'disabled': True},
            {'id': 'system-prompt', 'config': {'includeHarnessIdentity': False,
                'includeRuntimeContext': False, 'personaPrefix': system_prompt}},
            {'insert': [{'id': 'duplex-readonly-tools', 'name': str(bundle/'readonly_tools.mjs'),
                         'config': {'workspaceRoot': str(workspace)}},
                        {'id': 'duplex-frontend-tools', 'name': str(bundle/'frontend_tools.mjs'),
                         'config': {'bundleRoot': str(bundle), 'python': sys.executable,
                                    'customTools': os.environ.get('DUPLEX_CUSTOM_TOOLS') or str(bundle.parent/'tools'/'custom'),
                                    'personalContext': request.get('personal_context')}}]},
        ]
        # Search/fetch are official Harness plugins. They use the configured
        # DeepSeek origin, so an approved task relay also carries web calls.
        # Custom OpenAI endpoints do not inherit a DeepSeek credential.
        if cfg.get('protocol') == 'deepseek':
            parsed = urlsplit(cfg['base_url'])
            web_base = urlunsplit((parsed.scheme, parsed.netloc, '/v1/tools', '', ''))
            patch.append({'insert': [
                {'id': 'web', 'name': '@deepseek-ai/dsh-web', 'config': {
                    'searchProvider': 'deepseek-search-api', 'fetchProvider': 'deepseek-fetch-api'}},
                {'id': 'web-deepseek-api', 'name': '@deepseek-ai/dsh-web-deepseek-api', 'config': {
                    'baseURL': web_base, 'apiKeyEnv': 'DUPLEX_HARNESS_API_KEY',
                    'count': 5, 'searchRequestsPerSecond': 3}},
                {'id': 'tool-web', 'name': '@deepseek-ai/dsh-tool-web', 'config': {
                    'searchMaxResults': 5, 'searchMaxQueries': 4, 'fetchMaxOutputChars': 18000,
                    'fetchTimeoutMs': 45000, 'searchTimeoutMs': 45000}},
            ]})
        provider = 'deepseek-official'
        if cfg.get('protocol') == 'openai-compatible':
            provider = 'duplex-custom'
            patch += [{'id': 'llm-deepseek', 'disabled': True}, {'insert': [
                {'id': 'duplex-openai-provider', 'name': '@deepseek-ai/dsh-llm-pi-ai',
                 'config': {'providers': {provider: {'api': 'openai-completions',
                    'baseURL': cfg['base_url'], 'apiKeyEnv': 'DUPLEX_HARNESS_API_KEY',
                    'models': [{'id': cfg['model'], 'contextWindow': 32768,
                                'maxTokens': 8192, 'input': ['text']}]}}}}]}]
        patchfile = root/'patch.json'; patchfile.write_text(json.dumps(patch))
        ending = {}
        def notify(value):
            payload = value.payload
            event = payload.get('event', payload)
            kind = event.get('type', event.get('kind', ''))
            if kind == 'turn/end':
                ending.update(event.get('data', {}).get('reason', {}))
            if kind.startswith('tool/') or kind in ('turn/end', 'turn/start'):
                cleaned = json.dumps(event, ensure_ascii=False).replace(secret, '[REDACTED]') if secret else json.dumps(event, ensure_ascii=False)
                emit({'type': 'harness_event', 'event': json.loads(cleaned)})
        with DeepSeekHarness(
            profile='sdk-minimal', provider=provider, model=cfg['model'],
            base_url=cfg['base_url'], api_key=secret, max_tokens=8192,
            cwd=str(workspace), runtime_cwd=str(workspace), dsh_home=str(root/'home'),
            patches=(str(patchfile),), initialize_timeout_seconds=60,
            env={'DUPLEX_HARNESS_API_KEY': secret, 'DSH_SYSTEM_PROMPT': system_prompt},
        ) as harness:
            if request.get('runtime_check') is True:
                emit({'type': 'harness_runtime_ready', 'ready': True,
                      'sdk_version': '0.2.1rc2', 'model_api_called': False})
                return
            value, result = run_validated(harness, request['task'], notify, ending)
        emit({'type': 'harness_result', 'result': value, 'finish_reason': result.finish_reason,
              'engine': 'official_deepseek_harness', 'sdk_version': '0.2.1rc2', 'reply_contract': CONTRACT['version']})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Provider errors can contain credential-bearing request diagnostics.
        # Never forward their raw exception string to the browser or logs.
        code = error.code if isinstance(error, HandoffFailure) else 'BackendExecutionError'
        emit({'type': 'harness_error', 'code': code,
              'message': 'Backend execution or output protocol failed; check API configuration and tool trace.'})
        emit({'type': 'harness_result', 'result': failure_result(code, TASK_LANGUAGE),
              'finish_reason': 'failed', 'engine': 'official_deepseek_harness',
              'sdk_version': '0.2.1rc2', 'reply_contract': CONTRACT['version']})
        sys.exit(1)
