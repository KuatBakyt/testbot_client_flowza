import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Config:
    token: str
    api_url: str
    phone: str
    password: str
    timezone: str = 'Asia/Almaty'
    state_path: str = 'data/bot.sqlite3'
    business: str = 'Flowza'
    master_chat_id: int | None = None
    web_url: str = ''

    @classmethod
    def load(cls, path='.env'):
        values = {}
        file = Path(path)
        if file.exists():
            for line in file.read_text(encoding='utf-8-sig').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    raise ValueError('Invalid .env line: expected KEY=VALUE')
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
        values.update(os.environ)
        required = ['TELEGRAM_BOT_TOKEN', 'CRM_API_URL', 'CRM_PHONE', 'CRM_PASSWORD']
        if any(not values.get(k) or values[k].startswith('YOUR_') for k in required):
            raise ValueError('Fill TELEGRAM_BOT_TOKEN, CRM_API_URL, CRM_PHONE and CRM_PASSWORD in .env')
        url = values['CRM_API_URL'].rstrip('/') + '/'
        parts = urlsplit(url)
        local = parts.hostname in {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('CRM_API_URL must be an HTTP(S) API URL without credentials or query')
        if parts.scheme != 'https' and not local:
            raise ValueError('Use HTTPS for a remote CRM')
        tz = values.get('BOT_TIMEZONE', 'Asia/Almaty')
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            raise ValueError('Unknown BOT_TIMEZONE; install requirements.txt') from None
        chat = values.get('MASTER_TELEGRAM_CHAT_ID', '').strip()
        web = values.get('CRM_WEB_URL', '').strip().rstrip('/')
        if bool(chat) != bool(web):
            raise ValueError('Set both MASTER_TELEGRAM_CHAT_ID and CRM_WEB_URL to enable notifications')
        if chat and (not chat.isdecimal() or int(chat) <= 0):
            raise ValueError('MASTER_TELEGRAM_CHAT_ID must be a positive private chat ID; use /myid')
        if web:
            target = urlsplit(web)
            local_web = target.hostname in {'localhost', '127.0.0.1', '::1'}
            if not target.hostname or target.username or target.password or target.query or target.fragment or target.scheme not in {'http', 'https'} or (target.scheme != 'https' and not local_web):
                raise ValueError('CRM_WEB_URL must be an HTTPS website URL (local HTTP is allowed for testing)')
        state = Path(values.get('BOT_STATE_PATH', 'data/bot.sqlite3'))
        if not state.is_absolute():
            state = file.resolve().parent / state
        return cls(values['TELEGRAM_BOT_TOKEN'], url, values['CRM_PHONE'], values['CRM_PASSWORD'],
                   tz, str(state), values.get('BOT_BUSINESS_NAME', 'Flowza'), int(chat) if chat else None, web)
