import argparse
import json
import logging
import os
import time
from pathlib import Path

from .config import Config
from .crm import CRM
from .dialogue import Dialogue
from .http import HTTPError
from .storage import Store, InstanceLock
from .telegram import Telegram
from .notifications import MasterNotifications


def main():
    parser = argparse.ArgumentParser(description='Flowza Telegram bot — русский / қазақша')
    parser.add_argument('--env', default='.env', help='Path to .env')
    parser.add_argument('--check', action='store_true', help='Check Telegram/CRM without sending messages')
    parser.add_argument('--reset-webhook', action='store_true', help='Explicitly remove webhook without dropping pending updates')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    lock = store = None
    try:
        config = Config.load(args.env)
        telegram, crm = Telegram(config.token), CRM(config)
        identity = telegram.call('getMe')
        catalog = crm.catalog()
        crm.check_endpoint()
        webhook = telegram.call('getWebhookInfo')
        if webhook.get('url'):
            if args.reset_webhook:
                telegram.call('deleteWebhook', {'drop_pending_updates': False})
            else:
                raise ValueError('Webhook is enabled. Run with --reset-webhook to use polling.')
        print('Telegram OK; MASTER account OK; services OK; bot/orders endpoint OK.')
        if config.master_chat_id:
            try:
                recipient = telegram.call('getChat', {'chat_id': config.master_chat_id})
            except HTTPError as exc:
                if exc.status in (400, 403, 404):
                    raise ValueError('Master chat is unavailable: ask master to send /start and /myid to this bot, then check MASTER_TELEGRAM_CHAT_ID') from None
                raise
            if recipient.get('type') != 'private':
                raise ValueError('Master notifications require a private chat; ask master to send /start')
            print('Master Telegram chat OK; notification URL configured.')
        else:
            print('Master notifications disabled: set MASTER_TELEGRAM_CHAT_ID and CRM_WEB_URL.')
        if args.check:
            return 0
        labels = {}
        labels_path = os.environ.get('BOT_LABELS_PATH')
        # Keep optional labels alongside .env; .env parser returns only known config.
        if not labels_path and Path(args.env).exists():
            for line in Path(args.env).read_text(encoding='utf-8-sig').splitlines():
                if line.strip().startswith('BOT_LABELS_PATH='):
                    labels_path = line.split('=', 1)[1].strip().strip('\"\'')
        if labels_path:
            path = Path(labels_path)
            if not path.is_absolute():
                path = Path(args.env).resolve().parent / path
            labels = json.loads(path.read_text(encoding='utf-8'))
        lock = InstanceLock(config.state_path)
        store = Store(config.state_path)
        store.bind(identity['id'], catalog['master_id'])
        engine = Dialogue(store, crm, config, identity['id'], labels=labels)
        notifications = MasterNotifications(store, crm, config)
        print('Бот запущен / Бот іске қосылды. Stop: Ctrl+C.')
        delay = 1
        while True:
            try:
                notifications.poll()
                telegram.drain(store)
                updates = telegram.updates(int(store.meta('offset') or 0))
                for update in updates:
                    engine.process(update)
                notifications.poll()
                telegram.drain(store)
                delay = 1
            except HTTPError as exc:
                logging.warning('Connection status=%s; retrying', exc.status)
                if exc.status in (401, 409):
                    raise ValueError('Telegram token rejected or another poller/webhook is active') from None
                retry_after = exc.data.get('parameters', {}).get('retry_after', delay)
                time.sleep(min(max(retry_after, delay), 30))
                delay = min(delay * 2, 30)
    except KeyboardInterrupt:
        print('\nБот остановлен / Бот тоқтатылды.')
        return 0
    except HTTPError as exc:
        print(f'Connection check failed (status {exc.status}). Check token, CRM, credentials and API module.')
        return 1
    except (ValueError, OSError) as exc:
        # OSError may contain paths, never credentials; no HTTP exception URLs are printed.
        print(str(exc) if isinstance(exc, ValueError) else 'Cannot open configuration/state files')
        return 1
    finally:
        if store:
            store.close()
        if lock:
            lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
