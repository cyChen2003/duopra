"""Legacy model cache views over Transformers 4.57 DynamicLayer storage.

Installed when loading the Duopra bundle on NVIDIA CUDA. Tensor storage and update math stay native.
"""
from collections.abc import Sequence
import functools


class _TensorView(Sequence):
    def __init__(self, cache, field):
        self.cache, self.field = cache, field

    def _layers(self):
        return [layer for layer in self.cache.layers if layer.is_initialized]

    def __len__(self):
        return len(self._layers())

    def __getitem__(self, index):
        layers = self._layers()
        if isinstance(index, slice):
            return [getattr(layer, self.field) for layer in layers[index]]
        return getattr(layers[index], self.field)

    def __setitem__(self, index, value):
        setattr(self._layers()[index], self.field, value)


def install():
    from transformers.cache_utils import DynamicCache, DynamicLayer
    if hasattr(DynamicCache, 'key_cache'):
        return

    def field_property(field):
        def read(cache):
            return _TensorView(cache, field)

        def write(cache, tensors):
            tensors = list(tensors)
            while len(cache.layers) < len(tensors):
                cache.layers.append(DynamicLayer())
            del cache.layers[len(tensors):]
            for layer, tensor in zip(cache.layers, tensors):
                if not layer.is_initialized:
                    layer.lazy_initialization(tensor)
                setattr(layer, field, tensor)
        return property(read, write)

    DynamicCache.key_cache = field_property('keys')
    DynamicCache.value_cache = field_property('values')
    DynamicCache.seen_tokens = property(lambda cache: cache.get_seq_length())
    DynamicCache.get_usable_length = lambda cache, new_seq_length, layer_idx=0: cache.get_seq_length(layer_idx)


def install_audio_attention(model):
    """Restore the old three-result contract at the custom encoder boundary."""
    for layer in model.apm.layers:
        attention = layer.self_attn
        if getattr(attention, '_duopra_legacy_return', False):
            continue
        original = attention.forward

        def wrap(forward):
            @functools.wraps(forward)
            def legacy(*args, **kwargs):
                cache = kwargs.pop('past_key_value', kwargs.get('past_key_values'))
                kwargs['past_key_values'] = cache
                result = forward(*args, **kwargs)
                # This custom module is an encoder with self attention only.
                # Returning an EncoderDecoderCache exposes an uninitialized
                # cross-attention half through legacy tuple indexing.
                encoder_cache = getattr(cache, 'self_attention_cache', cache)
                return (*result, encoder_cache) if len(result) == 2 else result
            return legacy
        attention.forward = wrap(original)
        attention._duopra_legacy_return = True
