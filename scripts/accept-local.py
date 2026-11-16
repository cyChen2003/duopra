"""Exercise the real local HTTP/WebSocket/audio path and save inspectable output."""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import statistics
import time
import wave

import httpx
import websockets


async def run(args):
    base = f'http://127.0.0.1:{args.port}'
    origin = base
    if args.organizer_phase:
        await organizer(args, base)
        return
    result = {'started_at': time.time(), 'checks': {}, 'timings': [], 'text': ''}
    pcm = bytearray()
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        for route in ('/', '/static/app.js', '/api/config', '/healthz', '/api/model-proof'):
            response = await client.get(route)
            response.raise_for_status()
            result['checks'][route] = response.status_code
            if route == '/api/model-proof':
                result['model_proof'] = response.json()
                proof = result['model_proof']
                assert proof['ready'] and proof['proof']['backend'] == 'nvidia-cuda'
                assert proof['proof']['model_variant'] == 'Duopra'
                assert proof['proof']['final_projector_loaded']
        async with websockets.connect(f'ws://127.0.0.1:{args.port}/ws', origin=origin,
                                      max_size=4 * 1024 * 1024, open_timeout=120) as ws:
            while True:
                message = json.loads(await ws.recv())
                if message['type'] == 'fatal_error':
                    raise RuntimeError(message)
                if message['type'] == 'ready':
                    break
            result['checks']['websocket_ready'] = True
            result['framing'] = {k: message[k] for k in
                                 ('native_first_samples', 'native_subsequent_samples')}
            assert result['framing'] == {'native_first_samples': 16560, 'native_subsequent_samples': 16000}
            sender = None
            if args.transport == 'upload':
                response = await client.post(f'/api/sessions/{message["session_id"]}/audio',
                    content=Path(args.audio).read_bytes(),
                    headers={'x-venus-session-token': message['upload_token'],
                             'origin': origin, 'content-type': 'audio/wav'})
                response.raise_for_status()
                result['upload'] = response.json()
            else:
                sender = asyncio.create_task(stream_audio(ws, args.audio))
            result['transport'] = args.transport
            end_tick = None
            playback_ticks = set()
            while True:
                message = json.loads(await ws.recv())
                kind = message['type']
                if kind in ('fatal_error', 'command_error'):
                    raise RuntimeError(message)
                if kind == 'playback_cancel':
                    raise RuntimeError(f'Model cancelled the reply at tick {message["tick"]}: '
                                       f'{message.get("reason", "unspecified")}. Replay without an interruption.')
                if kind in ('playback', 'text'):
                    result['text'] += message.get('text', '')
                if kind == 'playback':
                    assert message['sample_rate'] == 24000 and message['channels'] == 1
                    pcm.extend(base64.b64decode(message['data'], validate=True))
                    playback_ticks.add(message['tick'])
                if kind == 'timing':
                    assert message['private_conditioning_violations'] == 0
                    expected = 1.035 if not result['timings'] else 1.0
                    assert abs(message['input_seconds'] - expected) < 1e-6
                    result['timings'].append(message)
                    if end_tick is not None and message['tick'] >= end_tick:
                        break
                if kind == 'playback_end' and pcm:
                    end_tick = message['tick']
                if kind == 'media_status' and message.get('state') == 'complete' and not pcm:
                    raise RuntimeError('No generated audio during input and postroll')
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            await ws.send(json.dumps({'type': 'stop_session'}))
        # Wait for cleanup and then establish a fresh model session.
        for _ in range(120):
            status = (await client.get('/api/status')).json()
            if status.get('busy') is False:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError('Session cleanup did not finish')
        async with websockets.connect(f'ws://127.0.0.1:{args.port}/ws', origin=origin,
                                      open_timeout=120) as ws:
            while True:
                message = json.loads(await ws.recv())
                if message['type'] == 'fatal_error':
                    raise RuntimeError(message)
                if message['type'] == 'ready':
                    result['checks']['reconnect_ready'] = True
                    break
            await ws.send(json.dumps({'type': 'stop_session'}))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output / 'response.wav'), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(pcm)
    import numpy as np
    samples = np.frombuffer(pcm, dtype='<i2').astype('float32') / 32768
    result['output_peak'] = float(np.max(np.abs(samples)))
    result['output_rms'] = float(np.sqrt(np.mean(samples ** 2)))
    assert result['output_peak'] > 0.001, 'Generated output is silent'
    result['output_seconds'] = len(pcm) / 48000
    if result['timings']:
        result['median_unit_compute_ms'] = statistics.median(x['compute_ms'] for x in result['timings'])
        speaking = [x for x in result['timings'] if x['tick'] in playback_ticks]
        result['speech_rtf'] = sum(x['compute_ms'] for x in speaking) / (1000 * result['output_seconds'])
        result['speech_unit_rtf_max'] = max(x['rtf_unit'] for x in speaking)
        result['speech_unit_rtf_median'] = statistics.median(x['rtf_unit'] for x in speaking)
    result['human_listening_accepted'] = False
    result['passed'] = True
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'timings'}, ensure_ascii=False, indent=2))


async def stream_audio(ws, filename):
    """Send real WAV speech through the browser's PCM16 WebSocket protocol."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd
    samples, rate = sf.read(filename, dtype='float32', always_2d=True)
    samples = samples.mean(axis=1)
    divisor = gcd(rate, 16000)
    samples = resample_poly(samples, 16000 // divisor, rate // divisor)
    samples = np.concatenate((samples, np.zeros(16000 * 20, dtype='float32')))
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()
    # 100 ms packets, paced like AudioWorklet capture.
    for sequence, start in enumerate(range(0, len(pcm), 3200)):
        await ws.send(json.dumps({'type': 'audio', 'format': 'pcm16', 'sample_rate': 16000,
                                 'sequence': sequence,
                                 'data': base64.b64encode(pcm[start:start + 3200]).decode()}))
        await asyncio.sleep(0.1)


async def organizer(args, base):
    """Seed, then verify after restarting the service with the same database."""
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    saved = output / 'organizer.json'
    headers = {'origin': base, 'x-duplex-personal-request': '1'}
    async with httpx.AsyncClient(base_url=base, timeout=30, headers=headers) as client:
        async def call(tool, arguments):
            response = await client.post('/api/personal/execute', json={'tool': tool, 'arguments': arguments})
            response.raise_for_status()
            return response.json()['payload']['result']
        if args.organizer_phase == 'seed':
            if saved.exists():
                raise RuntimeError('Organizer test already seeded; verify it before seeding again')
            profile = await client.get('/api/personal/profile')
            profile.raise_for_status()
            assert profile.json()['available']
            event = (await call('calendar.create', {'title': 'Reproduction check',
                'start': '2030-01-02T14:00+08:00', 'end': '2030-01-02T15:00+08:00'}))['item']
            task = (await call('task.create', {'title': 'Reproduction check'}))['item']
            saved.write_text(json.dumps({'cookies': dict(client.cookies), 'event': event, 'task': task}))
            saved.chmod(0o600)
            print('Organizer seeded. Restart with the same state directory, then run --organizer-phase verify.')
        else:
            value = json.loads(saved.read_text())
            client.cookies.update(value['cookies'])
            agenda = await call('calendar.agenda', {'calendar_id': 'personal', 'date': '2030-01-02'})
            tasks = await call('task.list', {})
            assert value['event']['event_id'] in json.dumps(agenda)
            assert value['task']['task_id'] in json.dumps(tasks)
            await call('calendar.delete', {'event_id': value['event']['event_id']})
            await call('task.delete', {'task_id': value['task']['task_id']})
            saved.unlink()
            (output / 'organizer-result.json').write_text(json.dumps({'persisted_after_restart': True}))
            print('Calendar and task persisted across restart; acceptance items removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=23479)
    parser.add_argument('--audio', help='WAV containing real speech')
    parser.add_argument('--transport', choices=('upload', 'stream'), default='upload')
    parser.add_argument('--organizer-phase', choices=('seed', 'verify'))
    parser.add_argument('--output', default='state/acceptance')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    if not args.audio and not args.organizer_phase:
        parser.error('--audio or --organizer-phase is required')
    async def bounded_run():
        await asyncio.wait_for(run(args), timeout=args.timeout)
    asyncio.run(bounded_run())
