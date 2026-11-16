"""FP32 semantic projection/norm, one cast into the unchanged BF16 codec."""
import types

PROFILE='fp32-semantic-projector-single-bf16-handoff-v1'


def install(duplex):
    if getattr(duplex,'speech_condition_precision_profile',None)==PROFILE:return
    tts=duplex.model.tts
    codec_dtype=tts.emb_text.weight.dtype
    # Frozen BF16 weight values are preserved exactly by expansion to FP32.
    # The original normalization and text addition now operate in FP32, rather
    # than first rounding LLM hidden states and the projector output to BF16.
    tts.projector_semantic.float()
    original=duplex._convert_results_to_tts_input
    def convert(self,results):
        value=original(results)
        return None if value is None else value.to(dtype=codec_dtype)
    duplex._convert_results_to_tts_input=types.MethodType(convert,duplex)
    duplex.speech_condition_precision_profile=PROFILE
