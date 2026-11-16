"""Bound compressed uploads before decoding and outside the event loop."""
import io

MAX_BYTES = 10 * 1024**2
MAX_SECONDS = 300


def decode_audio(raw):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd
    if len(raw) > MAX_BYTES:
        raise ValueError('Audio upload exceeds 10 MiB')
    with sf.SoundFile(io.BytesIO(raw)) as source:
        if (not 8000 <= source.samplerate <= 96000 or source.channels not in (1, 2)
                or not 0 < len(source) <= MAX_SECONDS * source.samplerate):
            raise ValueError('Audio must be mono/stereo, 8–96 kHz, and at most 300 seconds')
        sr = source.samplerate
        wave = source.read(dtype='float32', always_2d=True).mean(axis=1)
    if not np.isfinite(wave).all():
        raise ValueError('Non-finite audio')
    if sr != 16000:
        common = gcd(sr, 16000)
        wave = resample_poly(wave, 16000 // common, sr // common)
    return wave
