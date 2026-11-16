"""Capture the BF16 codec's one-token eager decoder on a fixed CUDA buffer."""
import time


def _dynamic_cache(cache, length, config):
    from transformers.cache_utils import DynamicCache
    result = DynamicCache(config=config)
    for index, layer in enumerate(cache.layers):
        result.update(layer.keys[:, :, :length], layer.values[:, :, :length], index)
    return result


def _copy_cache(target, source, length):
    for dest, original in zip(target.layers, source.layers):
        dest.keys[:, :, :length].copy_(original.keys)
        dest.values[:, :, :length].copy_(original.values)


class _CodecGraph:
    def __init__(self, model, capacity):
        import torch
        from transformers.cache_utils import StaticCache
        self.model, self.original = model, model.forward
        self.capacity, self.length = capacity, 0
        parameter = next(model.parameters())
        device, dtype = parameter.device, parameter.dtype
        if device.type != 'cuda' or dtype != torch.bfloat16 or model.config._attn_implementation != 'eager':
            raise ValueError('The codec graph requires the native CUDA BF16/eager decoder')
        self.proof = {'enabled': True, 'capacity': capacity, 'dtype': str(dtype), 'attention': 'eager',
                      'replay_calls': 0, 'prefill_calls': 0, 'fallback_calls': 0, 'overflow_count': 0}
        self.cache = StaticCache(model.config, max_cache_len=capacity)
        self.cache.early_initialization(1, model.config.num_key_value_heads,
            model.config.hidden_size // model.config.num_attention_heads, dtype, device)
        self.embeds = torch.zeros((1, 1, model.config.hidden_size), dtype=dtype, device=device)
        self.positions = torch.zeros(1, dtype=torch.long, device=device)
        self.indices = torch.arange(capacity, device=device)
        self.mask = torch.empty((1, 1, 1, capacity), dtype=dtype, device=device)
        self.graph = torch.cuda.CUDAGraph()

        def decode():
            self.mask.fill_(torch.finfo(dtype).min)
            self.mask.masked_fill_(self.indices <= self.positions[0], 0)
            return self.original(inputs_embeds=self.embeds, position_ids=self.positions[None],
                cache_position=self.positions, attention_mask=self.mask, past_key_values=self.cache,
                use_cache=True, return_dict=True).last_hidden_state

        began = time.perf_counter()
        current = torch.cuda.current_stream(device)
        stream = torch.cuda.Stream(device=device)
        stream.wait_stream(current)
        # Capture has its own cache and stream and leaves sampling RNG intact.
        with torch.random.fork_rng(devices=[device.index]), torch.cuda.stream(stream):
            for _ in range(3):
                decode()
            stream.synchronize()
            with torch.cuda.graph(self.graph, stream=stream):
                self.output = decode()
            self.cache.reset()
        current.wait_stream(stream)
        stream.synchronize()
        self.proof['capture_seconds'] = time.perf_counter() - began

    def __call__(self, *, inputs_embeds, position_ids, past_key_values=None, **kwargs):
        from transformers.modeling_outputs import BaseModelOutputWithPast
        count = inputs_embeds.shape[1]
        if past_key_values is None:
            self.cache.reset()
            self.length = 0
        elif past_key_values is not self.cache:
            # An overflow remains native until the next turn resets the cache.
            self.proof['fallback_calls'] += 1
            return self.original(inputs_embeds=inputs_embeds, position_ids=position_ids,
                                 past_key_values=past_key_values, **kwargs)
        if self.length + count > self.capacity:
            self.proof['overflow_count'] += 1
            self.proof['fallback_calls'] += 1
            past = _dynamic_cache(self.cache, self.length, self.model.config) if self.length else None
            return self.original(inputs_embeds=inputs_embeds, position_ids=position_ids,
                                 past_key_values=past, **kwargs)
        if count != 1:
            # Prefill retains its original attention shape; only decoding pads KV.
            past = _dynamic_cache(self.cache, self.length, self.model.config) if self.length else None
            result = self.original(inputs_embeds=inputs_embeds, position_ids=position_ids,
                                   past_key_values=past, **kwargs)
            self.length += count
            _copy_cache(self.cache, result.past_key_values, self.length)
            result.past_key_values = self.cache
            self.proof['prefill_calls'] += 1
            return result
        self.embeds.copy_(inputs_embeds)
        self.positions.copy_(position_ids[0])
        self.graph.replay()
        self.length += 1
        self.proof['replay_calls'] += 1
        return BaseModelOutputWithPast(last_hidden_state=self.output.clone(), past_key_values=self.cache)


def install(model, capacity=2048):
    """Install once on the codec LlamaModel; return live execution counters."""
    import torch
    if isinstance(model.forward, _CodecGraph):
        return model.forward.proof
    # PyTorch shares graph RNG bookkeeping across captures. Allocate it as
    # ordinary tensors even when the caller is already in inference mode.
    with torch.inference_mode(False), torch.no_grad():
        wrapper = _CodecGraph(model, capacity)
    model.forward = wrapper
    return wrapper.proof
