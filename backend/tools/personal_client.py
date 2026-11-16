"""Owner-scoped access to the configured local calendar and task database."""
import os

from personal_store import PersonalError

COOKIE_NAME = 'duplex_personal_workspace'
PERSONAL_TOOLS = frozenset({'calendar.agenda', 'calendar.create', 'calendar.update', 'calendar.delete', 'calendar.restore',
                            'task.list', 'task.create', 'task.update', 'task.delete', 'task.restore'})


def connection_mode():
    return 'local_private_store' if os.environ.get('DUPLEX_PERSONAL_DB') else 'disabled'


def rpc(path, body):
    database = os.environ.get('DUPLEX_PERSONAL_DB')
    if not database:
        raise PersonalError('personal_unavailable', 'Local organizer is not configured.')
    from local_organizer import rpc as local_rpc
    return local_rpc(path, body, database)


def execute(token, name, arguments, authorized, request_key=None):
    return rpc('execute', {'workspace_token': token, 'tool': name, 'arguments': arguments,
                           'authorized': authorized, 'request_key': request_key})
