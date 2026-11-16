"""Chronological delegate mask over complete native units, including j=0.

The frozen native converter and its FP32-projector/BF16 handoff are preserved.
This instance-local wrapper replaces the older chunk-final EOS mask reset.
It never removes private tokens from the Thinker's autoregressive KV history.
"""
import hashlib
from pathlib import Path
import types

VERSION = 'chronological-delegate-eos-mask-v1'


class DelegateMask:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.open_id = tokenizer.convert_tokens_to_ids('<delegate>')
        self.close_id = tokenizer.convert_tokens_to_ids('</delegate>')
        self.end_id = tokenizer.convert_tokens_to_ids('<|turn_eos|>')
        self.unit_end = tokenizer.convert_tokens_to_ids('</unit>')
        self.controls = set(tokenizer.all_special_ids)
        self.controls.update(int(k) for k, v in tokenizer.added_tokens_decoder.items() if v.special)
        self.reset()

    def reset(self):
        self.inside = False
        self.quarantined = False
        self.private_ids = []

    def classify(self, ids):
        flags = []
        for token in ids:
            if self.quarantined:
                private = True
            elif token == self.open_id:
                private = True
                if self.inside:
                    self.quarantined = True
                else:
                    self.inside = True
                    self.private_ids = []
            elif token == self.close_id:
                private = True
                if self.inside:
                    try:
                        from tool_runtime import strict_json
                        value = strict_json(self.tokenizer.decode(self.private_ids, skip_special_tokens=True))
                        if type(value) is not dict:
                            raise ValueError('Private action is not one object')
                    except (ValueError, TypeError):
                        self.quarantined = True
                self.inside = False
                self.private_ids = []
            else:
                private = self.inside
                if self.inside and token not in self.controls:
                    self.private_ids.append(token)
                    if len(self.private_ids) > 4096:
                        self.quarantined = True
                        self.private_ids = []
            flags.append(private)
            # Reset at the EOS position, not at the end of a unit that may
            # already contain the opening of another private span.
            if token == self.end_id:
                self.reset()
        return flags


def result_flags(raw, flags, results):
    """Match the chronological j>0 native hidden rows without guessing IDs."""
    cursor = 1
    selected = []
    for token, hidden, ended in results:
        while cursor < len(raw) and raw[cursor] != token:
            cursor += 1
        if cursor == len(raw):
            raise RuntimeError('Delegate mask cannot map native result position')
        selected.append(flags[cursor])
        cursor += 1
    return selected


def install(duplex):
    if getattr(duplex, 'delegate_speech_repair_version', None) == VERSION:
        return duplex.delegate_speech_repair_proof
    if type(duplex).__name__ != 'MiniCPMODuplex':
        raise ValueError('Delegate repair requires the qualified native Audio class')
    # The class method is the immutable native arithmetic, preceding all
    # instance-local mask wrappers. The model inventory verifies the original
    # sources; this wrapper records its own source digest below.
    native = type(duplex)._convert_results_to_tts_input.__get__(duplex, type(duplex))
    original_reset = duplex._reset_streaming_state
    mask = DelegateMask(duplex.tokenizer)
    codec_dtype = duplex.model.tts.emb_text.weight.dtype

    def reset(self, *args, **kwargs):
        mask.reset()
        self.delegate_mask_record = None
        return original_reset(*args, **kwargs)

    def convert(self, results):
        ends = [i for i, token in enumerate(self.total_ids) if token == mask.unit_end]
        raw = self.total_ids[ends[-2] + 1 if len(ends) >= 2 else 0:]
        flags = mask.classify(raw)
        selected = result_flags(raw, flags, results)
        dtype = next(self.model.tts.projector_semantic.parameters()).dtype
        kept = [(token, hidden.to(dtype=dtype), ended)
                for (token, hidden, ended), private in zip(results, selected) if not private]
        self.delegate_mask_record = {
            'version': VERSION, 'result_rows': len(results),
            'kept_result_indices': [i for i, private in enumerate(selected) if not private],
            'dropped_private_rows': sum(selected), 'inside_after': mask.inside,
            'quarantined_after': mask.quarantined,
            'eos_then_new_private_span': any(token == mask.end_id and mask.open_id in raw[i + 1:]
                                             for i, token in enumerate(raw)),
        }
        self._delegate_tts_skip = mask.inside or mask.quarantined
        has_private = any(flags)
        ended = mask.end_id in raw
        orphan = ended and not any(token not in mask.controls for token, _, _ in kept) and self.tts_past_key_values is None
        if orphan:
            self.orphan_eos_muted += 1
            return None
        if has_private and not kept:
            if ended:
                self.private_eos_muted += 1
            return None
        value = native(kept)
        return None if value is None else value.to(dtype=codec_dtype)

    duplex._reset_streaming_state = types.MethodType(reset, duplex)
    duplex._convert_results_to_tts_input = types.MethodType(convert, duplex)
    duplex.delegate_speech_repair_version = VERSION
    duplex.delegate_speech_repair_proof = {
        'version': VERSION,
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'original_native_converter_math_unchanged': True,
        'thinker_kv_private_context_preserved': True,
        'chronological_eos_and_j0_handling': True,
        'malformed_private_spans_quarantined': True,
    }
    return duplex.delegate_speech_repair_proof
