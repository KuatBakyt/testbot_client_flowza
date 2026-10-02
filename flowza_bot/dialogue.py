import copy
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from .http import HTTPError
from .i18n import tr, label

STEPS = ['consent', 'name', 'phone', 'service', 'description', 'address', 'district', 'date', 'duration', 'review']
FIELDS = ['name', 'phone', 'service', 'description', 'address', 'district', 'date', 'duration']
LIMITS = {'name': 150, 'description': 2000, 'address': 255, 'district': 100}
LOG = logging.getLogger(__name__)
KAZAKH_DISTRICTS = {'Алатауский': 'Алатау', 'Алмалинский': 'Алмалы', 'Ауэзовский': 'Әуезов', 'Бостандыкский': 'Бостандық', 'Жетысуский': 'Жетісу', 'Медеуский': 'Медеу', 'Наурызбайский': 'Наурызбай', 'Турксибский': 'Түрксіб'}


def normalize_phone(value):
    digits = re.sub(r'[\s()+-]', '', value)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if not re.fullmatch(r'[0-9]{10,15}', digits):
        raise ValueError()
    return '+' + digits


class Dialogue:
    def __init__(self, store, crm, config, bot_id, now=None, labels=None):
        self.store, self.crm, self.config, self.bot_id = store, crm, config, bot_id
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.zone = ZoneInfo(config.timezone)
        self.labels = labels or {}

    def message(self, chat, text, markup=None):
        payload = {'chat_id': chat, 'text': text}
        if markup:
            payload['reply_markup'] = markup
        self.store.enqueue({'method': 'sendMessage', 'payload': payload})

    def buttons(self, s, rows):
        return {'inline_keyboard': [[{'text': text, 'callback_data': f"{s['revision']}|{action}"}
                                    for text, action in row] for row in rows]}

    def render(self, chat, s, notice=None):
        s['revision'] += 1
        if notice:
            self.message(chat, notice)
        step = s['step']
        if s.pop('phone_keyboard', False) and step != 'phone':
            self.message(chat, tr(s, 'keyboard_closed'), {'remove_keyboard': True})
        if s.get('choosing_language') or step == 'language':
            self.message(chat, tr(s, 'language'), self.buttons(s, [[('Қазақша', 'lang:kk'), ('Русский', 'lang:ru')]]))
            return
        rows = []
        if step == 'idle':
            text = tr(s, 'cancelled')
            rows = [[(tr(s, 'new'), 'new')]]
        elif step == 'done':
            text = tr(s, 'done', order_id=s['result']['id'])
            rows = [[(tr(s, 'new'), 'new')]]
        elif step == 'resume':
            text = tr(s, 'resume_prompt')
            rows = [[(tr(s, 'resume'), 'resume'), (tr(s, 'restart'), 'new')]]
        elif step == 'edit':
            text = tr(s, 'edit_prompt')
            rows = [[(tr(s, 'f_' + field), 'edit:' + field)] for field in FIELDS]
        elif step == 'pending':
            text = tr(s, 'uncertain')
            rows = [[(tr(s, 'retry'), 'send')]]
        elif step == 'review':
            text = self.summary(s)
            rows = [[(tr(s, 'send'), 'send')], [(tr(s, 'edit'), 'edit')]]
        elif step == 'consent':
            text = tr(s, 'consent', business=self.config.business)
            rows = [[(tr(s, 'continue'), 'continue')]]
        elif step == 'phone':
            s['phone_keyboard'] = True
            self.message(chat, tr(s, 'phone'), {'keyboard': [[{'text': tr(s, 'share'), 'request_contact': True}],
                                  [{'text': '/back'}, {'text': '/cancel'}, {'text': '/language'}]],
                                  'resize_keyboard': True, 'one_time_keyboard': True})
            return
        elif step == 'service':
            text = tr(s, 'service')
            rows = [[(label(spec, s['lang']), 'service:' + str(i))]
                    for i, spec in enumerate(s['catalog']['specs'])]
        elif step == 'district' and s['catalog']['districts']:
            text = tr(s, 'district')
            rows = [[(self.district_label(name, s['lang']), 'district:' + str(i))]
                    for i, name in enumerate(s['catalog']['districts'])]
        elif step == 'duration':
            text = tr(s, 'duration')
            rows = [[(tr(s, 'hour' + str(i)), 'duration:' + str(i)) for i in (1, 2, 3)]]
        elif step == 'date':
            text = tr(s, 'date', timezone=self.config.timezone,
                      example=(self.now().astimezone(self.zone) + timedelta(days=2)).strftime('%d.%m.%Y %H:%M'))
        else:
            text = tr(s, step)
            if step == 'description':
                rows = [[(tr(s, 'skip'), 'skip')]]
        if step in STEPS[1:] or step == 'edit':
            rows.append([(tr(s, 'back'), 'back')])
        if step not in {'done', 'idle', 'pending'}:
            rows.append([(tr(s, 'cancel'), 'cancel')])
        rows.append([(tr(s, 'language_button'), 'language')])
        self.message(chat, text, self.buttons(s, rows))

    def district_label(self, name, lang):
        return self.labels.get('districts', {}).get(name, {}).get(lang) or (KAZAKH_DISTRICTS.get(name) if lang == 'kk' else None) or name

    def summary(self, s):
        f = s['fields']
        values = dict(f)
        spec = next((x for x in s['catalog']['specs'] if x['id'] == f.get('service')), None)
        values['service'] = label(spec, s['lang']) if spec else f.get('service', '')
        values['district'] = self.district_label(f.get('district', ''), s['lang'])
        values['date'] = datetime.fromisoformat(f['date']).astimezone(self.zone).strftime('%d.%m.%Y %H:%M') + ' (' + self.config.timezone + ')'
        values['duration'] = tr(s, 'summary_duration', hours=f['duration'])
        return tr(s, 'review') + '\n\n' + '\n'.join(
            tr(s, 'f_' + k) + ': ' + str(values.get(k) or tr(s, 'empty')) for k in FIELDS) + '\n\n' + tr(s, 'warning')

    def fresh_catalog(self):
        catalog = self.crm.catalog()
        for spec in catalog['specs']:
            for lang, text in self.labels.get('specializations', {}).get(spec['id'], {}).items():
                if lang in {'ru', 'kk'}:
                    spec['name_' + lang] = text
        return catalog

    def begin(self, s):
        catalog = self.fresh_catalog()
        revision, lang = s['revision'], s['lang']
        s.clear()
        s.update(step='consent', lang=lang, revision=revision, fields={}, catalog=catalog)

    def advance(self, s):
        if s.pop('editing', False):
            s['step'] = 'review'
        else:
            s['step'] = STEPS[STEPS.index(s['step']) + 1]

    def prepare(self, s, user):
        if s.get('payload'):
            return
        catalog = self.fresh_catalog()
        s['catalog'] = catalog
        spec = next((x for x in catalog['specs'] if x['id'] == s['fields']['service']), None)
        if not spec:
            s['step'] = 'service'; s['editing'] = True
            raise HTTPError(400, {'code': 'invalid_service'})
        f = s['fields']
        start = datetime.fromisoformat(f['date'])
        if not self.now() < start <= self.now() + timedelta(days=365):
            s['step'] = 'date'; s['editing'] = True
            raise HTTPError(400, {'code': 'bad_date'})
        s['payload'] = {
            'request_id': str(uuid4()),
            'client': {'name': f['name'], 'phone': f['phone'], 'external_id': f'telegram:{self.bot_id}:{user}'},
            'order': {'specialization': spec['id'], 'title': spec['name'], 'description': f.get('description', ''),
                      'address': f['address'], 'district': f['district'],
                      'start_at': start.astimezone(timezone.utc).isoformat(),
                      'end_at': (start + timedelta(hours=f['duration'])).astimezone(timezone.utc).isoformat()}}

    def process(self, update):
        """Persist submission intent BEFORE HTTP; acknowledge only with final state/outbox."""
        update_id = update['update_id']
        query = update.get('callback_query')
        msg = query.get('message', {}) if query else update.get('message', {})
        sender = query.get('from', {}) if query else msg.get('from', {})
        user, chat = sender.get('id'), msg.get('chat', {}).get('id')
        submit = False
        with self.store.transaction():
            if self.store.seen(update_id):
                return
            if msg.get('chat', {}).get('type') != 'private' or not user or user != chat:
                self.store.acknowledge(update_id)
                return
            s = self.store.load(user)
            action = None
            if query:
                self.store.enqueue({'method': 'answerCallbackQuery', 'payload': {'callback_query_id': query['id']}})
                try:
                    revision, action = query['data'].split('|', 1)
                    if int(revision) != s['revision']:
                        self.message(chat, tr(s, 'stale'))
                        self.store.acknowledge(update_id)
                        return
                except (KeyError, ValueError):
                    self.store.acknowledge(update_id)
                    return
            text = msg.get('text', '').strip() if not query else ''
            if text.split('@')[0] == '/myid':
                self.message(chat, f'Telegram Chat ID: {chat}\nMASTER_TELEGRAM_CHAT_ID={chat}')
                self.store.acknowledge(update_id)
                return
            if text.startswith('/'):
                command = text.split()[0].split('@')[0]
                action = {'/start': 'start', '/new': 'new', '/language': 'language',
                          '/cancel': 'cancel', '/back': 'back'}.get(command, 'unknown')
            try:
                submit = self.transition(s, user, action, text, msg.get('contact'))
                if not submit:
                    self.render(chat, s)
            except HTTPError as exc:
                key = exc.data.get('code')
                notice = tr(s, key) if key in {'invalid_service', 'bad_date'} else tr(s, 'unavailable')
                # Initial selection can fail before a catalog exists; keep a usable idle screen.
                if not s.get('catalog'):
                    s['step'] = 'idle'
                self.render(chat, s, notice)
            except InputError as exc:
                self.render(chat, s, tr(s, exc.key, **exc.values))
            self.store.save(user, s)
            if not submit:
                self.store.acknowledge(update_id)
        if submit:
            # A crash here leaves a frozen payload; redelivered update safely retries it.
            self.finish_submission(update_id, user, chat)

    def transition(self, s, user, action, text, contact):
        step = s['step']
        if action == 'language':
            s['choosing_language'] = True
            return False
        if action and action.startswith('lang:'):
            lang = action.split(':')[1]
            if lang not in {'ru', 'kk'} or not (s.get('choosing_language') or step == 'language'):
                raise InputError('choose')
            s['lang'] = lang; s.pop('choosing_language', None)
            if step == 'language':
                self.begin(s)
            return False
        if not s['lang'] or s.get('choosing_language'):
            return False
        if step == 'pending':
            if action == 'send':
                return True
            raise InputError('pending')
        if action == 'start':
            if step in {'idle', 'done'}:
                self.begin(s)
            elif step != 'language':
                if step != 'resume':
                    s['resume_step'] = step
                s['step'] = 'resume'
            return False
        if action == 'new':
            self.begin(s)
            return False
        if action == 'resume' and step == 'resume':
            s['step'] = s.pop('resume_step')
            return False
        if action == 'cancel':
            s.update(step='idle', fields={}); s.pop('editing', None)
            return False
        if action == 'back':
            if step == 'edit' or s.get('editing'):
                s.pop('editing', None); s['step'] = 'review'
            elif step in STEPS and step != 'consent':
                s['step'] = STEPS[STEPS.index(step) - 1]
            return False
        if action == 'continue' and step == 'consent':
            s['consent'] = True; self.advance(s)
            return False
        if action == 'edit' and step == 'review':
            s['step'] = 'edit'
            return False
        if action and action.startswith('edit:') and step == 'edit':
            field = action.split(':')[1]
            if field not in FIELDS:
                raise InputError('choose')
            s.update(step=field, editing=True)
            return False
        if action == 'send' and step == 'review':
            self.prepare(s, user)
            s['step'] = 'pending'
            return True
        if action == 'unknown':
            raise InputError('unknown')
        f = s['fields']
        if step == 'service' and action and action.startswith('service:'):
            try:
                index = int(action.split(':')[1]); assert index >= 0
                f['service'] = s['catalog']['specs'][index]['id']
            except (IndexError, ValueError, AssertionError):
                raise InputError('choose') from None
        elif step == 'district' and s['catalog']['districts']:
            try:
                assert action and action.startswith('district:')
                index = int(action.split(':')[1]); assert index >= 0
                f['district'] = s['catalog']['districts'][index]
            except (IndexError, ValueError, AssertionError):
                raise InputError('choose') from None
        elif step == 'duration':
            if action not in {'duration:1', 'duration:2', 'duration:3'}:
                raise InputError('choose')
            f['duration'] = int(action.split(':')[1])
        elif step == 'phone':
            if contact:
                if contact.get('user_id') != user:
                    raise InputError('bad_phone')
                text = contact.get('phone_number', '')
            try:
                f['phone'] = normalize_phone(text)
            except ValueError:
                raise InputError('bad_phone') from None
            s.pop('phone_keyboard', None)
            self.message(user, tr(s, 'f_phone') + ': ' + f['phone'], {'remove_keyboard': True})
        elif step == 'date':
            try:
                if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}', text):
                    raise ValueError()
                naive = datetime.strptime(text, '%d.%m.%Y %H:%M')
                local = naive.replace(tzinfo=self.zone)
                start = local.astimezone(timezone.utc)
                # Reject nonexistent/ambiguous wall times for configurable DST zones.
                if start.astimezone(self.zone).replace(tzinfo=None) != naive or local.utcoffset() != local.replace(fold=1).utcoffset():
                    raise ValueError()
                if not self.now() < start <= self.now() + timedelta(days=365):
                    raise ValueError()
                f['date'] = start.isoformat()
            except ValueError:
                raise InputError('bad_date') from None
        elif step in LIMITS:
            if step == 'description' and action == 'skip':
                f[step] = ''
            elif action:
                raise InputError('choose')
            elif not text:
                raise InputError('unsupported')
            elif len(text) > LIMITS[step]:
                raise InputError('bad_text', limit=LIMITS[step])
            else:
                f[step] = text
        else:
            raise InputError('choose')
        self.advance(s)
        return False

    def finish_submission(self, update_id, user, chat):
        payload = copy.deepcopy(self.store.load(user)['payload'])
        result, failure = None, None
        try:
            result = self.crm.submit(payload)
        except HTTPError as exc:
            failure = exc
            LOG.warning('CRM submission status=%s code=%s request_id=%s', exc.status,
                        self.safe_code(exc.data.get('code')), payload['request_id'])
        with self.store.transaction():
            s = self.store.load(user)
            notice = None
            if result:
                # Erase unnecessary PII after successful delivery; keep language and receipt.
                s = {'lang': s['lang'], 'step': 'done', 'revision': s['revision'], 'fields': {}, 'result': result}
            elif failure.status == 409 and failure.data.get('code') in {'schedule_conflict', 'outside_working_hours'}:
                s.pop('payload'); s.update(step='date', editing=True)
                notice = tr(s, 'outside_working_hours' if failure.data.get('code') == 'outside_working_hours' else 'conflict')
            elif failure.status == 400:
                s.pop('payload'); s['step'] = 'review'; notice = tr(s, 'rejected')
            elif failure.status in {401, 403, 404, 405}:
                # Auth/router rejection did not reach create_order and is safe to edit.
                s.pop('payload'); s['step'] = 'review'; notice = tr(s, 'not_sent')
            elif failure.status == 409 and failure.data.get('code') == 'idempotency_conflict':
                notice = tr(s, 'identity_conflict')
            # Network/5xx/unknown conflicts retain the exact frozen payload.
            self.render(chat, s, notice)
            self.store.save(user, s)
            self.store.acknowledge(update_id)

    @staticmethod
    def safe_code(code):
        return code if code in {'schedule_conflict', 'outside_working_hours', 'idempotency_conflict', 'validation_error', 'master_unavailable'} else 'error'


class InputError(Exception):
    def __init__(self, key, **values):
        self.key, self.values = key, values
