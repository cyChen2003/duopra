"""Native Qwen decoder state with one optional last-position vocabulary projection."""
import types


def install(decoder):
    """Keep the trained FP32 backbone/cache while avoiding unused CausalLM logits."""
    import torch

    @torch.no_grad()
    def feed(self, embeds, return_logits=False):
        embeds = embeds.float()
        length = embeds.size(0)
        past_length = self.get_cache_length()
        positions = torch.arange(past_length, past_length + length, device=embeds.device).unsqueeze(0)
        output = self.m.model(inputs_embeds=embeds.unsqueeze(0), position_ids=positions,
                              past_key_values=self.cache, return_dict=True, output_hidden_states=False)
        self.cache = output.past_key_values
        if return_logits:
            hidden = output.last_hidden_state
            return self.m.lm_head(hidden[:, -1:])[:, 0], hidden

    decoder.feed = types.MethodType(feed, decoder)
    return {'backbone_dtype': 'float32', 'vocabulary_projection': 'last_position_when_requested'}


def _apply_repetition_penalty(logits, tokens, penalty, window_size):
    """Apply the native penalty once to each recent vocabulary token."""
    import torch

    if penalty == 1.0 or not tokens:
        return
    recent = [token for token in set(tokens[-window_size:]) if token < logits.size(-1)]
    if not recent:
        return
    indices = torch.tensor(recent, device=logits.device)
    values = logits[0, indices]
    if penalty > 1.0:
        values /= penalty
    else:
        values *= 1.0 / penalty
    logits[0, indices] = values


def install_sampling(decoder):
    """Batch repetition penalties without changing native sampling decisions."""
    import inspect
    import torch
    import torch.nn.functional as F

    filter_logits = inspect.unwrap(decoder.decode).__globals__['top_k_top_p_filtering']

    @torch.no_grad()
    def decode(self, logits, mode='sampling', temperature=0.7, top_k=20, top_p=0.8,
               listen_top_k=None, listen_prob_scale=1.0, text_repetition_penalty=1.05,
               text_repetition_window_size=512):
        logits = logits.clone()
        if mode == 'greedy':
            sampled = torch.argmax(logits[0]).item()
        else:
            sampled = torch.multinomial(F.softmax(logits[0], dim=-1), num_samples=1).item()
        if sampled == self.chunk_eos_id:
            result = torch.tensor([self.chunk_eos_id], device=logits.device)
            self.tokenizer.decode(result)
            return result

        if self.forbidden_token_ids:
            logits[:, self.forbidden_token_ids] = float('-inf')
        _apply_repetition_penalty(logits, self.generated_tokens,
                                  text_repetition_penalty, text_repetition_window_size)
        if listen_prob_scale != 1.0:
            logits[0, self.listen_id] *= listen_prob_scale
        listen_rank = (logits[0] > logits[0, self.listen_id]).sum().item()
        if listen_top_k is not None and listen_rank < listen_top_k:
            result = torch.tensor([self.listen_id], device=logits.device)
            text = self.tokenizer.decode(result)
            self.context += ' ' if text == '<|listen|>' else text
            return result

        if mode == 'greedy':
            result = torch.argmax(logits, dim=-1)
        elif mode == 'sampling':
            logits = filter_logits(logits / temperature, top_k=top_k, top_p=top_p)
            result = torch.multinomial(F.softmax(logits, dim=-1), num_samples=1).squeeze(1)
        else:
            raise ValueError(f'Unsupported decode mode: {mode}')
        token = result.item()
        history = (self.generated_special_tokens if token in self.special_token_ids
                   else self.generated_tokens)
        history.append(token)
        return result

    decoder.decode = types.MethodType(decode, decoder)
    return {'repetition_penalty': 'batched_unique_recent_tokens'}
