"""Model identity helpers and shared serving settings."""
import hashlib
import json
from pathlib import Path


class ReleaseError(ValueError):
    pass


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            value.update(block)
    return value.hexdigest()


def serving_config(release, model_root, profile_path):
    profile = json.loads(Path(profile_path).read_text())
    from cuda_runtime import validate_profile
    validate_profile(profile)
    return {
        'runtime_backend': 'nvidia-cuda',
        'release': release,
        'model': str(Path(model_root).resolve()),
        'model_variant': 'Duopra',
        'thinker_dtype': 'float32', 'speech_dtype': 'bfloat16',
        'system_prompt': 'Streaming Omni Conversation.', 'sliding_window_mode': 'off',
        'scoring_profile': 'llm_math_canonical', 'codec_final_maximum': 156,
        'nvidia_profile': profile,
        'tool_discovery': {'mode': 'function'}, 'session_clock': {'enabled': True, 'timezone': 'Asia/Shanghai'},
    }
