"""Exercise model stop decisions through the real WebSocket session."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend/web'), str(ROOT/'backend/tools')]
from server import NativeOwner, create_app

class Tokenizer:
    specials = {'<delegate>': 2000000, '</delegate>': 2000001, '<|turn_eos|>': 2000002,
                '<|listen|>': 2000003}
    all_special_ids = list(specials.values())
    added_tokens_decoder = {}

    def convert_tokens_to_ids(self, text):
        return self.specials[text]

    def decode(self, ids, skip_special_tokens=True):
        return ''.join(chr(token) for token in ids if token not in self.all_special_ids)


class ScriptedOwner:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.ready = True
        self.active = None
        self.proof = {'backend': 'nvidia-cuda'}
        self.tokenizer = Tokenizer()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = asyncio.Lock()
        self.engine = SimpleNamespace(d=SimpleNamespace(total_ids=[]))
        self.speech_discards = 0

    async def run(self, function, *args):
        return function(*args)

    def prepare(self):
        pass

    def finish(self):
        pass

    def discard_speech(self):
        self.speech_discards += 1

    def step(self, wave, observations):
        text, ended = next(self.outputs)
        ids = list(map(ord, text)) + ([self.tokenizer.specials['<|turn_eos|>']] if ended else [])
        self.engine.d.total_ids.extend(ids)
        return dict(ids=ids, wave=np.full(2400, .1, dtype=np.float32),
                    end_of_turn=ended, is_listen=False,
                    prefill_seconds=.01, generation_seconds=.01, compute_seconds=.02,
                    condition={'proven_public_flush': False})


class EosThenListenOwner(ScriptedOwner):
    def step(self, wave, observations):
        result = super().step(wave, observations)
        if result['end_of_turn']:
            listen = self.tokenizer.specials['<|listen|>']
            result['ids'].append(listen)
            self.engine.d.total_ids.append(listen)
            # The native listen branch returns False even after emitting EOS.
            result.update(end_of_turn=False, is_listen=True, wave=np.zeros(24000, dtype=np.float32))
        return result


class VoiceInterruptTests(unittest.TestCase):
    def test_browser_player_cancellation(self):
        result = subprocess.run(['node', '--test', str(ROOT/'tests/audio-player.test.mjs')],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def replay(self, voice, outputs, acknowledge=(), owner_type=ScriptedOwner, expected_discards=None):
        received = []
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'config.json'
            config.write_text(json.dumps({'system_prompt': 'Streaming Omni Conversation.'}))
            owner = owner_type(outputs)
            with TestClient(create_app(str(config), 'test', directory, owner)) as client:
                with client.websocket_connect('/ws?initial_tools=[]') as ws:
                    while ws.receive_json()['type'] != 'ready':
                        pass
                    for tick, talking in enumerate(voice):
                        samples = 16560 if tick == 0 else 16000
                        if isinstance(talking,bool):
                            wave = np.full(samples,1000 if talking else 0,dtype='<i2')
                        elif isinstance(talking,np.ndarray):
                            wave = talking.astype('<i2')
                            self.assertEqual(len(wave), samples)
                        else:
                            wave = np.concatenate([np.full(round(seconds*16000),1000 if active else 0,dtype='<i2')
                                                   for seconds,active in talking])
                            self.assertEqual(len(wave), samples)
                        pcm = wave.tobytes()
                        ws.send_json(dict(type='audio', data=base64.b64encode(pcm).decode(),
                                          sample_rate=16000, format='pcm16', sequence=tick))
                        messages = []
                        while True:
                            message = ws.receive_json()
                            self.assertNotEqual(message['type'], 'fatal_error', message)
                            messages.append(message)
                            if message['type'] == 'timing':
                                break
                        received.append(messages)
                        if tick in acknowledge:
                            for message in messages:
                                if message['type']=='playback':
                                    ws.send_json(dict(type='playback_ack', utterance_id=message['utterance_id'],
                                                      chunks=message['chunk_seq']))
                    ws.send_json({'type': 'stop_session'})
            if expected_discards is not None:
                self.assertEqual(owner.speech_discards, expected_discards)
        return received

    def test_model_eos_after_new_user_speech_cancels_old_audio(self):
        units = self.replay([True, [(0.4,False),(0.6,True)], False],
                            [('回答', False), ('', True), ('新的回答', True)])
        stopped = units[1]
        self.assertEqual([m['type'] for m in stopped if m['type'].startswith('playback')],
                         ['playback_cancel'])
        self.assertEqual(next(m for m in stopped if m['type'] == 'playback_cancel')['utterance_ids'], ['0'])
        self.assertEqual(next(m for m in units[2] if m['type'] == 'playback')['utterance_id'], '1')

    def test_eos_then_listen_cancels_pending_reply_after_new_user_speech(self):
        units = self.replay([True, [(0.4,False),(0.6,True)]],
                            [('回答', False), ('', True)], owner_type=EosThenListenOwner, expected_discards=1)
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')],
                         ['playback_cancel'])
        self.assertEqual(next(m for m in units[1] if m['type']=='playback_cancel')['utterance_ids'], ['0'])

    def test_quiet_eos_then_listen_finishes_without_discarding_pending_audio(self):
        units = self.replay([True, False], [('回答', False), ('', True)],
                            owner_type=EosThenListenOwner, expected_discards=0)
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')],
                         ['playback_end'])
        self.assertEqual(next(m for m in units[1] if m['type']=='playback_end')['utterance_id'], '0')

    def test_private_eos_then_listen_does_not_cancel_public_audio(self):
        class PrivateEosOwner(EosThenListenOwner):
            def step(self, wave, observations):
                before = len(self.engine.d.total_ids)
                result = super().step(wave, observations)
                if result['is_listen']:
                    result['ids'] = [self.tokenizer.specials['<delegate>']] + list(map(ord, '{')) + result['ids']
                    self.engine.d.total_ids[before:] = result['ids']
                return result
        units = self.replay([True, [(0.4,False),(0.6,True)]],
                            [('回答', False), ('', True)], owner_type=PrivateEosOwner, expected_discards=0)
        self.assertFalse(any(m['type'].startswith('playback') for m in units[1]))

    def test_normal_eos_keeps_tail_and_does_not_cancel_original_question(self):
        units = self.replay([True, True], [('回答', False), ('结束', True)])
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')],
                         ['playback', 'playback_end'])

    def test_short_noise_does_not_cancel_the_normal_tail(self):
        units = self.replay([True, [(0.4,False),(0.04,True),(0.56,False)]],
                            [('回答', False), ('结束', True)])
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')],
                         ['playback', 'playback_end'])

    def test_quiet_stop_cancels_and_releases_speech_cache(self):
        quiet_stop = np.concatenate([np.zeros(6400,dtype='<i2'),np.full(9600,80,dtype='<i2')])
        units = self.replay([True,quiet_stop], [('回答',False),('',True)], expected_discards=1)
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')],
                         ['playback_cancel'])

    def test_short_hum_does_not_discard_a_normal_tail(self):
        noise = np.zeros(16000,dtype='<i2')
        noise[6400:8960] = np.rint(1000*np.sin(2*np.pi*50*np.arange(2560)/16000)).astype('<i2')
        units = self.replay([True,noise], [('回答',False),('正常尾音',True)], expected_discards=0)
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')],
                         ['playback','playback_end'])
        self.assertEqual(next(m for m in units[1] if m['type']=='playback')['text'], '正常尾音')

    def test_discard_speech_keeps_thinker_and_input_context(self):
        owner = NativeOwner({},'test')
        self.addCleanup(owner.pool.shutdown)
        thinker = SimpleNamespace(cache=object(),embeds=object())
        audio = np.arange(16,dtype=np.float32)
        logits,history = object(),[1,2,3]
        d = SimpleNamespace(decoder=thinker,audio_buffer=audio,pending_logits=logits,total_ids=history,
                            tts_past_key_values=object(),tts_text_start_pos=73,
                            tts_current_turn_start_time=8,_delegate_turn_preflushed=True,
                            token2wav_buffer=[100,101,102])
        def reset_waveform():
            d.token2wav_buffer = [4218]*3
        d._reset_token2wav_for_new_turn = reset_waveform
        owner.engine = SimpleNamespace(d=d)
        owner.discard_speech()
        self.assertIsNone(d.tts_past_key_values)
        self.assertEqual(d.tts_text_start_pos,0)
        self.assertIsNone(d.tts_current_turn_start_time)
        self.assertFalse(d._delegate_turn_preflushed)
        self.assertEqual(d.token2wav_buffer,[4218]*3)
        self.assertIs(d.decoder,thinker)
        self.assertIs(d.audio_buffer,audio)
        self.assertIs(d.pending_logits,logits)
        self.assertIs(d.total_ids,history)

    def test_new_speech_alone_does_not_stop_the_model(self):
        units = self.replay([True, [(0.4,False),(0.6,True)]],
                            [('回答', False), ('继续', False)])
        self.assertEqual([m['type'] for m in units[1] if m['type'].startswith('playback')], ['playback'])

    def test_continued_reply_does_not_turn_later_normal_eos_into_interruption(self):
        units = self.replay([True, [(0.4,False),(0.6,True)], False, False],
                            [('回答', False), ('继续回答', False), ('继续回答', False), ('正常尾音', True)])
        self.assertEqual([m['type'] for m in units[3] if m['type'].startswith('playback')],
                         ['playback', 'playback_end'])
        self.assertEqual(next(m for m in units[3] if m['type']=='playback')['text'], '正常尾音')

    def test_interjection_across_units_can_stop_at_first_quiet_unit(self):
        units = self.replay([True, [(0.4,False),(0.6,True)], True, False],
                            [('回答', False), ('继续', False), ('继续', False), ('', True)])
        self.assertEqual([m['type'] for m in units[3] if m['type'].startswith('playback')],
                         ['playback_cancel'])

    def test_completed_but_unplayed_reply_is_cancelled_without_dropping_new_reply(self):
        units = self.replay([True, [(0.4,False),(0.6,True)], True],
                            [('旧回答', True), ('新回答', False), ('新回答尾音', True)], expected_discards=0)
        final = units[2]
        self.assertEqual([m['type'] for m in final if m['type'].startswith('playback')],
                         ['playback_cancel', 'playback', 'playback_end'])
        self.assertEqual(next(m for m in final if m['type']=='playback_cancel')['utterance_ids'], ['0'])
        self.assertEqual(next(m for m in final if m['type']=='playback')['utterance_id'], '1')

    def test_played_reply_is_removed_from_pending_receipts(self):
        units = self.replay([True, [(0.4,False),(0.6,True)]],
                            [('旧回答', True), ('新回答', True)], acknowledge=(0,))
        self.assertFalse(any(m['type']=='playback_cancel' for m in units[1]))


if __name__ == '__main__':
    unittest.main()
