"""Coordinator-local organizer adapter; no unauthenticated internal listener."""
from functools import lru_cache
from pathlib import Path

from personal_store import PersonalError, PersonalStore


@lru_cache(maxsize=1)
def store(path):
    location = Path(path)
    if not location.is_absolute() or location.is_symlink():
        raise PersonalError('personal_unavailable', 'A private absolute database path is required.')
    location.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return PersonalStore(location)


def rpc(path, body, database):
    local = store(database)
    if path == 'bootstrap':
        return local.bootstrap(body.get('workspace_token'))
    if path != 'execute':
        raise PersonalError('unknown_operation', 'Unsupported local operation.')
    from tool_runtime import Catalog, validate
    tool = Catalog().tools.get(body.get('tool'))
    if tool is None:
        raise PersonalError('unknown_tool', 'Unknown personal tool.')
    validate(body.get('arguments'), tool['parameters'])
    return local.execute(body.get('workspace_token'), body['tool'], body['arguments'],
                         authorized=body.get('authorized'), request_key=body.get('request_key'))
