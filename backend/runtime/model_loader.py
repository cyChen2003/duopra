"""Duopra model loading for local and container NVIDIA CUDA entrypoints."""
import sys
import time
import types
from pathlib import Path

from deployment_model import ReleaseError, digest
from standalone_model import load_inventory, restore_fp32


def validate_settings(settings):
    if (settings['thinker_dtype'] != 'float32' or settings['speech_dtype'] != 'bfloat16'
            or settings['system_prompt'] != 'Streaming Omni Conversation.'
            or settings['sliding_window_mode'] != 'off'
            or settings['scoring_profile'] != 'llm_math_canonical'):
        raise ReleaseError('Duopra requires FP32 backbone, BF16 speech and canonical prompt/attention/KV settings')
    model_path = Path(settings['model']).resolve()
    if load_inventory(settings['standalone_inventory'], model_path) != settings['release']:
        raise ReleaseError('Serving model identity differs from inventory')
    print('Duopra model file checksums verified.', flush=True)
    return model_path


def load_model(settings, runtime, model_path):
    """Load pinned tensors and install the native duplex speech protocol."""
    import torch
    from transformers import AutoConfig, AutoModel
    from cache_compat import install as install_cache_compat, install_audio_attention
    install_cache_compat()
    torch.cuda.set_device(0)
    torch.manual_seed(641)
    began = time.monotonic()
    cfg = AutoConfig.from_pretrained(model_path, trust_remote_code=True, local_files_only=True)
    cfg.init_vision = False
    cfg.init_tts = True
    model = AutoModel.from_pretrained(model_path, config=cfg, trust_remote_code=True,
        local_files_only=True, torch_dtype=torch.bfloat16, attn_implementation='sdpa').eval()
    install_audio_attention(model)
    for module in model.modules():
        if (hasattr(module, 'parametrizations') and 'weight' in module.parametrizations
                and all(type(x).__name__ == '_WeightNorm' for x in module.parametrizations.weight)):
            torch.nn.utils.parametrize.remove_parametrizations(module, 'weight', leave_parametrized=True)
    # Restore FP32 bytes on CPU before the single GPU transfer. Loading BF16 and
    # then casting back would permanently discard trained backbone/projector bits.
    model.llm.float()
    model.tts.projector_semantic.float()
    model.requires_grad_(False)
    exported, projector = restore_fp32(model, settings)
    print('FP32 backbone and projector restored and compared.', flush=True)
    model.to('cuda')
    model.tts.model.config._attn_implementation = 'eager'
    native = sys.modules[type(model).__module__]
    duplex_type = getattr(native, 'MiniCPMODuplex', None)
    if duplex_type is None:
        raise ReleaseError('The Duopra bundle must provide MiniCPMODuplex')
    duplex = duplex_type.from_existing_model(model, generate_audio=True,
        force_listen_count=0, sliding_window_mode='off')
    from audio_protocol import install as install_protocol
    from audio_native_reference_normalization import install as install_normalization
    install_protocol(model, duplex, model_path)
    normalization = install_normalization(model, duplex, {'enabled': True})
    for speech_module in (model.tts.audio_tokenizer.flow, model.tts.audio_tokenizer.hift):
        speech_module.requires_grad_(False)
    from decoder import install as install_decoder, install_sampling
    decoder = install_decoder(duplex.decoder)
    decoder.update(install_sampling(duplex.decoder))
    from codec_graph import install as install_codec_graph
    codec_graph = install_codec_graph(model.tts.model)
    from waveform_graph import install as install_waveform_graph
    waveform_graph = install_waveform_graph(model.tts.audio_tokenizer)
    from speech_bridge import install as install_bridge
    from codec_tail import install as install_tail
    from speech_precision import install as install_precision
    from delegate_speech_repair import install as install_repair
    from attention_backend import install as install_attention
    install_bridge(duplex)
    install_tail(duplex, maximum=settings['codec_final_maximum'])
    install_precision(duplex)
    repair = install_repair(duplex)
    attention = install_attention(model, settings['scoring_profile'])
    reference, prompt_path = model._load_default_reference_audio()
    proof = {
        'backend': settings['runtime_backend'], 'model_variant': settings['model_variant'],
        'grpo_update': settings['release']['update'], 'actor_sha256': settings['release']['actor_sha256'],
        'thinker_dtype': str(next(model.llm.parameters()).dtype),
        'speech_dtype': str(next(model.tts.parameters()).dtype),
        'waveform_dtype': {
            'flow': str(next(model.tts.audio_tokenizer.flow.parameters()).dtype),
            'hift': str(next(model.tts.audio_tokenizer.hift.parameters()).dtype),
        },
        'native_first_samples': 16560, 'native_subsequent_samples': 16000,
        'public_max_new_speak_tokens_per_chunk': 50, 'text_decode_mode': 'greedy',
        'system_prompt': settings['system_prompt'], 'codec_final_maximum': settings['codec_final_maximum'],
        'speaker_reference_sha256': digest(prompt_path), 'image_runtime': runtime,
        'canonical_attention': attention, 'reference_normalization': normalization,
        'decoder': decoder, 'codec_graph': codec_graph, 'waveform_graph': waveform_graph,
        'delegate_speech_repair': repair, 'semantic_projector_release': projector,
        'grpo_merge': exported, 'model_load_seconds': time.monotonic() - began,
        'weights_in_image': False,
        'realtime_gate_passed': False, 'same_input_and_audio_quality_acceptance_claimed': False,
    }
    return types.SimpleNamespace(model=model, d=duplex), model, duplex.tokenizer, reference, proof
