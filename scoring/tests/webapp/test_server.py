import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from webapp.jobs import HistoryStore, JobManager, SearchHistoryItem, ensure_web_dirs
from webapp.metrics import DEFERRED_REQUEST_HOURLY_QUOTA
from webapp.server import MAX_UPLOAD_BYTES, WebAppHandler


@contextmanager
def run_test_server(manager: JobManager | None = None) -> Iterator[str]:
    """Запускает HTTP-сервер webapp на свободном локальном порту."""

    ensure_web_dirs()
    old_manager = WebAppHandler.manager
    WebAppHandler.manager = manager or JobManager()
    server = ThreadingHTTPServer(("127.0.0.1", 0), WebAppHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        WebAppHandler.manager = old_manager


def test_estimate_counts_uploaded_csv_queries() -> None:
    body, content_type = make_multipart_file(
        filename="queries.csv",
        content="query\nЛопата\nКраска\n".encode("utf-8"),
    )

    with run_test_server() as address:
        status, payload = post_json(
            address=address,
            path="/api/estimate",
            body=body,
            content_type=content_type,
        )

    assert status == HTTPStatus.OK
    assert payload["total"] == 2
    assert payload["cost"]["requests_count"] == 2
    assert payload["quota"]["overLimit"] is False


def test_metrics_returns_deferred_limits() -> None:
    with run_test_server() as address:
        status, payload = get_json(address=address, path="/api/metrics?count=200")

    assert status == HTTPStatus.OK
    assert payload["cost"]["requests_count"] == 200
    assert payload["usage"]["currentHour"] == 0
    assert payload["quota"]["currentHour"] == 0
    assert payload["quota"]["requested"] == 200
    assert payload["limits"]["deferredHourly"] == DEFERRED_REQUEST_HOURLY_QUOTA


def test_metrics_rejects_invalid_count() -> None:
    with run_test_server() as address:
        status, payload = get_json(address=address, path="/api/metrics?count=abc")

    assert status == HTTPStatus.BAD_REQUEST
    assert payload["error"] == "count должен быть целым числом"


def test_history_delete_removes_single_item(tmp_path: Path) -> None:
    manager = make_manager_with_history(tmp_path)
    manager.history.add(
        SearchHistoryItem(
            item_id="first",
            created_at="2026-07-01T21:19:33",
            kind="single",
            query="самокат",
            status="found",
        )
    )
    manager.history.add(
        SearchHistoryItem(
            item_id="second",
            created_at="2026-07-01T21:20:00",
            kind="single",
            query="лопата",
            status="found",
        )
    )

    with run_test_server(manager=manager) as address:
        status, payload = delete_json(address=address, path="/api/history/first")

    assert status == HTTPStatus.OK
    assert [item["item_id"] for item in payload["items"]] == ["second"]


def test_history_clear_removes_all_items(tmp_path: Path) -> None:
    manager = make_manager_with_history(tmp_path)
    manager.history.add(
        SearchHistoryItem(
            item_id="first",
            created_at="2026-07-01T21:19:33",
            kind="single",
            query="самокат",
            status="found",
        )
    )

    with run_test_server(manager=manager) as address:
        status, payload = delete_json(address=address, path="/api/history")

    assert status == HTTPStatus.OK
    assert payload["items"] == []


def test_estimate_warns_when_file_exceeds_quota() -> None:
    body, content_type = make_multipart_file(
        filename="queries.csv",
        content=make_queries_csv(DEFERRED_REQUEST_HOURLY_QUOTA + 1),
    )

    with run_test_server() as address:
        status, payload = post_json(
            address=address,
            path="/api/estimate",
            body=body,
            content_type=content_type,
        )

    assert status == HTTPStatus.OK
    assert payload["total"] == DEFERRED_REQUEST_HOURLY_QUOTA + 1
    assert payload["quota"]["overLimit"] is True
    assert payload["quota"]["remaining"] == DEFERRED_REQUEST_HOURLY_QUOTA


def test_upload_rejects_file_when_quota_is_exceeded() -> None:
    body, content_type = make_multipart_file(
        filename="queries.csv",
        content=make_queries_csv(DEFERRED_REQUEST_HOURLY_QUOTA + 1),
    )

    with run_test_server() as address:
        status, payload = post_json(
            address=address,
            path="/api/upload",
            body=body,
            content_type=content_type,
        )

    assert status == HTTPStatus.TOO_MANY_REQUESTS
    assert "Превышен лимит" in payload["error"]
    assert payload["quota"]["overLimit"] is True


def test_cancel_running_upload_job(monkeypatch) -> None:
    body, content_type = make_multipart_file(
        filename="queries.csv",
        content="query\nЛопата\nКраска\n".encode("utf-8"),
    )

    def fake_process_queries(*args: object, **kwargs: object) -> list[object]:
        should_stop = kwargs.get("should_stop")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if callable(should_stop) and should_stop():
                return []
            time.sleep(0.01)
        return []

    monkeypatch.setattr("webapp.jobs.process_queries", fake_process_queries)

    with run_test_server() as address:
        status, payload = post_json(
            address=address,
            path="/api/upload",
            body=body,
            content_type=content_type,
        )
        job_id = str(payload["job"]["id"])

        cancel_status, cancel_payload = post_empty(
            address=address,
            path=f"/api/jobs/{job_id}/cancel",
        )

        for _ in range(30):
            status, payload = get_json(address=address, path=f"/api/jobs/{job_id}")
            if payload["job"]["status"] == "canceled":
                break
            time.sleep(0.02)

    assert status == HTTPStatus.OK
    assert cancel_status == HTTPStatus.OK
    assert cancel_payload["job"]["status"] in {"canceling", "canceled"}
    assert payload["job"]["status"] == "canceled"


def test_canceled_upload_counts_completed_requests(monkeypatch) -> None:
    body, content_type = make_multipart_file(
        filename="queries.csv",
        content="query\nЛопата\nКраска\n".encode("utf-8"),
    )

    def fake_process_queries(*args: object, **kwargs: object) -> list[object]:
        should_stop = kwargs.get("should_stop")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if callable(should_stop) and should_stop():
                return [object()]
            time.sleep(0.01)
        return [object()]

    monkeypatch.setattr("webapp.jobs.process_queries", fake_process_queries)

    with run_test_server() as address:
        status, payload = post_json(
            address=address,
            path="/api/upload",
            body=body,
            content_type=content_type,
        )
        job_id = str(payload["job"]["id"])
        post_empty(address=address, path=f"/api/jobs/{job_id}/cancel")

        for _ in range(30):
            status, payload = get_json(address=address, path=f"/api/jobs/{job_id}")
            if payload["job"]["status"] == "canceled":
                break
            time.sleep(0.02)

    assert status == HTTPStatus.OK
    assert payload["job"]["status"] == "canceled"
    assert payload["job"]["done"] == 1
    assert payload["usage"]["currentHour"] == 1


def test_upload_job_marks_unexpected_batch_error(monkeypatch) -> None:
    body, content_type = make_multipart_file(
        filename="queries.csv",
        content="query\nЛопата\n".encode("utf-8"),
    )

    def fake_process_queries(*args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("сломалась обработка")

    monkeypatch.setattr("webapp.jobs.process_queries", fake_process_queries)

    with run_test_server() as address:
        status, payload = post_json(
            address=address,
            path="/api/upload",
            body=body,
            content_type=content_type,
        )
        job_id = str(payload["job"]["id"])

        for _ in range(30):
            status, payload = get_json(address=address, path=f"/api/jobs/{job_id}")
            if payload["job"]["status"] == "error":
                break
            time.sleep(0.02)

    assert status == HTTPStatus.OK
    assert payload["job"]["status"] == "error"
    assert payload["job"]["error"] == "RuntimeError: сломалась обработка"


def test_upload_rejects_too_large_body_before_reading_file() -> None:
    headers = {
        "Content-Type": "multipart/form-data; boundary=test-boundary",
        "Content-Length": str(MAX_UPLOAD_BYTES + 1),
    }

    with run_test_server() as address:
        status, payload = post_raw(
            address=address,
            path="/api/upload",
            body=b"",
            headers=headers,
        )

    assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert "Файл слишком большой" in payload["error"]


def make_queries_csv(count: int) -> bytes:
    """Создаёт CSV с нужным количеством запросов."""

    rows = ["query", *(f"Товар {index}" for index in range(1, count + 1))]
    return "\n".join(rows).encode("utf-8")


def make_manager_with_history(tmp_path: Path) -> JobManager:
    """Создаёт менеджер с историей во временном файле."""

    manager = JobManager()
    manager.history = HistoryStore(tmp_path / "history.json")
    return manager


def make_multipart_file(*, filename: str, content: bytes) -> tuple[bytes, str]:
    """Собирает multipart/form-data с одним полем file."""

    boundary = "test-boundary"
    body = b"\r\n".join(
        [
            f"--{boundary}".encode("ascii"),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"'
            ).encode("utf-8"),
            b"Content-Type: text/csv",
            b"",
            content,
            f"--{boundary}--".encode("ascii"),
            b"",
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def post_json(
    *,
    address: str,
    path: str,
    body: bytes,
    content_type: str,
) -> tuple[int, dict[str, object]]:
    """Отправляет POST и возвращает JSON-ответ."""

    connection = HTTPConnection(address, timeout=5)
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": content_type},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        connection.close()


def post_raw(
    *,
    address: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[int, dict[str, object]]:
    """Отправляет POST с готовыми заголовками и возвращает JSON-ответ."""

    connection = HTTPConnection(address, timeout=5)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        connection.close()


def post_empty(*, address: str, path: str) -> tuple[int, dict[str, object]]:
    """Отправляет пустой POST и возвращает JSON-ответ."""

    connection = HTTPConnection(address, timeout=5)
    try:
        connection.request("POST", path)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        connection.close()


def delete_json(*, address: str, path: str) -> tuple[int, dict[str, object]]:
    """Отправляет DELETE и возвращает JSON-ответ."""

    connection = HTTPConnection(address, timeout=5)
    try:
        connection.request("DELETE", path)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        connection.close()


def get_json(*, address: str, path: str) -> tuple[int, dict[str, object]]:
    """Отправляет GET и возвращает JSON-ответ."""

    connection = HTTPConnection(address, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        connection.close()
