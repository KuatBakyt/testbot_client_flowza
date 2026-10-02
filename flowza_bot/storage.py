import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (user_id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processed (id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        ''')
        self.db.commit()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def load(self, user_id):
        row = self.db.execute('SELECT body FROM sessions WHERE user_id=?', (user_id,)).fetchone()
        return json.loads(row[0]) if row else {'step': 'language', 'lang': None, 'fields': {}, 'revision': 0}

    def save(self, user_id, state):
        self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',
                        (user_id, json.dumps(state, ensure_ascii=False)))

    def meta(self, key, value=None):
        if value is not None:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def bind(self, bot_id, master_id):
        with self.transaction():
            identity = f'{bot_id}:{master_id}'
            previous = self.meta('identity')
            if previous and previous != identity:
                raise ValueError('State belongs to another bot/master. Choose a new BOT_STATE_PATH.')
            self.meta('identity', identity)

    def seen(self, update_id):
        return self.db.execute('SELECT 1 FROM processed WHERE id=?', (update_id,)).fetchone() is not None

    def acknowledge(self, update_id):
        self.db.execute('INSERT OR IGNORE INTO processed VALUES (?)', (update_id,))
        self.meta('offset', update_id + 1)
        # Telegram offsets, not session contents, are enough to reject old deliveries.
        self.db.execute('DELETE FROM processed WHERE id < ?', (update_id - 10000,))

    def enqueue(self, message):
        self.db.execute('INSERT INTO outbox(body) VALUES (?)', (json.dumps(message, ensure_ascii=False),))

    def pending(self):
        return [(i, json.loads(body)) for i, body in self.db.execute('SELECT id,body FROM outbox ORDER BY id')]

    def sent(self, row_id):
        self.db.execute('DELETE FROM outbox WHERE id=?', (row_id,))
        self.db.commit()

    def close(self):
        self.db.close()


class InstanceLock:
    """OS-released SQLite lock; no stale PID files after a crash."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) + '.lock.sqlite3', timeout=0)
        try:
            self.db.execute('BEGIN EXCLUSIVE')
        except sqlite3.OperationalError:
            self.db.close()
            raise ValueError('Another bot instance is using this BOT_STATE_PATH') from None

    def close(self):
        self.db.rollback()
        self.db.close()
