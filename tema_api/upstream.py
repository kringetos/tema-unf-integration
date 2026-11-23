"""Small OData v3/1C HTTP client. Mutations are never retried automatically."""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class APIError(Exception):
    def __init__(self, status, code, message, details=""):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details

    def as_dict(self):
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


@dataclass
class Result:
    status: int
    body: object
    content_type: str = "application/json; charset=utf-8"
    disposition: str = ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, user="", password="", token="", timeout=20, public_errors=False):
        url = urllib.parse.urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            raise ValueError("An absolute HTTP(S) upstream URL without embedded credentials is required")
        if url.query or url.fragment:
            raise ValueError("Upstream URL cannot contain a query or fragment")
        self.base = base_url.rstrip("/")
        self.authorization = ("Bearer " + token if token else "Basic " +
                              base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii"))
        self.timeout = timeout
        self.public_errors = public_errors
        # Local 1C must not go through the user's external HTTP proxy.
        handlers = [NoRedirect()]
        if url.hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, method, path, query=None, body=None, headers=None):
        encoded = urllib.parse.quote(path.lstrip("/"), safe="/()',$=:")
        url = self.base + "/" + encoded
        if query:
            # OData query grammar uses percent-encoded spaces, not HTML form '+'.
            # Keep '$' option names intact for the 1C query parser.
            url += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote, safe="$,'()")
        request_headers = {"Authorization": self.authorization, "Accept": "application/json, application/pdf"}
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise APIError(502, "UPSTREAM_RESPONSE_TOO_LARGE", "Ответ 1С слишком большой")
                content_type = response.headers.get("Content-Type", "")
                if "application/pdf" in content_type:
                    if not raw.startswith(b"%PDF-"):
                        raise APIError(502, "INVALID_PDF", "1С вернула некорректный PDF")
                    return Result(response.status, raw, "application/pdf", response.headers.get("Content-Disposition", ""))
                if not raw:
                    return Result(response.status, {})
                return Result(response.status, json.loads(raw.decode("utf-8-sig")))
        except urllib.error.HTTPError as error:
            # 1C exception bodies can contain credentials, filesystem paths and stack traces.
            # Do not forward arbitrary upstream error text to a public client.
            status = error.code
            if self.public_errors and status in {400, 404, 409, 500, 503}:
                try:
                    payload = json.loads(error.read(65536).decode("utf-8-sig"))["error"]
                    if isinstance(payload["code"], str) and isinstance(payload["message"], str):
                        raise APIError(status, payload["code"], payload["message"], str(payload.get("details", "")))
                except (ValueError, KeyError, UnicodeError, TypeError):
                    pass
            if status in {401, 403}:
                raise APIError(502, "UPSTREAM_AUTH", "Ошибка авторизации Python в 1С") from None
            if status in {400, 404, 409, 412}:
                code = {400: "UPSTREAM_VALIDATION", 404: "NOT_FOUND", 409: "CONFLICT", 412: "CONFLICT"}[status]
                raise APIError(409 if status == 412 else status, code, "1С отклонила запрос", f"HTTP {status}") from None
            raise APIError(502, "UPSTREAM_ERROR", "Ошибка HTTP-сервиса 1С", f"HTTP {status}") from None
        except (TimeoutError, urllib.error.URLError, OSError):
            raise APIError(504, "UPSTREAM_UNAVAILABLE", "1С не ответила вовремя",
                           "Для записи результат может быть неизвестен; проверьте объект перед повтором") from None
        except (ValueError, UnicodeError):
            raise APIError(502, "UPSTREAM_FORMAT", "1С вернула некорректный JSON") from None

    def get(self, entity, query=None):
        return self.request("GET", entity, {"$format": "json", **(query or {})}).body


def literal(value):
    """OData string literal escaping, followed by URL encoding in Client."""
    return "'" + str(value).replace("'", "''") + "'"
