import argparse
import json
import mimetypes
from dataclasses import asdict
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from uuid import uuid4

import httpx
from batch.table_files import BatchFileError, load_queries_from_file
from search.yandex_search import YandexSearchConfigError, YandexSearchResponseError
from webapp.jobs import UPLOAD_DIR, JobManager, QuotaExceededError, ensure_web_dirs
from webapp.jobs import is_supported_upload, job_to_dict
from webapp.metrics import DEFERRED_REQUEST_HOURLY_QUOTA
from webapp.metrics import DEFERRED_REQUEST_SECOND_QUOTA, estimate_deferred_cost
from webapp.metrics import QuotaEstimate, TariffInfo, get_deferred_tariff


STATIC_DIR = Path(__file__).parent / "static"
HOST = "127.0.0.1"
PORT = 8000
MAX_UPLOAD_MEGABYTES = 20
MAX_UPLOAD_BYTES = MAX_UPLOAD_MEGABYTES * 1024 * 1024


class UploadTooLargeError(ValueError):
    """Ошибка превышения допустимого размера загружаемого файла."""


class WebAppHandler(BaseHTTPRequestHandler):
    """HTTP-обработчик web-интерфейса."""

    manager = JobManager()

    def do_GET(self) -> None:
        """Обрабатывает GET-запросы."""

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_static_file(STATIC_DIR / "index.html")
            return

        if path.startswith("/static/"):
            self.send_static_file(STATIC_DIR / path.removeprefix("/static/"))
            return

        if path.startswith("/api/jobs/"):
            self.handle_job_status(path)
            return

        if path.startswith("/api/download/"):
            self.handle_download(path)
            return

        if path == "/api/history":
            self.send_json({"items": self.manager.history.items()})
            return

        if path == "/api/metrics":
            self.handle_metrics(parsed.query)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Обрабатывает POST-запросы."""

        parsed = urlparse(self.path)

        if parsed.path == "/api/upload":
            self.handle_upload()
            return

        if parsed.path == "/api/estimate":
            self.handle_file_estimate()
            return

        if parsed.path == "/api/search":
            self.handle_single_search()
            return

        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
            self.handle_job_cancel(parsed.path)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        """Обрабатывает DELETE-запросы."""

        parsed = urlparse(self.path)

        if parsed.path == "/api/history":
            self.handle_history_clear()
            return

        if parsed.path.startswith("/api/history/"):
            self.handle_history_delete(parsed.path)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_upload(self) -> None:
        """Принимает CSV/XLSX файл и создаёт фоновую задачу."""

        try:
            filename, content = self.read_uploaded_file()
        except UploadTooLargeError as exc:
            self.send_json(
                {"error": str(exc)},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if not is_supported_upload(filename):
            self.send_json(
                {"error": "Поддерживаются только CSV и XLSX файлы"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        upload_path = UPLOAD_DIR / f"{uuid4().hex}_{Path(filename).name}"
        upload_path.write_bytes(content)

        try:
            job = self.manager.create_file_job(upload_path, filename)
        except BatchFileError as exc:
            upload_path.unlink(missing_ok=True)
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except QuotaExceededError as exc:
            upload_path.unlink(missing_ok=True)
            self.send_json(
                {
                    "error": str(exc),
                    "quota": quota_to_dict(exc.quota),
                    "usage": self.usage_payload(),
                },
                status=HTTPStatus.TOO_MANY_REQUESTS,
            )
            return

        self.send_json({"job": job_to_dict(job)}, status=HTTPStatus.CREATED)

    def handle_file_estimate(self) -> None:
        """Считает запросы в файле без запуска поиска."""

        try:
            filename, content = self.read_uploaded_file()
        except UploadTooLargeError as exc:
            self.send_json(
                {"error": str(exc)},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if not is_supported_upload(filename):
            self.send_json(
                {"error": "Поддерживаются только CSV и XLSX файлы"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        preview_path = UPLOAD_DIR / f"preview_{uuid4().hex}_{Path(filename).name}"
        preview_path.write_bytes(content)

        try:
            queries = load_queries_from_file(preview_path)
        except BatchFileError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        finally:
            preview_path.unlink(missing_ok=True)

        cost = estimate_deferred_cost(len(queries))
        quota = self.manager.usage.estimate(len(queries))
        self.send_json(
            {
                "filename": filename,
                "total": len(queries),
                "cost": asdict(cost),
                "usage": self.usage_payload(),
                "quota": quota_to_dict(quota),
                "tariff": tariff_to_dict(get_deferred_tariff()),
            }
        )

    def handle_single_search(self) -> None:
        """Выполняет одиночный поиск."""

        try:
            data = self.read_json()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        query = str(data.get("query", "")).strip()
        if not query:
            self.send_json(
                {"error": "Введите поисковый запрос"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            urls = self.manager.search_once(query)
        except QuotaExceededError as exc:
            self.send_json(
                {
                    "error": str(exc),
                    "quota": quota_to_dict(exc.quota),
                    "usage": self.usage_payload(),
                },
                status=HTTPStatus.TOO_MANY_REQUESTS,
            )
            return
        except (
            YandexSearchConfigError,
            YandexSearchResponseError,
            httpx.HTTPError,
        ) as exc:
            self.send_json(
                {"error": f"{type(exc).__name__}: {exc}"},
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        self.send_json(
            {
                "query": query,
                "urls": urls,
                "status": "found" if urls else "not_found",
                "cost": asdict(estimate_deferred_cost(1)),
                "usage": self.usage_payload(),
                "tariff": tariff_to_dict(get_deferred_tariff()),
            }
        )

    def handle_history_clear(self) -> None:
        """Очищает всю историю поиска."""

        self.manager.history.clear()
        self.send_json({"items": []})

    def handle_history_delete(self, path: str) -> None:
        """Удаляет одну запись истории."""

        item_id = path.rsplit("/", 1)[-1].strip()
        if not item_id:
            self.send_json(
                {"error": "Запись истории не указана"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        removed = self.manager.history.remove(item_id)
        if not removed:
            self.send_json(
                {"error": "Запись истории не найдена"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        self.send_json({"items": self.manager.history.items()})

    def handle_job_status(self, path: str) -> None:
        """Возвращает состояние фоновой задачи."""

        job_id = path.rsplit("/", 1)[-1]
        job = self.manager.get_job(job_id)
        if job is None:
            self.send_json({"error": "Задача не найдена"}, status=HTTPStatus.NOT_FOUND)
            return

        self.send_json({"job": job_to_dict(job), "usage": self.usage_payload()})

    def handle_job_cancel(self, path: str) -> None:
        """Отменяет фоновую обработку файла."""

        parts = path.strip("/").split("/")
        if len(parts) != 4:
            self.send_json(
                {"error": "Некорректный адрес задачи"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        job = self.manager.cancel_job(parts[2])
        if job is None:
            self.send_json({"error": "Задача не найдена"}, status=HTTPStatus.NOT_FOUND)
            return

        self.send_json({"job": job_to_dict(job), "usage": self.usage_payload()})

    def handle_download(self, path: str) -> None:
        """Отдаёт готовый файл результата."""

        job_id = path.rsplit("/", 1)[-1]
        job = self.manager.get_job(job_id)
        if job is None or job.output_path is None or not job.output_path.exists():
            self.send_json({"error": "Файл не найден"}, status=HTTPStatus.NOT_FOUND)
            return

        data = job.output_path.read_bytes()
        encoded_filename = quote(job.output_path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=result.csv; filename*=UTF-8''{encoded_filename}",
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_metrics(self, query_string: str) -> None:
        """Возвращает расчёт цены и лимитов."""

        params = parse_qs(query_string)
        try:
            count = int(params.get("count", ["0"])[0] or 0)
        except ValueError:
            self.send_json(
                {"error": "count должен быть целым числом"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        quota = self.manager.usage.estimate(count)
        self.send_json(
            {
                "cost": asdict(estimate_deferred_cost(count)),
                "usage": self.usage_payload(),
                "quota": quota_to_dict(quota),
                "tariff": tariff_to_dict(get_deferred_tariff()),
                "limits": {
                    "deferredHourly": DEFERRED_REQUEST_HOURLY_QUOTA,
                    "deferredPerSecond": DEFERRED_REQUEST_SECOND_QUOTA,
                },
            }
        )

    def read_uploaded_file(self) -> tuple[str, bytes]:
        """Читает файл из multipart/form-data."""

        content_type = self.headers.get("Content-Type", "")
        content_length = self.read_content_length(max_bytes=MAX_UPLOAD_BYTES)
        body = self.rfile.read(content_length)

        if "multipart/form-data" not in content_type:
            raise ValueError("Нужен multipart/form-data")

        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + body
        )

        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") != "file":
                continue

            filename = part.get_filename()
            if not filename:
                raise ValueError("Файл не выбран")
            return filename, part.get_payload(decode=True) or b""

        raise ValueError("Файл не найден в запросе")

    def read_content_length(self, *, max_bytes: int | None = None) -> int:
        """Возвращает Content-Length и проверяет его верхнюю границу."""

        raw_value = self.headers.get("Content-Length", "0") or "0"
        try:
            content_length = int(raw_value)
        except ValueError as exc:
            raise ValueError("Некорректный Content-Length") from exc

        if content_length < 0:
            raise ValueError("Некорректный Content-Length")

        if max_bytes is not None and content_length > max_bytes:
            raise UploadTooLargeError(
                f"Файл слишком большой. Максимум: {MAX_UPLOAD_MEGABYTES} МБ."
            )

        return content_length

    def read_json(self) -> dict[str, object]:
        """Читает JSON-тело запроса."""

        content_length = self.read_content_length()
        if content_length <= 0:
            return {}

        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Некорректный JSON") from exc

        if not isinstance(value, dict):
            raise ValueError("JSON должен быть объектом")

        return value

    def usage_payload(self) -> dict[str, object]:
        """Возвращает счётчик запросов и состояние лимита."""

        current_hour_count = self.manager.usage.get()
        tariff_usage = self.manager.usage.by_tariff()
        return {
            "currentHour": current_hour_count,
            "today": current_hour_count,
            "limit": DEFERRED_REQUEST_HOURLY_QUOTA,
            "overLimit": current_hour_count > DEFERRED_REQUEST_HOURLY_QUOTA,
            "day": asdict(tariff_usage["day"]),
            "night": asdict(tariff_usage["night"]),
        }

    def send_static_file(self, path: Path) -> None:
        """Отдаёт статичный файл."""

        static_root = STATIC_DIR.resolve()
        requested_path = path.resolve()
        if static_root not in requested_path.parents and requested_path != static_root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not requested_path.exists() or not requested_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data = requested_path.read_bytes()
        content_type = (
            mimetypes.guess_type(requested_path.name)[0] or "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(
        self,
        data: dict[str, object],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        """Отправляет JSON-ответ."""

        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """Отключает шумный access-log."""


def run(host: str = HOST, port: int = PORT) -> None:
    """Запускает web-интерфейс."""

    ensure_web_dirs()
    server = ThreadingHTTPServer((host, port), WebAppHandler)
    print(f"Web-интерфейс: http://{host}:{port}")
    server.serve_forever()


def quota_to_dict(quota: QuotaEstimate) -> dict[str, object]:
    """Преобразует расчёт лимита в JSON-словарь."""

    return {
        "currentHour": quota.current_hour,
        "today": quota.current_hour,
        "requested": quota.requested,
        "limit": quota.limit,
        "remaining": quota.remaining,
        "totalAfterRequest": quota.total_after_request,
        "overLimit": quota.over_limit,
    }


def tariff_to_dict(tariff: TariffInfo) -> dict[str, object]:
    """Преобразует тариф в JSON-словарь."""

    return {
        "code": tariff.code,
        "label": tariff.label,
        "pricePer1000": tariff.price_per_1000,
        "isNight": tariff.is_night,
        "timezone": tariff.timezone,
        "nightStartHour": tariff.night_start_hour,
        "nightEndHour": tariff.night_end_hour,
    }


def main() -> None:
    """Разбирает аргументы и запускает web-интерфейс."""

    parser = argparse.ArgumentParser(description="Web-интерфейс Link Ranker.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
