"""Capture the native FP32 flow decoder for one second of streaming speech."""
import time


class _CountedGraph:
    def __init__(self, graph, proof):
        self.graph = graph
        self.proof = proof

    def replay(self):
        self.graph.replay()
        self.proof['replay_calls'] += 1


def install(token2wav, max_cache_frames=500):
    """Add 50-frame capture to native dispatch; other shapes retain eager execution.

    Token2wav retains the reference prefix and 100 recent mel frames. The default
    capacity covers the bundled Duopra reference. Capturing uses the existing DiT
    weights and blocks_forward_chunk; it does not change the diffusion steps.
    """
    import torch

    previous = getattr(token2wav, '_duopra_waveform_graph', None)
    if previous is not None:
        if previous['max_cache_frames'] != max_cache_frames:
            raise ValueError('Waveform graph already captured with a different cache capacity')
        return previous
    estimator = token2wav.flow.decoder.estimator
    device = estimator.cnn_cache_buffer.device
    if device.type != 'cuda' or estimator.cnn_cache_buffer.dtype != torch.float32 or token2wav.float16:
        raise ValueError('Waveform graph requires the native FP32 CUDA flow decoder')
    if estimator.training:
        raise ValueError('Set the flow decoder to evaluation mode before capture')
    if max_cache_frames not in (500, 1000):
        raise ValueError('Waveform graph cache capacity must be 500 or 1000 mel frames')

    chunk = 50  # 25 codec tokens, upsampled by the native flow encoder.
    started = time.perf_counter()
    allocated_before = torch.cuda.memory_allocated(device)
    # Match the codec capture mode: the first graph creates shared CUDA RNG
    # bookkeeping that subsequent captures must be able to update.
    with (torch.inference_mode(False), torch.no_grad(), torch.cuda.device(device),
          torch.random.fork_rng(devices=[device.index])):
        # These are the native DiT graph layouts. The forward_chunk dispatcher
        # pads the cache/mask and slices the returned cache to its true length.
        x = torch.zeros((2, 320, chunk), dtype=torch.float32, device=device)
        timestep = torch.zeros((2, 1, 512), dtype=torch.float32, device=device)
        mask = torch.ones((2, chunk, max_cache_frames + chunk), dtype=torch.bool, device=device)
        att_cache = torch.zeros((16, 2, 8, max_cache_frames, 128), dtype=torch.float32, device=device)
        cnn_cache = torch.zeros((16, 2, 1024, 2), dtype=torch.float32, device=device)
        next_att_cache = torch.zeros((16, 2, 8, max_cache_frames + chunk, 128),
                                     dtype=torch.float32, device=device)
        next_cnn_cache = torch.zeros_like(cnn_cache)
        inputs = [x, timestep, mask, cnn_cache, att_cache]

        stream = torch.cuda.Stream(device=device)
        stream.wait_stream(torch.cuda.current_stream(device))
        with torch.cuda.stream(stream):
            for _ in range(3):
                estimator.blocks_forward_chunk(*inputs, next_cnn_cache, next_att_cache)
        stream.synchronize()
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, stream=stream):
            output = estimator.blocks_forward_chunk(*inputs, next_cnn_cache, next_att_cache)
        stream.synchronize()

    outputs = [output, next_cnn_cache, next_att_cache]
    proof = {
        'implementation': 'native_dit_cuda_graph',
        'dtype': 'float32', 'mel_frames': chunk, 'max_cache_frames': max_cache_frames,
        'diffusion_steps': token2wav.n_timesteps, 'replay_calls': 0,
        'capture_seconds': time.perf_counter() - started,
        'static_buffer_bytes': sum(t.numel() * t.element_size() for t in inputs + outputs),
        'allocated_bytes_added': torch.cuda.memory_allocated(device) - allocated_before,
        'other_shapes': 'native_eager',
    }
    estimator.max_size_chunk[chunk] = max_cache_frames
    estimator.inference_buffers_chunk[chunk] = {'static_inputs': inputs, 'static_outputs': outputs}
    estimator.graph_chunk[chunk] = _CountedGraph(graph, proof)
    estimator.use_cuda_graph = True
    token2wav._duopra_waveform_graph = proof
    return proof
