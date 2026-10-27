"""Private, size-only diagnostics and request correlation. Never logs bodies/queries/credentials."""
import asyncio
import contextvars
import fcntl
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

REQUEST_ID = contextvars.ContextVar("spotizer_request_id", default=None)
_ID = re.compile(r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})\Z")
MAX_BYTES = 20 * 1024 * 1024


def request_id(value):
    return value.lower() if isinstance(value, str) and len(value) <= 36 and _ID.fullmatch(value) else str(uuid.uuid4())


def exception_info(error):
    """Stack frames/types, not exception messages (SQL parameters/tokens may be in those)."""
    chain, seen = [], set()
    current = error
    category = "internal_error"
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        # Classify known operational errors without persisting their arbitrary messages.
        message = str(current).lower()
        for marker, label in [("no such table", "schema_missing_table"), ("does not exist", "schema_missing_object"),
                              ("no such column", "schema_missing_column"), ("connection refused", "database_connection_refused"),
                              ("permission denied", "permission_denied"), ("disk is full", "disk_full"), ("no space left", "disk_full")]:
            if marker in message:
                category = label
                break
        row = {"type": type(current).__name__, "frames": [
            {"file": os.path.basename(f.filename), "line": f.lineno, "function": f.name}
            for f in traceback.extract_tb(current.__traceback__, limit=35)
        ]}
        for key in ("sqlstate", "sqlite_errorcode", "sqlite_errorname"):
            value = getattr(current, key, None)
            if isinstance(value, (str, int)) and re.fullmatch(r"[A-Za-z0-9_]{1,40}", str(value)):
                row[key] = value
        chain.append(row)
        current = current.__cause__ or current.__context__
    return {"error_type": type(error).__name__, "category": category, "exceptions": chain}


class BoundedJournal:
    """One byte cap across workers; flock protects rotation and appending. No time expiry."""
    def __init__(self, directory, max_bytes=MAX_BYTES):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.lock = threading.RLock()

    def write(self, entry):
        raw = (json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(raw) > min(32768, self.max_bytes):
            entry = {k: v for k, v in entry.items() if k != "exceptions"}
            entry["stack_truncated"] = True
            raw = (json.dumps(entry, separators=(",", ":")) + "\n").encode()
        if len(raw) > self.max_bytes:
            return
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / ".lock").open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    files = sorted(self.directory.glob("*.jsonl"))
                    total = sum(f.stat().st_size for f in files)
                    while files and total + len(raw) > self.max_bytes:
                        old = files.pop(0)
                        total -= old.stat().st_size
                        old.unlink()
                    active = files[-1] if files else None
                    if active is None or active.stat().st_size + len(raw) > min(256 * 1024, self.max_bytes):
                        active = self.directory / f"{time.time_ns():020d}-{uuid.uuid4().hex}.jsonl"
                    with active.open("ab") as output:
                        output.write(raw)
                        output.flush()
                        if entry.get("error_type"):
                            os.fsync(output.fileno())
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


_journal = None
_logger = logging.getLogger("spotizer.diagnostics")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    _logger.addHandler(logging.StreamHandler())


def configure(directory, max_bytes=MAX_BYTES):
    global _journal
    _journal = BoundedJournal(directory, max_bytes)


def record(event, **fields):
    allowed = {"request_id", "method", "route", "status", "duration_ms", "error_type", "category", "exceptions", "operation"}
    entry = {"time": time.time_ns() // 1_000_000, "event": event}
    entry.update({k: v for k, v in fields.items() if k in allowed})
    if "request_id" not in entry and REQUEST_ID.get():
        entry["request_id"] = REQUEST_ID.get()
    # These are exclusively our structured records, not SQLAlchemy's raw exception text.
    try:
        if _journal:
            _journal.write(entry)
    except Exception:
        # Logging must never turn an otherwise successful API request into a failure.
        _logger.warning('{"event":"diagnostic_storage_unavailable"}')
    _logger.info(json.dumps(entry, ensure_ascii=True, separators=(",", ":")))


def record_exception(operation, error):
    record("background_error", operation=operation, **exception_info(error))


class RequestDiagnosticsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        supplied = [v.decode("latin1") for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"]
        rid = request_id(supplied[0] if len(supplied) == 1 else None)
        scope.setdefault("state", {})["request_id"] = rid
        token = REQUEST_ID.set(rid)
        start = time.monotonic()
        status, started, error = 500, False, None

        async def send_with_id(message):
            nonlocal status, started
            if message["type"] == "http.response.start":
                status, started = message["status"], True
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"] + [(b"x-request-id", rid.encode("ascii"))]
            await send(message)
        try:
            await self.app(scope, receive, send_with_id)
        except asyncio.CancelledError:
            status = 499
            raise
        except Exception as exc:
            error = exception_info(exc)
            if started:
                # A partial audio stream cannot be replaced with a JSON response.
                raise
            body = json.dumps({"error": "internal_server_error", "request_id": rid}).encode()
            await send_with_id({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send_with_id({"type": "http.response.body", "body": body})
        finally:
            try:
                # Use route templates, never raw paths (which can contain user IDs or tokens).
                route = getattr(scope.get("route"), "path", "[unmatched]")
                fields = dict(request_id=rid, method=scope.get("method", "UNKNOWN"), route=route, status=status,
                              duration_ms=round((time.monotonic() - start) * 1000))
                record("request_failed" if error or status >= 400 else "request_finished", **fields, **(error or {}))
            finally:
                REQUEST_ID.reset(token)
