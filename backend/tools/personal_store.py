"""Owner-scoped local calendar/tasks. No external calendar or account side effects."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from zoneinfo import ZoneInfo


class PersonalError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class PersonalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS owners(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS items(owner TEXT, kind TEXT, id TEXT PRIMARY KEY,
                    value TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL,
                    updated REAL NOT NULL, FOREIGN KEY(owner) REFERENCES owners(id));
                CREATE INDEX IF NOT EXISTS by_owner_kind ON items(owner,kind,deleted);
                CREATE TABLE IF NOT EXISTS requests(owner TEXT, key TEXT, signature TEXT NOT NULL,
                    response TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(owner,key));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def owner(self, db, token):
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
            raise PersonalError('personal_identity_required', 'Open the local organizer to initialize your private workspace.')
        row = db.execute('SELECT id FROM owners WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None:
            raise PersonalError('personal_identity_required', 'Local workspace identity is not recognized.')
        return row['id']

    def bootstrap(self, token=None):
        with self.connection() as db:
            if token:
                try:
                    self.owner(db, token)
                    return {'workspace_token': token, 'timezone': 'Asia/Shanghai', 'source': 'local_personal_store'}
                except PersonalError:
                    pass
            token = secrets.token_hex(32)
            db.execute('INSERT INTO owners VALUES(?,?)', (secrets.token_hex(16), hashlib.sha256(token.encode()).hexdigest()))
        return {'workspace_token': token, 'timezone': 'Asia/Shanghai', 'source': 'local_personal_store'}

    def timestamp(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise PersonalError('invalid_datetime', 'Use an explicit ISO datetime with timezone, e.g.2026-10-28T14:00+08:00.')
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error:
            raise PersonalError('invalid_datetime', 'The date or time does not exist.') from error

    def check_value(self, kind, value):
        if not isinstance(value.get('title'), str) or not value['title'].strip() or len(value['title']) > 160:
            raise PersonalError('invalid_title', 'A nonempty title up to160 characters is required.')
        for field in ['notes', 'location', 'calendar_id']:
            if field in value and (not isinstance(value[field], str) or len(value[field]) > 500 or any(ord(c)<32 and not(field=='notes' and c in '\r\n') for c in value[field])):
                raise PersonalError('invalid_text', field+' has invalid text.')
        if kind == 'calendar':
            start, end = self.timestamp(value['start']), self.timestamp(value['end'])
            if end <= start:
                raise PersonalError('invalid_time_range', 'Event end must follow event start.')
        else:
            if value.get('due'):
                self.timestamp(value['due'])
            if type(value.get('completed', False)) is not bool:
                raise PersonalError('invalid_completed', 'completed must be a boolean.')

    def execute(self, token, tool, args, *, authorized=False, request_key=None):
        if authorized is not True:
            raise PersonalError('authorization_required', 'Allow local personal tools before model access.')
        if not isinstance(args, dict):
            raise PersonalError('invalid_arguments', 'arguments must be an object.')
        kind = 'calendar' if tool.startswith('calendar.') else 'task'
        if tool not in {'calendar.agenda', 'calendar.create', 'calendar.update', 'calendar.delete', 'calendar.restore',
                        'task.list', 'task.create', 'task.update', 'task.delete', 'task.restore'}:
            raise PersonalError('unknown_tool', 'Unsupported local personal tool.')
        read = tool in {'calendar.agenda', 'task.list'}
        signature = hashlib.sha256(canonical([tool, args]).encode()).hexdigest()
        if request_key is not None and (not isinstance(request_key, str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}', request_key)):
            raise PersonalError('invalid_request_key', 'Invalid mutation identity.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE' if not read else 'BEGIN')
            owner = self.owner(db, token)
            if not read and request_key:
                previous = db.execute('SELECT * FROM requests WHERE owner=? AND key=?', (owner, request_key)).fetchone()
                if previous:
                    if previous['signature'] != signature:
                        raise PersonalError('idempotency_conflict', 'Request identity was reused with different arguments.')
                    return json.loads(previous['response'])
            if read:
                records = db.execute('SELECT * FROM items WHERE owner=? AND kind=? AND deleted=0 ORDER BY updated DESC', (owner, kind)).fetchall()
                items = [dict(json.loads(row['value']), revision=row['revision']) for row in records]
                if kind == 'calendar':
                    try:
                        day = datetime.strptime(args['date'], '%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                        if day.strftime('%Y-%m-%d') != args['date']:
                            raise ValueError()
                    except (ValueError, KeyError, TypeError) as error:
                        raise PersonalError('invalid_date', 'Use a valid YYYY-MM-DD date.') from error
                    items = [item for item in items if item['calendar_id'] == args['calendar_id'] and
                             self.timestamp(item['start']) < day+timedelta(days=1) and self.timestamp(item['end']) > day]
                    items.sort(key=lambda item: self.timestamp(item['start']))
                    if len(items) > 100:
                        raise PersonalError('result_limit', 'More than100 events; narrow the requested calendar.')
                    return {'title': items[0]['title'] if items else '暂无日程',
                            'starts': items[0]['start'] if items else '', 'events': items,
                            'count': len(items), 'date': args['date'], 'timezone': 'Asia/Shanghai'}
                status = args.get('status', 'all')
                items = [item for item in items if status == 'all' or item['completed'] == (status == 'done')]
                return {'items': items[:100], 'count': min(len(items), 100), 'has_more': len(items)>100}
            op = tool.split('.')[1]
            key = 'event_id' if kind == 'calendar' else 'task_id'
            if op == 'create':
                if db.execute('SELECT COUNT(*) FROM items WHERE owner=?', (owner,)).fetchone()[0] >= 5000:
                    raise PersonalError('personal_capacity', 'Local workspace has reached5000 records.')
                value = ({'calendar_id': 'personal', 'location': '', 'notes': ''} if kind == 'calendar'
                         else {'due': '', 'completed': False, 'notes': ''})
                value.update(args)
                value[key] = ('ev_' if kind == 'calendar' else 'task_')+secrets.token_hex(8)
                self.check_value(kind, value)
                revision = 1
                db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?)', (owner, kind, value[key], canonical(value), 0, revision, time.time()))
                state = 'created'
            else:
                row = db.execute('SELECT * FROM items WHERE owner=? AND kind=? AND id=?', (owner, kind, args.get(key))).fetchone()
                if row is None:
                    raise PersonalError('personal_not_found', 'This item does not exist in your local workspace.')
                if 'expected_revision' in args and args['expected_revision'] != row['revision']:
                    raise PersonalError('revision_conflict', 'The item has changed; refresh before editing.')
                value = json.loads(row['value'])
                if op == 'update':
                    if row['deleted']:
                        raise PersonalError('personal_not_found', 'Restore the deleted item before editing.')
                    changes = {k:v for k,v in args.items() if k not in {key, 'expected_revision'}}
                    if not changes:
                        raise PersonalError('empty_update', 'Specify at least one field to change.')
                    value.update(changes)
                    self.check_value(kind, value)
                deleted = int(op == 'delete') if op in {'delete', 'restore'} else row['deleted']
                revision = row['revision']+1
                db.execute('UPDATE items SET value=?,deleted=?,revision=?,updated=? WHERE owner=? AND id=?',
                           (canonical(value), deleted, revision, time.time(), owner, value[key]))
                state = {'update': 'updated', 'delete': 'deleted', 'restore': 'restored'}[op]
            result = {'item': dict(value, revision=revision), 'state': state, 'undo_available': op == 'delete'}
            if request_key:
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?)', (owner, request_key, signature, canonical(result), time.time()))
                db.execute('DELETE FROM requests WHERE owner=? AND key NOT IN (SELECT key FROM requests WHERE owner=? ORDER BY created DESC LIMIT 1024)', (owner, owner))
            return result
