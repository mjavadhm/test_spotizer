import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from app import diagnostics as d


def invoke(inner, headers=()):
    messages = []
    async def run():
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "method": "GET", "path": "/private-token", "headers": headers}
        await d.RequestDiagnosticsMiddleware(inner)(scope, receive, send)
    asyncio.run(run())
    return messages


def test_incoming_id_is_echoed_and_context_is_reset(tmp_path):
    d.configure(tmp_path)
    rid = "12345678-1234-1234-1234-123456789abc"
    async def app(scope, receive, send):
        assert d.REQUEST_ID.get() == rid
        scope["route"] = SimpleNamespace(path="/v1/tracks/{track_id}/status")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"OK"})
    messages = invoke(app, [(b"x-request-id", rid.encode())])
    assert (b"x-request-id", rid.encode()) in messages[0]["headers"]
    assert d.REQUEST_ID.get() is None
    assert "private-token" not in next(tmp_path.glob("*.jsonl")).read_text()


def test_uncaught_exception_gets_safe_json_500_and_correlated_stack(tmp_path):
    d.configure(tmp_path)
    async def app(scope, receive, send):
        raise RuntimeError("Authorization: Bearer SECRET lyric and SQL parameters")
    messages = invoke(app)
    assert messages[0]["status"] == 500
    body = json.loads(messages[1]["body"])
    assert body["request_id"].encode() == dict(messages[0]["headers"])[b"x-request-id"]
    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text())
    assert record["request_id"] == body["request_id"]
    assert record["error_type"] == "RuntimeError"
    assert record["exceptions"][0]["frames"]
    assert "SECRET" not in json.dumps(record)


def test_invalid_or_duplicate_request_ids_are_replaced():
    for value in [None, "", "x\r\nAuthorization:secret", "a" * 10000, "not-a-uuid"]:
        rid = d.request_id(value)
        assert d._ID.fullmatch(rid)
        assert rid != value
    assert d.request_id("A" * 32) == "a" * 32


def test_handled_http_errors_still_receive_request_id(tmp_path):
    d.configure(tmp_path)
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": [(b"x-request-id", b"wrong")]})
        await send({"type": "http.response.body", "body": b"missing"})
    messages = invoke(app)
    assert messages[0]["status"] == 404
    assert len([k for k,v in messages[0]["headers"] if k == b"x-request-id"]) == 1


def test_rotation_is_byte_bounded_and_does_not_expire_by_age(tmp_path):
    journal = d.BoundedJournal(tmp_path, 1500)
    journal.write({"event": "old", "time": 1})
    assert any('"time":1' in f.read_text() for f in tmp_path.glob("*.jsonl"))
    for i in range(100):
        journal.write({"event": "request", "request_id": str(i), "time": i})
    assert sum(f.stat().st_size for f in tmp_path.glob("*.jsonl")) <= 1500
    for f in tmp_path.glob("*.jsonl"):
        for line in f.read_text().splitlines(): json.loads(line)


def test_parallel_writers_never_corrupt_or_exceed_cap(tmp_path):
    def writer():
        journal = d.BoundedJournal(tmp_path, 4000)
        for i in range(30): journal.write({"event": "request", "time": i})
    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(f.stat().st_size for f in tmp_path.glob("*.jsonl")) <= 4000
    for f in tmp_path.glob("*.jsonl"):
        for line in f.read_text().splitlines(): json.loads(line)


def test_schema_error_is_classified_without_logging_secret_parameters():
    try:
        raise RuntimeError("no such table: library_tracks; token=SECRET")
    except RuntimeError as exc:
        info = d.exception_info(exc)
    assert info["category"] == "schema_missing_table"
    assert "SECRET" not in json.dumps(info)


def test_logging_disk_failure_does_not_break_successful_request(monkeypatch):
    class Broken:
        def write(self, entry): raise OSError("disk full")
    monkeypatch.setattr(d, "_journal", Broken())
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"OK"})
    assert invoke(app)[0]["status"] == 200


def test_real_fastapi_errors_validation_and_headers(tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    d.configure(tmp_path)
    app = FastAPI()
    app.add_middleware(d.RequestDiagnosticsMiddleware)

    @app.get('/tracks/{track_id}')
    def track(track_id: int):
        if track_id == 1:
            raise RuntimeError('SQL password=SECRET')
        if track_id == 2:
            raise HTTPException(status_code=404, detail='Not found')
        return {'ok': True}

    rid = '12345678-1234-1234-1234-123456789abc'
    with TestClient(app) as client:
        for path, status in [('/tracks/1', 500), ('/tracks/2', 404), ('/tracks/bad', 422), ('/tracks/3', 200), ('/unknown-secret', 404)]:
            response = client.get(path, headers={'X-Request-ID': rid})
            assert response.status_code == status
            assert response.headers['X-Request-ID'] == rid
        assert client.get('/tracks/1').json()['error'] == 'internal_server_error'
    text = ''.join(f.read_text() for f in tmp_path.glob('*.jsonl'))
    assert 'SECRET' not in text and 'unknown-secret' not in text
    assert '/tracks/{track_id}' in text


def test_real_fastapi_streaming_failure_keeps_id_and_logs_error(tmp_path):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient
    d.configure(tmp_path)
    app = FastAPI()
    app.add_middleware(d.RequestDiagnosticsMiddleware)

    @app.get('/stream')
    def stream():
        async def chunks():
            yield b'partial audio'
            raise OSError('private path SECRET')
        return StreamingResponse(chunks())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/stream')
        assert response.status_code == 200  # Headers already sent: never replace partial audio with JSON.
        assert d._ID.fullmatch(response.headers['X-Request-ID'])
    text = ''.join(f.read_text() for f in tmp_path.glob('*.jsonl'))
    assert 'request_failed' in text and 'SECRET' not in text
    assert response.headers['X-Request-ID'] in text
