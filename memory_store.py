"""Local user-scoped memory and durable conversation state (not authentication)."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


class MemoryStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (
            user TEXT, session TEXT, payload TEXT, PRIMARY KEY(user, session));
        CREATE TABLE IF NOT EXISTS memories (
            user TEXT, name TEXT, value TEXT, updated TEXT, PRIMARY KEY(user, name));
        ''')

    def load(self, user, session):
        row = self.db.execute('SELECT payload FROM sessions WHERE user=? AND session=?', (user, session)).fetchone()
        return json.loads(row[0]) if row else {'turns': [], 'task': None}

    def save(self, user, session, state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',
                            (user, session, json.dumps(state, ensure_ascii=False)))

    def memories(self, user):
        return [{'name': n, 'value': v, 'updated': t} for n, v, t in
                self.db.execute('SELECT name,value,updated FROM memories WHERE user=? ORDER BY name', (user,))]

    def remember(self, user, name, value):
        if not name.strip() or len(name) > 80 or not value.strip() or len(value) > 2000:
            raise ValueError('记忆名称需为 1–80 字符，内容需为 1–2000 字符')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO memories VALUES(?,?,?,?)',
                            (user, name.strip(), value.strip(), datetime.now(timezone.utc).isoformat()))

    def forget(self, user, name):
        with self.db:
            self.db.execute('DELETE FROM memories WHERE user=? AND name=?', (user, name))

    def reset(self, user, session):
        with self.db:
            self.db.execute('DELETE FROM sessions WHERE user=? AND session=?', (user, session))

    def close(self):
        self.db.close()
