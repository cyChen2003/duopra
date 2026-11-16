"""Shared local/container configuration and single-owner server startup."""
import json
import os
from pathlib import Path

from deployment_model import serving_config
from cuda_runtime import runtime_preflight
from standalone_model import load_inventory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configuration(model, inventory):
    # The loader hashes the complete bundle immediately before executing its code.
    release = json.loads(Path(inventory).read_text())
    profile = PROJECT_ROOT / 'deploy/nvidia/profile.json'
    config = serving_config(release, model, profile)
    config['standalone_inventory'] = str(Path(inventory).resolve())
    config['runtime_contract'] = str(PROJECT_ROOT / 'deploy/nvidia/runtime-contract.json')
    return config


def check(config):
    release = load_inventory(config['standalone_inventory'], config['model'])
    proof = runtime_preflight(contract_path=config.get('runtime_contract'))
    return {'release_valid': True, 'update': release['update'], 'runtime': proof, 'model_loaded': False}


def serve(config, state, host, port, origin, access=None, development=False):
    from server import NativeOwner, create_app
    from security import PublicAccess
    PublicAccess(None, origin, access, development=development)
    os.umask(0o077)
    state = Path(state).resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ['DUPLEX_RUN_DIR'] = str(state)
    generated = state / 'serving.json'
    generated.write_text(json.dumps(config))
    owner = NativeOwner(str(generated), 'Duopra')
    try:
        print(f"Loading Duopra on {config['runtime_backend']} and qualifying native speech boundaries...", flush=True)
        owner.load()
        import torch
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        owner.proof['cuda_memory'] = {
            'allocated_bytes': torch.cuda.memory_allocated(),
            'reserved_bytes': torch.cuda.memory_reserved(),
            'startup_peak_allocated_bytes': torch.cuda.max_memory_allocated(),
            'startup_peak_reserved_bytes': torch.cuda.max_memory_reserved(),
        }
        (state / 'runtime-proof.json').write_text(json.dumps(owner.proof, ensure_ascii=False, indent=2))
        app = create_app(str(generated), owner.checkpoint, state, preloaded_owner=owner)
        import uvicorn
        uvicorn.run(PublicAccess(app, origin, access, development=development),
                    host=host, port=port, workers=1, access_log=False, proxy_headers=False,
                    ws_max_size=200000, timeout_graceful_shutdown=20)
    finally:
        owner.pool.shutdown(wait=True, cancel_futures=True)
