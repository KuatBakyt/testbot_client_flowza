"""HTTP errors deliberately contain no URL, credentials or response text."""
import json
from urllib import request, error


class HTTPError(Exception):
    def __init__(self, status=0, data=None):
        self.status = status
        self.data = data if isinstance(data, dict) else {}
        super().__init__(f'HTTP status {status}' if status else 'Network request failed')


def json_request(method, url, payload=None, headers=None, timeout=20):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = request.Request(url, data=body, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
    # Do not follow redirects: Authorization or bot tokens must not cross origins.
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
            return json.loads(response.read(2_000_000))
    except error.HTTPError as exc:
        try:
            data = json.loads(exc.read(100_000))
        except (ValueError, OSError):
            data = {}
        raise HTTPError(exc.code, data) from None
    except (error.URLError, TimeoutError, OSError, ValueError):
        raise HTTPError() from None
