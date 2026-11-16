"""Controlled native speech boundary replay before serving model sessions.

These forced decoder tokens test runtime isolation, not student tool selection,
ASR, speech accuracy or model pass@1. Real model hidden states and speech
modules execute; the original decoder and fresh session state are restored.
"""
import json


def qualify(owner):
    import numpy as np
    import torch
    d = owner.engine.d
    tokenizer = d.tokenizer
    token = tokenizer.convert_tokens_to_ids
    speak, opened, closed, eos, chunk = map(token, ['<|speak|>','<delegate>','</delegate>','<|turn_eos|>','<|chunk_eos|>'])
    encode = lambda text: tokenizer.encode(text, add_special_tokens=False)
    action = encode(json.dumps({'op':'select','call_id':'q1','query':'stock price'}, separators=(',',':')))
    public_a, public_b = encode('我来查一下。'), encode('查询完成。')
    middle = len(action)//2
    cases = {
        'eos_then_open': [[speak]+public_a+[eos,opened]+action[:middle]+[chunk],
                          [speak]+action[middle:]+[closed]+public_b+[eos,chunk]],
        'marker_at_j0': [[opened]+action[:middle]+[chunk],
                         [speak]+action[middle:]+[chunk],
                         [closed]+public_b+[eos,chunk]],
        'nested_quarantine': [[speak,opened]+encode('{')+[opened]+encode('NEVER_SPEAK_THIS')+[closed,chunk],
                               [speak]+encode('STILL_PRIVATE')+[eos,chunk]],
    }
    measurements = []
    for case, units in cases.items():
        assert all(len(ids)<49 for ids in units), 'Boundary fixture exceeds the native unit budget'
        owner.prepare()
        decoder = d.decoder
        original = decoder.decode
        owner.public_active = False
        try:
            for index, ids in enumerate(units):
                remaining = iter(ids)
                def controlled_decode(*args, **kwargs):
                    return torch.tensor([next(remaining,chunk)], device=d.device, dtype=torch.long)
                decoder.decode = controlled_decode
                samples = 16560 if index == 0 else 16000
                result = owner.step(np.zeros(samples,dtype=np.float32), [])
                condition = result['condition']
                assert not any(condition[k] for k in ['private_hidden_conditioned','private_text_conditioned','unmapped_hidden_conditioned'])
                assert result['ids'][:-1] == ids, 'Native replay did not consume the exact controlled unit'
                if case == 'nested_quarantine':
                    assert not np.any(np.abs(result['wave']) > 1e-8), 'Quarantined private speech was emitted'
                record = condition['delegate_mask']
                if case == 'eos_then_open' and index == 0:
                    assert record['inside_after'] and record['eos_then_new_private_span']
                measurements.append({'case':case,'unit':index,'controlled_native_replay':True,
                                     'private_rows_dropped':record['dropped_private_rows'],
                                     'public_projector_rows':len(condition['actual_projector_rows']),
                                     'private_conditioning_violations':0,
                                     'output_samples':len(result['wave']),
                                     'inside_after':record['inside_after'],
                                     'quarantined_after':record['quarantined_after']})
                owner.public_active = bool(condition['actual_projector_rows'])
        finally:
            decoder.decode = original
            owner.finish()
    return {'passed':True, 'cases':len(cases),'units':len(measurements),
            'measurements':measurements,'controlled_tokens':True,'real_native_hidden_and_speech_execution':True,
            'student_selected_tool_success_claimed':False,'CER_WER_or_duplex_latency_claimed':False,
            'decoder_restored_and_session_reset':True}
