"""Observe actual native TTS token/hidden inputs without changing its outputs."""
import types


class TTSConditionAudit:
    def __init__(self,duplex):
        self.d=duplex;self.original=duplex._convert_results_to_tts_input;self.frame=None;self.inside=False
        t=duplex.tokenizer;self.open_id=t.convert_tokens_to_ids('<delegate>');self.close_id=t.convert_tokens_to_ids('</delegate>')
        self.end=t.convert_tokens_to_ids('<|turn_eos|>');self.unit_end=t.convert_tokens_to_ids('</unit>')
        self.handles=[duplex.model.tts.emb_text.register_forward_pre_hook(self.text_hook),
                      duplex.model.tts.projector_semantic.register_forward_pre_hook(self.hidden_hook)]
        audit=self
        def convert(this,results):return audit.convert(results)
        duplex._convert_results_to_tts_input=types.MethodType(convert,duplex)

    def begin(self,pending_public):
        self.frame={'converter_called':False,'before_tts_past':False,'pending_public_before':bool(pending_public),
                    'condition_none':None,'token_filter_evidence':[],'actual_projector_rows':[],
                    'private_hidden_conditioned':0,'unmapped_hidden_conditioned':0,'private_text_conditioned':0}
        self.in_convert=False;self.latest_ids=[];self.sources=[]

    def convert(self,results):
        f=self.frame;f['converter_called']=True;f['before_tts_past']=self.d.tts_past_key_values is not None
        totals=self.d.total_ids;ends=[i for i,x in enumerate(totals)if x==self.unit_end]
        raw=totals[ends[-2]+1 if len(ends)>=2 else 0:];inside=self.inside;labels=[]
        for token in raw:
            private=inside or token in(self.open_id,self.close_id)
            if token==self.open_id:inside=True;private=True
            elif token==self.close_id:inside=False;private=True
            labels.append((token,private))
            if token==self.end:inside=False
        self.inside=inside
        # Native generation omits the j=0 hidden row and unit host tokens. Match
        # the exact chronological result subsequence, preserving repeated IDs.
        cursor=1;self.sources=[]
        for index,(token,hidden,ended)in enumerate(results):
            while cursor<len(labels)and labels[cursor][0]!=token:cursor+=1
            if cursor>=len(labels):raise RuntimeError('Actual converter results are not a subsequence of generated unit tokens')
            private=labels[cursor][1];cursor+=1
            self.sources.append((token,hidden,private,index))
            f['token_filter_evidence'].append({'result_index':index,'token_id':token,'private_span':private,'entered_projector':False})
        self.in_convert=True
        try:value=self.original(results)
        finally:self.in_convert=False
        f['condition_none']=value is None
        return value

    def text_hook(self,module,args):
        if self.in_convert:self.latest_ids=args[0].detach().reshape(-1).cpu().tolist()

    def hidden_hook(self,module,args):
        if not self.in_convert:return
        import torch
        rows=args[0].detach().reshape(-1,args[0].shape[-1]);f=self.frame
        if len(rows)!=len(self.latest_ids):raise RuntimeError('Actual TTS text/semantic row count differs')
        for token,row in zip(self.latest_ids,rows):
            matching=[(private,index)for source_id,hidden,private,index in self.sources
                      if source_id==token and torch.equal(row,hidden.detach().reshape(-1).to(dtype=row.dtype))]
            unknown=not matching;private=any(x[0]for x in matching)
            if private:f['private_hidden_conditioned']+=1;f['private_text_conditioned']+=1
            if unknown:f['unmapped_hidden_conditioned']+=1
            for _,index in matching:f['token_filter_evidence'][index]['entered_projector']=True
            f['actual_projector_rows'].append({'token_id':token,'matched_result_indices':[x[1]for x in matching],
                                              'private_span':private,'unmapped':unknown})

    def finish(self):
        f=dict(self.frame)
        f['proven_public_flush']=bool(f['converter_called']and f['condition_none']and f['before_tts_past']and
                                     f['pending_public_before']and not f['private_hidden_conditioned']and
                                     not f['private_text_conditioned']and not f['unmapped_hidden_conditioned'])
        f['private_filter_proven']=not(f['private_hidden_conditioned']or f['private_text_conditioned']or f['unmapped_hidden_conditioned'])
        return f

    def close(self):
        self.d._convert_results_to_tts_input=self.original
        for h in self.handles:h.remove()


def stop_state(units,ended_events,start_tick,complete_tick,scene):
    """Distinguish active STOP response from an already silent old turn."""
    if scene not in('stop','redirect')or start_tick is None or complete_tick is None:
        return {'stop_latency_units':None,'stop_response_kind':'not_stop_case','active_STOP_proven':False}
    delivered=any(u['tick']>=complete_tick and u.get('audio_input_delivered')for u in units)
    at_start=next((u for u in units if u['tick']==start_tick),None)
    active=bool(at_start and at_start.get('active_public_before'))
    if not delivered:return {'stop_latency_units':None,'stop_response_kind':'u2_audio_not_completely_delivered','active_STOP_proven':False}
    if active:
        event=next((tick for tick in ended_events if tick>=start_tick),None)
        old_turn=at_start.get('public_turn_id')
        old_audio=sum(u.get('audio_energy',0)>0 for u in units if u['tick']>=complete_tick and u.get('public_turn_id')==old_turn)
        return {'stop_latency_units':None if event is None else max(0,event-complete_tick),
                'stop_response_kind':'active_turn_EOS'if event is not None else'active_turn_not_stopped','active_STOP_proven':event is not None,
                'u2_audio_delivered':True,'old_public_turn_id':old_turn,'old_audio_units_after_u2_complete':old_audio}
    prior=any(tick<start_tick for tick in ended_events)
    no_old_audio=all(not u.get('proven_public_flush')and not u.get('active_public_before')
                     for u in units if start_tick<=u['tick']<=complete_tick)
    if prior and no_old_audio:
        return {'stop_latency_units':0,'stop_response_kind':'not_active_at_interrupt','active_STOP_proven':False,
                'already_quiescent_after_verified_u2_delivery':True,'u2_audio_delivered':True,
                'old_audio_units_after_u2_complete':0,'old_turn_already_EOS_before_u2':True}
    return {'stop_latency_units':None,'stop_response_kind':'no_active_old_turn_response_unproven','active_STOP_proven':False}
