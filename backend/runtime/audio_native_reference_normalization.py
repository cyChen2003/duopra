"""Optional instance-local restoration of native reference-audio normalization.

The streaming mel extractor changes its shared feature extractor's normalization
mode at 5 seconds. Native reset_streaming() only resets PCM state. A following
reference-audio prefill can otherwise inherit the previous session's mode.

This adapter restores the qualified native constructor's original settings at
the beginning of duplex.prepare(). Streaming extraction is left unchanged and
will still select fixed/dynamic normalization according to its native window.
Importing this module does not import torch. Disabled unless explicitly enabled.
"""
import functools
import hashlib
import inspect
from pathlib import Path
import threading


STATE_KEY = '_duplex_audio_reference_normalization'
QUALIFIED_MODEL_SHA256 = 'b7b4070cead9b88347f6e39d8333706ec007e2a832d6bcde5755063bf1453774'
QUALIFIED_PROCESSOR_SHA256 = '6d2062aebb27eef6b88dd513636b83be814bf7730800aaf716244c26024a71fc'
FIELDS = ('dynamic_log_norm', 'dynamic_range_db', 'log_floor_db')


def _file_sha(function):
    path = Path(inspect.getsourcefile(inspect.unwrap(function)))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(extractor):
    return {name: getattr(extractor, name) for name in FIELDS}


def _restore(extractor, values):
    for name in FIELDS:
        setattr(extractor, name, values[name])


def status(duplex):
    state = getattr(duplex, STATE_KEY, None)
    if state is None:
        return {'enabled': False, 'optimization': 'reference_audio_normalization_reset'}
    return dict(state['proof'], calls=dict(state['calls']))


def rollback(duplex):
    state = getattr(duplex, STATE_KEY, None)
    if state is None:
        return {'restored': False}
    if duplex.prepare is not state['installed']:
        raise RuntimeError('Reference normalization prepare ownership changed')
    if duplex.decoder.get_cache_length():
        raise RuntimeError('Reset the decoder before removing reference normalization adapter')
    if state['had_local']:
        duplex.prepare = state['previous_local']
    else:
        delattr(duplex, 'prepare')
    delattr(duplex, STATE_KEY)
    return {'restored': True, 'previous': state['proof']}


def install(model, duplex, config=None):
    options = dict(config or {})
    if set(options) - {'enabled'}:
        raise ValueError('Unknown reference normalization adapter options')
    enabled = options.get('enabled', False)
    if type(enabled) is not bool:
        raise TypeError('Reference normalization enabled must be boolean')
    if not enabled:
        return status(duplex)
    if hasattr(duplex, STATE_KEY):
        return status(duplex)
    if getattr(model, 'training', False):
        raise RuntimeError('Reference normalization adapter is inference-only')
    if duplex.model is not model or duplex.processor is not model.processor:
        raise ValueError('Native duplex and model must share the same processor')
    if duplex.decoder.get_cache_length():
        raise RuntimeError('Install reference normalization adapter before session preparation')
    original = duplex.prepare
    extractor = model.processor.audio_processor
    if _file_sha(original) != QUALIFIED_MODEL_SHA256:
        raise RuntimeError('Unqualified native duplex prepare source')
    if (type(extractor).__name__ != 'MiniCPMAAudioProcessor'
            or _file_sha(type(extractor).__init__) != QUALIFIED_PROCESSOR_SHA256):
        raise RuntimeError('Unqualified native audio feature extractor source')
    defaults = inspect.signature(type(extractor).__init__).parameters
    canonical = {name: defaults[name].default for name in FIELDS}
    if canonical != {'dynamic_log_norm': True, 'dynamic_range_db': 8.0, 'log_floor_db': -10.0}:
        raise RuntimeError('Native feature extractor defaults differ from qualified model')
    state = {
        'had_local': 'prepare' in vars(duplex),
        'previous_local': vars(duplex).get('prepare'),
        'calls': {'prepare': 0, 'restored_stale_state': 0, 'prepare_failed': 0},
        'proof': {'enabled': True, 'optimization': 'reference_audio_normalization_reset',
                  'model_sha256': QUALIFIED_MODEL_SHA256,
                  'processor_sha256': QUALIFIED_PROCESSOR_SHA256,
                  'canonical_state': canonical,
                  'canonical_origin': 'qualified native constructor defaults',
                  'streaming_normalization_policy_changed': False,
                  'reference_audio_changed': False, 'weights_changed': False,
                  'dtype_changed': False, 'global_patch': False,
                  'requires_independent_gpu_reference_prefix_gate': True},
    }
    lock = threading.Lock()

    @functools.wraps(original)
    def prepare(*args, **kwargs):
        if not lock.acquire(blocking=False):
            raise RuntimeError('Concurrent prepare on one native stream owner is unsupported')
        before = _snapshot(extractor)
        try:
            if model.processor is not duplex.processor or model.processor.audio_processor is not extractor:
                raise RuntimeError('Native audio processor identity changed after adapter installation')
            if getattr(model, 'training', False):
                raise RuntimeError('Reference normalization adapter cannot execute during training')
            state['calls']['prepare'] += 1
            state['calls']['restored_stale_state'] += int(before != canonical)
            _restore(extractor, canonical)
            result = original(*args, **kwargs)
            # The qualified prepare performs only offline reference extraction.
            # Streaming inputs start later and must keep their native policy.
            if _snapshot(extractor) != canonical:
                raise RuntimeError('Unexpected normalization mutation during native prepare')
            return result
        except BaseException:
            state['calls']['prepare_failed'] += 1
            _restore(extractor, before)
            raise
        finally:
            lock.release()

    state['installed'] = prepare
    duplex.prepare = prepare
    setattr(duplex, STATE_KEY, state)
    return status(duplex)
