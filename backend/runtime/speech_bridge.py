"""Mute private-only turn EOS: it must not initialize a new spoken turn.

Keep native flush of already buffered public speech. Public EOS, including an
empty EOS chunk after public words, remains unchanged. No model weight changes.
"""
import types

def private_only(results,state,open_id,close_id,controls):
    inside=state['inside'];private_turn=state['private_turn'];public_words=0;end=False
    for token,hidden,ended in results:
        end|=bool(ended)
        if token==open_id:inside=True;private_turn=True
        elif token==close_id:inside=False
        elif not inside and token not in controls:public_words+=1
    mute=private_turn and public_words==0
    state['inside']=False if end else inside
    state['private_turn']=False if end or public_words else private_turn
    return mute,end

def install(duplex):
    if getattr(duplex,'private_eos_bridge_version',None):return
    t=duplex.tokenizer;open_id=t.convert_tokens_to_ids('<delegate>');close_id=t.convert_tokens_to_ids('</delegate>')
    eos=t.convert_tokens_to_ids('<|turn_eos|>')
    controls={t.convert_tokens_to_ids(x) for x in ['<|speak|>','<|listen|>','<|turn_eos|>','<|chunk_eos|>','</unit>']}
    original=duplex._convert_results_to_tts_input;reset_original=duplex._reset_streaming_state
    state={'inside':False,'private_turn':False}
    def reset(self,*args,**kwargs):
        state.update(inside=False,private_turn=False);self.private_eos_muted=0;self.orphan_eos_muted=0
        return reset_original(*args,**kwargs)
    def convert(self,results):
        # A numerically stable FP32 Thinker may coexist with the frozen native
        # BF16 speech projector. Cast only at this explicit interface boundary.
        dtype=next(self.model.tts.projector_semantic.parameters()).dtype
        results=[(token,hidden.to(dtype=dtype),ended) for token,hidden,ended in results]
        # Native generation normally starts with speak. Recover delegate state
        # even if a model emits its opening/closing marker at j=0, whose hidden
        # vector the native converter intentionally does not retain.
        totals=self.total_ids
        endings=[i for i,v in enumerate(totals) if v==t.convert_tokens_to_ids('</unit>')]
        start=endings[-2]+1 if len(endings)>=2 else 0
        first=totals[start] if start<len(totals) else None
        if first==open_id:
            state.update(inside=True,private_turn=True);self._delegate_tts_skip=True
        elif first==close_id:
            state['inside']=False;self._delegate_tts_skip=False
        mute,end=private_only(results,state,open_id,close_id,controls)
        orphan=end and bool(results) and all(r[0] in controls for r in results) and self.tts_past_key_values is None
        if orphan:
            self.orphan_eos_muted+=1
            return None
        if not mute:return original(results)
        # Let the native converter update its cross-unit delegate mask. Do not
        # send a private EOS hidden state or an empty audio_bos into speech.
        cleaned=[r for r in results if r[0]!=eos]
        output=original(cleaned) if cleaned else None
        if end:self._delegate_tts_skip=False
        if any(r[0]==eos for r in results):self.private_eos_muted+=1
        return None
    duplex._reset_streaming_state=types.MethodType(reset,duplex)
    duplex._convert_results_to_tts_input=types.MethodType(convert,duplex)
    duplex.private_eos_bridge_version='delegate-private-orphan-eos-muted-v3'
    duplex.private_eos_muted=0
    duplex.orphan_eos_muted=0
