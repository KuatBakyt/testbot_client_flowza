from urllib.parse import urlsplit, parse_qs
from uuid import UUID
from .http import json_request, HTTPError


class CRM:
    def __init__(self, config, transport=json_request):
        self.config, self.transport = config, transport
        self.access = self.refresh = None

    def login(self):
        data = self.transport('POST', self.config.api_url + 'auth/login/',
                              {'phone': self.config.phone, 'password': self.config.password})
        try:
            self.access, self.refresh = data['access'], data['refresh']
        except (KeyError, TypeError):
            raise HTTPError(502) from None

    def call(self, method, path, payload=None):
        if not self.access:
            self.login()
        def send():
            return self.transport(method, self.config.api_url + path, payload,
                                  {'Authorization': 'Bearer ' + self.access})
        try:
            return send()
        except HTTPError as exc:
            if exc.status != 401:
                raise
        try:
            data = self.transport('POST', self.config.api_url + 'auth/refresh/', {'refresh': self.refresh})
            self.access = data['access']
            self.refresh = data.get('refresh', self.refresh)
        except HTTPError as exc:
            if exc.status not in (400, 401, 403):
                raise
            self.login()
        except (KeyError, TypeError):
            raise HTTPError(502) from None
        return send()

    def catalog(self):
        me = self.call('GET', 'me/')
        profile = me.get('master_profile')
        if me.get('role') != 'MASTER' or not profile:
            raise HTTPError(403, {'code': 'master_required'})
        if not profile.get('is_available'):
            raise HTTPError(409, {'code': 'master_unavailable'})
        ids = {s['specialization'] for s in profile.get('skills', []) if s.get('is_active')}
        results, page = [], 1
        for _ in range(100):
            data = self.call('GET', f'specializations/?page={page}')
            if isinstance(data, list):
                results.extend(data)
                break
            results.extend(data['results'])
            if not data.get('next'):
                break
            # Use only a validated page number, never request a server-provided URL.
            next_page = int(parse_qs(urlsplit(data['next']).query).get('page', [page + 1])[0])
            if next_page <= page:
                raise HTTPError(502)
            page = next_page
        else:
            raise HTTPError(502)
        specs = [s for s in results if s['id'] in ids and s.get('is_active', True)]
        if not specs:
            raise HTTPError(409, {'code': 'no_services'})
        return {'master_id': profile['id'], 'specs': specs, 'districts': profile.get('service_districts', profile.get('districts', [])),
                'city': profile.get('city', '')}

    def check_endpoint(self):
        data = self.call('OPTIONS', 'bot/orders/')
        if 'POST' not in data.get('actions', {}):
            raise HTTPError(404)

    def submit(self, payload):
        data = self.call('POST', 'bot/orders/', payload)
        try:
            UUID(data['id']); UUID(data['client_id'])
            if data['status'] != 'NEW' or not isinstance(data['replayed'], bool):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            # A malformed success may still have created the order: retry the same key.
            raise HTTPError(502) from None
        return data
