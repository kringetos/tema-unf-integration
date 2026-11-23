"""Run with python -m tema_api.server --config path/to/settings.json."""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .service import Service
from .upstream import APIError, Client, Result


class Journal:
    """Keep ambiguous writes pending across restarts; never blindly repeat them."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, status INTEGER, body TEXT)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, key, fingerprint):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fingerprint,state,status,body FROM requests WHERE key=?", (key,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise APIError(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key уже использован для другого запроса")
                if row[1] != "done":
                    raise APIError(409, "WRITE_OUTCOME_UNKNOWN", "Запрос уже выполняется или требует сверки с 1С",
                                   "Не меняйте ключ для повторной записи до проверки результата")
                return Result(row[2], json.loads(row[3]))
            db.execute("INSERT INTO requests(key,fingerprint,state) VALUES(?,?,'pending')", (key, fingerprint))

    def complete(self, key, result):
        with self.connection() as db:
            db.execute("UPDATE requests SET state='done',status=?,body=? WHERE key=?",
                       (result.status, json.dumps(result.body, ensure_ascii=False, allow_nan=False), key))

    def release(self, key):
        with self.connection() as db:
            db.execute("DELETE FROM requests WHERE key=? AND state='pending'", (key,))


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def load_settings(path):
    path = Path(path).resolve()
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    if settings.get("credentials_file"):
        source = Path(settings["credentials_file"])
        if not source.is_absolute():
            source = path.parent / source
        credentials = json.loads(source.read_text(encoding="utf-8-sig"))
        for part in settings.get("credentials_path", "profiles.default.agents.odata").split("."):
            credentials = credentials[part]
        for name in ("odata_url", "odata_user", "odata_password"):
            if name not in settings:
                settings[name] = credentials.get(name, "")
    settings["api_token"] = os.environ.get("TEMA_API_TOKEN", settings.get("api_token", ""))
    if len(settings["api_token"]) < 32:
        raise ValueError("Set TEMA_API_TOKEN or api_token to a random secret of at least 32 characters")
    journal = Path(settings.get("journal", "requests.sqlite3"))
    settings["journal"] = str(journal if journal.is_absolute() else path.parent / journal)
    return settings


def build_service(settings):
    timeout = settings.get("timeout", 20)
    odata = Client(settings["odata_url"], settings.get("odata_user", ""), settings.get("odata_password", ""), timeout=timeout)
    bridge = (Client(settings["bridge_url"], settings.get("bridge_user", settings.get("odata_user", "")),
                     settings.get("bridge_password", settings.get("odata_password", "")), timeout=timeout, public_errors=True)
              if settings.get("bridge_url") else None)
    return Service(odata, bridge, settings)


def handler(service, token, journal):
    mutation_lock = threading.Lock()

    def allowed_origin(origin):
        for allowed in service.settings.get("cors_allowed_origins", []):
            if origin == allowed:
                return True
            if allowed.startswith("https://*."):
                suffix = allowed[len("https://*"):]
                parsed = urllib.parse.urlsplit(origin)
                host = parsed.hostname or ""
                if (parsed.scheme == "https" and parsed.netloc == host and not parsed.path
                        and not parsed.query and not parsed.fragment
                        and host.endswith(suffix) and host != suffix[1:]):
                    return True
        return False

    class Handler(BaseHTTPRequestHandler):
        server_version = "TemaAPI/0.1"

        def log_message(self, fmt, *args):
            # The URL can contain client names, taxpayer IDs and other personal data.
            pass

        def send_result(self, result, request_id):
            raw = result.body if isinstance(result.body, bytes) else json.dumps(result.body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(result.status)
            self.send_header("Content-Type", result.content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Request-ID", request_id)
            origin = self.headers.get("Origin", "")
            if origin and allowed_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Expose-Headers", "Content-Disposition, X-Request-ID")
            if result.status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            if result.disposition and "\r" not in result.disposition and "\n" not in result.disposition:
                self.send_header("Content-Disposition", result.disposition)
            self.end_headers()
            self.wfile.write(raw)

        def execute(self):
            request_id = secrets.token_hex(16)
            try:
                self.connection.settimeout(30)
                route = urllib.parse.urlsplit(self.path)
                test_assets = {
                    "/test": ("test.html", "text/html; charset=utf-8"),
                    "/test/": ("test.html", "text/html; charset=utf-8"),
                    "/test.css": ("test.css", "text/css; charset=utf-8"),
                    "/test.js": ("test.js", "text/javascript; charset=utf-8"),
                }
                if self.command == "GET" and route.path in test_assets:
                    file_name, content_type = test_assets[route.path]
                    file_path = Path(__file__).resolve().parent / "web" / file_name
                    raw = file_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if self.command == "OPTIONS":
                    origin = self.headers.get("Origin", "")
                    method = self.headers.get("Access-Control-Request-Method", "")
                    requested = {item.strip().lower() for item in self.headers.get("Access-Control-Request-Headers", "").split(",") if item.strip()}
                    if (not allowed_origin(origin) or method not in {"GET", "POST", "PATCH"}
                            or requested - {"authorization", "content-type", "idempotency-key"}):
                        raise APIError(403, "CORS_DENIED", "Origin, method or headers are not allowed")
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Idempotency-Key")
                    self.send_header("Access-Control-Max-Age", "600")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.command == "GET" and route.path == "/health":
                    return self.send_result(service.dispatch("GET", "/health"), request_id)
                authorization = self.headers.get("Authorization", "")
                if not hmac.compare_digest(authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")):
                    raise APIError(401, "UNAUTHORIZED", "Требуется корректный токен Bearer")
                if self.headers.get("Transfer-Encoding"):
                    raise APIError(400, "VALIDATION_ERROR", "Transfer-Encoding не поддерживается")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise APIError(400, "VALIDATION_ERROR", "Некорректный Content-Length") from None
                if not 0 <= length <= 2 * 1024 * 1024:
                    raise APIError(413, "PAYLOAD_TOO_LARGE", "Максимальный размер тела запроса 2 МБ")
                body = None
                if length:
                    if self.headers.get_content_type() != "application/json":
                        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Требуется application/json")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise APIError(400, "INVALID_JSON", "Получено неполное тело запроса")
                    try:
                        body = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
                    except (ValueError, UnicodeError):
                        raise APIError(400, "INVALID_JSON", "Некорректный JSON") from None
                try:
                    pairs = urllib.parse.parse_qs(route.query, keep_blank_values=True, max_num_fields=30)
                except ValueError:
                    raise APIError(400, "VALIDATION_ERROR", "Слишком много параметров запроса") from None
                if any(len(values) != 1 for values in pairs.values()):
                    raise APIError(400, "VALIDATION_ERROR", "Повторяющиеся параметры запроса")
                query = {key: values[0] for key, values in pairs.items()}
                path = urllib.parse.unquote(route.path)
                key = self.headers.get("Idempotency-Key")
                if key is not None and (not 1 <= len(key) <= 200 or not key.isascii() or any(ord(c) < 33 for c in key)):
                    raise APIError(400, "VALIDATION_ERROR", "Некорректный Idempotency-Key")
                if self.command in {"POST", "PATCH"}:
                    with mutation_lock:
                        if key:
                            fingerprint = hashlib.sha256(json.dumps([self.command, path, query, body], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                            replay = journal.reserve(key, fingerprint)
                            if replay:
                                return self.send_result(replay, request_id)
                        try:
                            result = service.dispatch(self.command, path, query, body, key)
                        except APIError as error:
                            # Transport/server failures can occur after the upstream commit.
                            # Persist their reservation as pending for manual reconciliation.
                            if key and (error.status < 500 or error.code in {"BRIDGE_NOT_CONFIGURED", "CONTACT_WRITES_DISABLED"}):
                                journal.release(key)
                            raise
                        if key:
                            journal.complete(key, result)
                else:
                    result = service.dispatch(self.command, path, query, body, key)
                self.send_result(result, request_id)
            except APIError as error:
                self.send_result(Result(error.status, error.as_dict()), request_id)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.error("Unexpected failure; request_id=%s", request_id)
                self.send_result(Result(500, {"error": {"code": "INTERNAL_ERROR", "message": "Внутренняя ошибка Python API", "details": request_id}}), request_id)

        do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_OPTIONS = execute

    return Handler


def main():
    parser = argparse.ArgumentParser(description="ТеМа API facade")
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    settings = load_settings(args.config)
    service = build_service(settings)
    server = ThreadingHTTPServer((args.host, args.port), handler(service, settings["api_token"], Journal(settings["journal"])))
    print(f"Tema API: http://{args.host}:{args.port}/api/v1 (OData; bridge={'yes' if service.bridge else 'no'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
