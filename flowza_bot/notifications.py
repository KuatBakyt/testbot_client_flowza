import logging
from datetime import datetime, timezone
from uuid import UUID
from .http import HTTPError

LOG = logging.getLogger(__name__)


class MasterNotifications:
    def __init__(self, store, crm, config, now=None):
        self.store, self.crm, self.config = store, crm, config
        self.now = now or (lambda: datetime.now(timezone.utc))
        if config.master_chat_id and not store.meta('master_notifications_since'):
            with store.transaction():
                store.meta('master_notifications_since', self.now().isoformat())

    def poll(self):
        if not self.config.master_chat_id:
            return
        try:
            self._poll()
        except HTTPError as exc:
            LOG.warning('Master notifications: CRM status=%s; retry next cycle', exc.status)

    def _poll(self):
        since = datetime.fromisoformat(self.store.meta('master_notifications_since'))
        for page in range(1, 101):
            data = self.crm.call('GET', f'notifications/?page={page}')
            rows = data if isinstance(data, list) else data['results']
            stop = False
            for row in rows:
                created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
                if created < since:
                    stop = True
                    continue
                identifier = row['id']
                if self.store.db.execute('SELECT 1 FROM master_notifications WHERE id=?', (identifier,)).fetchone():
                    continue
                message = None
                if row['type'] in {'BOT_ORDER', 'ORDER_NEW', 'ORDER_REMINDER'}:
                    order_id = row.get('payload', {}).get('order_id')
                    try:
                        UUID(str(order_id))
                    except (ValueError, TypeError):
                        continue
                    link = f'{self.config.web_url}/#/orders/{order_id}'
                    title = 'Напоминание о заявке в CRM' if row['type'] == 'ORDER_REMINDER' else 'Новая заявка в CRM'
                    payload = {'chat_id': self.config.master_chat_id, 'text': title + '\n' + link}
                    if not self.config.web_url.startswith(('http://localhost', 'http://127.0.0.1')):
                        payload['reply_markup'] = {'inline_keyboard': [[{'text': 'Открыть заявку в CRM', 'url': link}]]}
                    message = {'method': 'sendMessage', 'payload': payload, 'kind': 'master_notice'}
                with self.store.transaction():
                    if message:
                        self.store.enqueue(message)
                    self.store.db.execute('INSERT OR IGNORE INTO master_notifications VALUES (?)', (identifier,))
            if stop or isinstance(data, list) or not data.get('next'):
                break
