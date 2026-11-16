"""Explicit LLM-only SDPA backend, including checkpoint recomputation.

The frontend and speech modules are left untouched. Wrapping each attention
module instead of only its caller keeps backward checkpoint replay in the same
backend after the forward caller's context has exited.
"""
import functools
import hashlib
from pathlib import Path

MATH = 'llm_math_canonical'


def source_sha256(): return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def install(model, profile):
    if profile != MATH: raise ValueError('Unknown LLM scoring profile')
    layers = list(model.llm.model.layers)
    if not layers: raise ValueError('LLM attention layers are missing')
    installed = 0
    for layer in layers:
        attention = layer.self_attn
        previous = getattr(attention, '_venus_original_sdpa_forward', None)
        if previous is None:
            original = attention.forward
            def make_forward(function):
                @functools.wraps(function)
                def forward(*args, **kwargs):
                    import torch
                    with torch.backends.cuda.sdp_kernel(enable_flash=False, enable_math=True, enable_mem_efficient=False):
                        return function(*args, **kwargs)
                return forward
            attention._venus_original_sdpa_forward = original
            attention.forward = make_forward(original)
        installed += 1
    model._venus_llm_scoring_profile = profile
    return {'profile': profile, 'attention_layers': len(layers), 'math_wrapped_layers': installed,
        'source_sha256': source_sha256(), 'frontend_and_speech_wrapped': False,
        'gradient_checkpoint_recomputation_uses_same_attention_wrapper': True}
