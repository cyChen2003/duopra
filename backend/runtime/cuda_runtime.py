"""NVIDIA CUDA runtime checks, device setup and model loading."""
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import threading

from deployment_model import ReleaseError

PROFILE = {
    'schema': 'duopra.nvidia.v1',
    'attention_backend': 'llm_math_canonical',
    'codec_attention_backend': 'eager',
    'allow_tf32': False,
    'audio_reference_normalization_reset': True,
}
CONTRACT = Path(__file__).resolve().parents[2] / 'deploy/nvidia/runtime-contract.json'


def validate_profile(profile):
    if (not isinstance(profile, dict) or profile != PROFILE
            or any(type(profile[k]) is not type(v) for k, v in PROFILE.items())):
        raise ReleaseError('NVIDIA profile must preserve canonical attention, precision and native framing')


def validate_device(torch):
    if not torch.version.cuda or getattr(torch.version, 'hip', None):
        raise ReleaseError('A genuine NVIDIA CUDA PyTorch build is required')
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ReleaseError('Expose exactly one NVIDIA GPU to this owner')
    capability = tuple(torch.cuda.get_device_capability(0))
    if capability < (8, 0) or not torch.cuda.is_bf16_supported():
        raise ReleaseError('This FP32/BF16 profile requires an Ampere-or-newer BF16-capable NVIDIA GPU')
    probe = torch.ones((2, 2), device='cuda', dtype=torch.bfloat16)
    result = probe @ probe
    torch.cuda.synchronize()
    if not torch.equal(result.float().cpu(), torch.full((2, 2), 2.0)):
        raise ReleaseError('CUDA BF16 device smoke check failed')
    return {'device_count': 1, 'compute_capability': list(capability), 'bf16_supported': True,
            'device_smoke_passed': True, 'vram_capacity_qualified': False}


def runtime_preflight(check_device=True, contract_path=None):
    if threading.current_thread() is not threading.main_thread():
        raise ReleaseError('Native initialization must run on the main thread')
    contract = json.loads(Path(contract_path or CONTRACT).read_text())
    if '.'.join(map(str, sys.version_info[:2])) != contract['python']:
        raise ReleaseError('CUDA image Python version differs from the declared runtime contract')
    versions = {}
    for name, expected in contract['packages'].items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ReleaseError('Missing CUDA runtime package: ' + name) from None
        if actual.split('+')[0] != expected:
            raise ReleaseError('CUDA runtime package version differs: ' + name)
        versions[name] = actual
    try:
        for name in contract['required_imports']:
            importlib.import_module(name)
        from stepaudio2 import Token2wav
        if not callable(Token2wav):
            raise ImportError('Token2wav constructor missing')
    except ImportError:
        raise ReleaseError('CUDA image lacks a compatible Audio/StepAudio/CosyVoice2 runtime') from None
    import torch
    if str(torch.version.cuda) != contract['cuda'] or getattr(torch.version, 'hip', None):
        raise ReleaseError('CUDA image must provide the declared NVIDIA CUDA build')
    proof = {'platform': 'nvidia-cuda', 'torch_version': torch.__version__,
             'cuda_version': torch.version.cuda, 'packages': versions,
             'container_image_id_inspected_in_process': False,
             'target_gpu_qualified': False, 'acoustic_acceptance_claimed': False}
    if check_device:
        proof.update(validate_device(torch))
    return proof


def load_for_serving(config, checkpoint):
    settings = config if isinstance(config, dict) else json.loads(Path(config).read_text())
    validate_profile(settings['nvidia_profile'])
    if settings.get('runtime_backend') != 'nvidia-cuda':
        raise ReleaseError('NVIDIA loader requires the NVIDIA runtime backend')
    runtime = runtime_preflight(check_device=True, contract_path=settings.get('runtime_contract'))
    from model_loader import load_model, validate_settings
    model_path = validate_settings(settings)
    import torch
    free, _ = torch.cuda.mem_get_info(0)
    required = sum(p.stat().st_size for p in model_path.glob('model-*.safetensors')) + 20 * 1024**3
    if free < required:
        raise ReleaseError(f'Insufficient free VRAM: {free / 1024**3:.1f} GiB; '
                           f'reserve at least {required / 1024**3:.1f} GiB for model and session')
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    return load_model(settings, runtime, model_path)
