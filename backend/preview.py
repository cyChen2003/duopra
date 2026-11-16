"""Loopback-only UI preview; explicitly not a speech model or acceptance gate."""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / part) for part in ('web', 'tools')]
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from harness_backend import install_routes
from tool_runtime import Catalog
from security import PublicAccess


def create_preview(port=23479):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    static = ROOT.parent / 'frontend/static'
    catalog = Catalog()
    app.mount('/static', StaticFiles(directory=static), name='static')
    install_routes(app, SimpleNamespace(active=None), catalog)

    @app.get('/')
    async def index():
        return FileResponse(static / 'index.html')

    @app.get('/healthz')
    @app.get('/health')
    async def health():
        return {'ready': False, 'preview_available': True, 'model_loaded': False}

    @app.get('/api/config')
    @app.get('/api/status')
    async def config():
        return {'configured': False, 'model': 'UI preview — no model', 'model_type': 'audio',
                'input_modes': ['audio', 'audio_file'], 'active_sessions': 0,
                'default_hot_tools': ['alert.bulletin', 'bike.docks', 'weather.day', 'timezone.offset'],
                'tool_configuration': {'maximum_hot_tools': 8, 'before_session': True, 'explicit_empty_supported': True},
                'preview_only': True}

    @app.get('/api/model-proof')
    async def proof():
        return {'ready': False, 'proof': {'model_variant': 'UI preview — no model'}, 'busy': False}

    @app.get('/api/tools')
    async def tools():
        return {'tools': catalog.cards(), 'registry_sha256': catalog.sha256}

    @app.get('/api/personal/profile')
    async def personal():
        return {'available': False, 'reason': 'Local organizer disabled in UI preview'}

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({'type': 'fatal_error', 'code': 'preview_only', 'error': 'This preview has no speech model.'})
        await ws.close(code=1008)

    return PublicAccess(app, f'http://127.0.0.1:{port}', development=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=23479)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_preview(args.port), host='localhost', port=args.port, access_log=False)
