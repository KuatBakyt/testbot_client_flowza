from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4
from flowza_bot.config import Config
from flowza_bot.dialogue import Dialogue
from flowza_bot.storage import Store

NOW = datetime(2026, 10, 2, 5, tzinfo=timezone.utc)
SPEC = 'd348d0fa-0f10-48d3-9edb-16700325948b'


class FakeCRM:
    def __init__(self):
        self.requests = []; self.receipts = {}; self.error = None; self.catalog_error = None
    def catalog(self):
        if self.catalog_error:
            raise self.catalog_error
        return {'master_id': 'master', 'specs': [{'id': SPEC, 'name': 'Сантехника'}],
                'districts': ['Бостандыкский'], 'city': 'Алматы'}
    def submit(self, payload):
        self.requests.append(payload)
        if self.error:
            raise self.error
        return self.receipts.setdefault(payload['request_id'],
                    {'id': str(uuid4()), 'client_id': str(uuid4()), 'status': 'NEW', 'replayed': False})


class Harness:
    def __init__(self, crm=None):
        self.temp = TemporaryDirectory(); self.path = self.temp.name + '/state.sqlite3'
        self.store = Store(self.path); self.crm = crm or FakeCRM()
        self.config = Config('token', 'http://localhost/api/v1/', 'phone', 'password')
        self.engine = Dialogue(self.store, self.crm, self.config, 123, now=lambda: NOW)
        self.update_id = 1
    def text(self, text, user=100, contact=None, extra=None):
        self.engine.process({'update_id': self.update_id, 'message': {
            'from': {'id': user}, 'chat': {'id': user, 'type': 'private'},
            **({'text': text} if text is not None else {}), **({'contact': contact} if contact else {}), **(extra or {})}})
        self.update_id += 1
    def click(self, action, user=100, revision=None):
        state = self.store.load(user)
        update = {'update_id': self.update_id, 'callback_query': {'id': str(self.update_id),
            'from': {'id': user}, 'data': f"{revision if revision is not None else state['revision']}|{action}",
            'message': {'chat': {'id': user, 'type': 'private'}}}}
        self.engine.process(update); self.update_id += 1
        return update
    def state(self, user=100):return self.store.load(user)
    def form(self, lang='ru', user=100):
        self.text('/start', user); self.click('lang:' + lang, user); self.click('continue', user)
        self.text('Әлия', user); self.text('+7 (701) 234-56-78', user); self.click('service:0', user)
        self.text('Кран ағып тұр', user); self.text('Алматы, Абая 10', user); self.click('district:0', user)
        self.text('05.10.2026 10:00', user); self.click('duration:1', user)
    def close(self):self.store.close();self.temp.cleanup()
