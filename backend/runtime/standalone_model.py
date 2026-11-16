"""Load an inventoried, already merged model without relabelling its update."""
import json
from pathlib import Path

from deployment_model import ReleaseError, digest


def load_inventory(path, model_root):
    root = Path(model_root).resolve(strict=True)
    manifest = json.loads(Path(path).read_text())
    if manifest.get('schema') != 'duopra.standalone.v1':
        raise ReleaseError('Unsupported standalone inventory')
    if type(manifest.get('update')) is not int or manifest['update'] != 420:
        raise ReleaseError('This deployment requires the Duopra standalone model')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    files = manifest['files']
    if set(files) != actual or any(p.is_symlink() for p in root.rglob('*')):
        raise ReleaseError('Standalone model inventory differs')
    for name, checksum in files.items():
        p = (root / name).resolve()
        if not p.is_relative_to(root) or digest(p) != checksum:
            raise ReleaseError('Standalone artifact checksum mismatch: ' + name)
    provenance = json.loads((root / 'PROVENANCE.json').read_text())
    if (not provenance.get('standalone') or provenance.get('runtime_overlay_required') is not False
            or provenance['grpo_update'] != manifest['update']
            or provenance['actor_sha256'] != manifest['actor_sha256']):
        raise ReleaseError('Standalone provenance differs from pinned inventory')
    return manifest


def restore_fp32(model, settings):
    """Recover original FP32 bytes after Transformers' uniform BF16 loading."""
    import torch
    from safetensors import safe_open
    root = Path(settings['model'])
    index = json.loads((root / 'model.safetensors.index.json').read_text())['weight_map']
    expected = {n: p for n, p in model.named_parameters()
                if n.startswith(('llm.model.layers.', 'llm.model.norm.', 'tts.projector_semantic.'))}
    backbone = {n for n in expected if n.startswith('llm.')}
    projector = set(expected) - backbone
    if len(backbone) != 397 or len(projector) != 4:
        raise ReleaseError('Unexpected standalone Backbone/projector topology')
    if sum(expected[n].numel() for n in projector) != 3737088:
        raise ReleaseError('Unexpected standalone projector shape')
    seen = set()
    with torch.no_grad():
        for shard in sorted({index[n] for n in expected}):
            with safe_open(root / shard, framework='pt', device='cpu') as weights:
                for name in sorted(set(weights.keys()) & set(expected)):
                    value, target = weights.get_tensor(name), expected[name]
                    if (value.dtype != torch.float32 or target.dtype != torch.float32
                            or value.shape != target.shape or not torch.isfinite(value).all()):
                        raise ReleaseError('Invalid standalone FP32 tensor: ' + name)
                    target.copy_(value.to(target.device))
                    if not torch.equal(target.cpu(), value):
                        raise ReleaseError('Standalone GPU tensor differs: ' + name)
                    seen.add(name)
    if seen != set(expected):
        raise ReleaseError('Incomplete standalone FP32 restore')
    identity = {'loaded': True, 'update': settings['release']['update'],
                'actor_sha256': settings['release']['actor_sha256'], 'dtype': 'float32',
                'source': 'inventoried_standalone', 'publisher_authenticity_verified': False}
    return (dict(identity, tensor_count=397),
            dict(identity, tensor_count=4, parameter_count=3737088,
                 formal_acoustic_acceptance_claimed=False))
