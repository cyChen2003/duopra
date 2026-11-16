"""Credential-free runtime checks for the isolated background assistant."""
from functools import lru_cache
import json
import os
import subprocess
import sys


@lru_cache(maxsize=8)
def _check(python, dependencies):
    program = '''import sys,json
if sys.argv[1]: sys.path.insert(0,sys.argv[1])
from deepseek_harness import DeepSeekHarness
from deepseek_harness_runtime import bundled_runtime_path
import importlib.metadata as metadata
p=bundled_runtime_path()
assert p.is_file()
print(json.dumps({'ready':True,'sdk_version':metadata.version('deepseek-harness-sdk')}))
'''
    try:
        command = [python, '-S'] if dependencies else [python]
        result = subprocess.run(command + ['-c', program, dependencies],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            value = json.loads(result.stdout)
            if value['sdk_version'] == '0.2.1rc2':
                return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {'ready': False, 'code': 'BackendRuntimeUnavailable',
            'message': '后台运行环境不可用，请检查 SDK 和匹配的执行程序。'}


def runtime_status():
    return _check(os.environ.get('DUPLEX_HARNESS_PYTHON', sys.executable),
                  os.environ.get('DUPLEX_HARNESS_DEPS', ''))
