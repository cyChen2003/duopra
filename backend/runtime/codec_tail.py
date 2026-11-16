"""Isolated EOS-aware native codec budget; preserve normal 25-frame units.

The mounted runtime resets codec KV after a turn_eos even if the fixed 26-token
budget exhausted before audio EOS. Record actual finish and bound the final
flush separately. Never install into the original model source/live processes.
"""
import inspect,textwrap,types

def install(duplex,maximum=156):
    tts=duplex.model.tts
    if getattr(tts,'codec_tail_guard_installed',False):return
    original=tts.generate_chunk
    source=textwrap.dedent(inspect.getsource(original))
    marker='return genrated_input_ids, past_key_values'
    assert source.count(marker)==1
    source=source.replace(marker,"self.codec_last_finished = bool(finish.all().item())\n    self.codec_last_budget_exhausted = not self.codec_last_finished\n    "+marker)
    namespace=dict(inspect.unwrap(original.__func__).__globals__)
    exec(compile(source,'<isolated_native_codec_finish_trace>','exec'),namespace)
    traced=types.MethodType(namespace['generate_chunk'],tts)
    convert_original=duplex._convert_results_to_tts_input
    def convert(self,results):
        # Native condition=None with existing public codec KV is a real final
        # public flush, even while Thinker continues a private delegate turn.
        condition=convert_original(results)
        self.codec_pending_public_flush_final=condition is None and self.tts_past_key_values is not None
        return condition
    duplex._convert_results_to_tts_input=types.MethodType(convert,duplex)
    duplex.codec_pending_public_flush_final=False
    def generate(self,*args,**kwargs):
        public_flush=bool(duplex.codec_pending_public_flush_final)
        final=kwargs.get('min_new_tokens',0)==0 and (duplex.current_turn_ended or public_flush)
        if final:kwargs['max_new_token']=maximum
        try:output=traced(*args,**kwargs)
        finally:duplex.codec_pending_public_flush_final=False
        duplex.codec_last_generation_unit=duplex.audio_chunk_idx
        duplex.codec_last_was_final=final
        duplex.codec_last_public_flush_final=bool(final and public_flush)
        duplex.codec_last_final_reason='public_flush'if final and public_flush else'turn_eos'if final else'nonfinal'
        return output
    tts.generate_chunk=types.MethodType(generate,tts)
    tts.codec_tail_guard_installed=True
    duplex.codec_final_maximum=maximum
    duplex.codec_last_generation_unit=-1
    reset_original=duplex._reset_streaming_state
    def reset(self,*args,**kwargs):
        result=reset_original(*args,**kwargs)
        self.codec_last_generation_unit=-1;self.codec_last_was_final=False
        self.codec_pending_public_flush_final=False;self.codec_last_public_flush_final=False;self.codec_last_final_reason=None
        tts.codec_last_finished=False;tts.codec_last_budget_exhausted=False
        return result
    duplex._reset_streaming_state=types.MethodType(reset,duplex)
