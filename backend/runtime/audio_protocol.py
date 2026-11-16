"""Audio-native DuplexAgent interfaces; no weights or public pacing changes."""
import types
from pathlib import Path


def filter_private(results, state, open_id, close_id):
    kept = []
    for item in results:
        token = item[0]
        if token == open_id:
            state['inside'] = True
        elif token == close_id:
            state['inside'] = False
        elif not state['inside']:
            kept.append(item)
    if any(item[2] for item in results):
        state['inside'] = False
    return kept


def install(model, duplex, model_path):
    """Preserve native Audio math, supply its packaged voice and private mask."""
    if not hasattr(model, '_load_default_reference_audio'):
        reference = Path(model_path) / 'assets/HT_ref_audio.wav'
        if not reference.is_file():
            raise FileNotFoundError(reference)
        def load_reference(self):
            import librosa
            import numpy as np
            wave, _ = librosa.load(str(reference), sr=16000, mono=True)
            if wave.ndim != 1 or not len(wave) or not np.isfinite(wave).all():
                raise ValueError('Native Audio reference waveform invalid')
            return wave, str(reference)
        model._load_default_reference_audio = types.MethodType(load_reference, model)
    # Original Audio lacks Omni's delegate-to-speech exclusion. The protocol
    # wrapper changes ONLY which public token/hidden pairs enter frozen TTS.
    # Every private token still participates in the native Thinker KV context.
    if type(duplex).__name__ != 'MiniCPMODuplex':
        return
    # Audio's original caller does not accept condition=None. Reuse the exact
    # previously verified private-only branch, whose public/LLM math is equal
    # to the Audio implementation; this prevents an empty private call from
    # entering codec generation and preserves one genuine public tail flush.
    from audio_duplex_generate import streaming_generate
    duplex.streaming_generate = types.MethodType(streaming_generate, duplex)
    original = duplex._convert_results_to_tts_input
    original_reset = duplex._reset_streaming_state
    state = {'inside': False}
    open_id = duplex.tokenizer.convert_tokens_to_ids('<delegate>')
    close_id = duplex.tokenizer.convert_tokens_to_ids('</delegate>')
    def convert(self, results):
        # The established speech bridge also handles a marker at native j=0,
        # whose hidden vector the original converter intentionally omits.
        state['inside'] = getattr(self, '_delegate_tts_skip', state['inside'])
        had_private = state['inside'] or any(x[0] in (open_id, close_id) for x in results)
        kept = filter_private(results, state, open_id, close_id)
        self._delegate_tts_skip = state['inside']
        if had_private and not kept:
            return None
        return original(kept)
    def reset(self, *args, **kwargs):
        state['inside'] = False
        self._delegate_tts_skip = False
        self._delegate_turn_preflushed = False
        return original_reset(*args, **kwargs)
    duplex._convert_results_to_tts_input = types.MethodType(convert, duplex)
    duplex._reset_streaming_state = types.MethodType(reset, duplex)
    duplex.audio_delegate_protocol_version = 'audio-native-private-tts-mask-v1'
