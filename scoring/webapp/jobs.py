import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from batch.search_runner import process_queries, result_to_csv_row
from batch.table_files import BatchFileError, BatchQuery
from batch.table_files import load_queries_from_file, write_results_csv
from ranking.link_ranker import find_product_links_strict
from webapp.metrics import CostEstimate, QuotaEstimate
from webapp.metrics import DEFERRED_REQUEST_SECOND_QUOTA
from webapp.metrics import estimate_deferred_cost, estimate_deferred_quota
from webapp.metrics import get_tariff_timezone


WEB_DATA_DIR = Path("web_data")
UPLOAD_DIR = WEB_DATA_DIR / "uploads"
WEB_RESULTS_DIR = WEB_DATA_DIR / "results"
HISTORY_PATH = WEB_DATA_DIR / "history.json"
SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".xlsx"}
MAX_HISTORY_ITEMS = 100
WEB_DEFAULT_WORKERS = 4
WEB_DEFAULT_WORKERS_ENV = "WEB_DEFAULT_WORKERS"


@dataclass(slots=True)
class SearchHistoryItem:
    """Одна запись истории поиска."""

    created_at: str
    kind: str
    query: str
    status: str
    item_id: str = field(default_factory=lambda: uuid4().hex)
    urls: list[str] = field(default_factory=list)
    filename: str = ""
    total: int = 0


@dataclass(slots=True)
class SearchJob:
    """Состояние фоновой обработки файла."""

    job_id: str
    input_path: Path
    filename: str
    total: int
    cost: CostEstimate
    done: int = 0
    status: str = "queued"
    error: str = ""
    output_path: Path | None = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def percent(self) -> int:
        """Возвращает прогресс в процентах."""

        if self.total <= 0:
            return 0
        return round(self.done / self.total * 100)


@dataclass(frozen=True, slots=True)
class TariffUsage:
    """Количество запросов по одному тарифу."""

    requests: int = 0


def current_hour_key() -> datetime:
    """Возвращает начало текущего часа по часовому поясу тарифа."""

    return datetime.now(get_tariff_timezone()).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


class HourlyUsage:
    """Хранит счётчик запросов текущего часа и разбивку по тарифам."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hour = current_hour_key()
        self._count = 0
        self._day_requests = 0
        self._night_requests = 0

    def add(self, value: int, *, tariff_code: str) -> int:
        """Добавляет количество запросов и возвращает новый счётчик."""

        with self._lock:
            self._reset_if_needed()
            safe_value = max(value, 0)
            self._count += safe_value
            if tariff_code == "night":
                self._night_requests += safe_value
            else:
                self._day_requests += safe_value

            return self._count

    def get(self) -> int:
        """Возвращает количество запросов за текущий час."""

        with self._lock:
            self._reset_if_needed()
            return self._count

    def estimate(self, requested: int) -> QuotaEstimate:
        """Считает лимит с учётом нового количества запросов."""

        with self._lock:
            self._reset_if_needed()
            return estimate_deferred_quota(
                current_hour=self._count,
                requested=requested,
            )

    def by_tariff(self) -> dict[str, TariffUsage]:
        """Возвращает запросы отдельно по дневному и ночному тарифу."""

        with self._lock:
            self._reset_if_needed()
            return {
                "day": TariffUsage(requests=self._day_requests),
                "night": TariffUsage(requests=self._night_requests),
            }

    def _reset_if_needed(self) -> None:
        current_hour = current_hour_key()
        if self._hour != current_hour:
            self._hour = current_hour
            self._count = 0
            self._day_requests = 0
            self._night_requests = 0


class HistoryStore:
    """Читает и записывает историю поиска."""

    def __init__(self, path: Path = HISTORY_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()

    def add(self, item: SearchHistoryItem) -> None:
        """Добавляет запись в историю."""

        with self._lock:
            rows = self._read()
            rows.insert(0, asdict(item))
            rows = rows[:MAX_HISTORY_ITEMS]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def items(self) -> list[dict[str, object]]:
        """Возвращает историю поиска."""

        with self._lock:
            rows = self._read()
            normalized_rows, changed = ensure_history_ids(rows)
            if changed:
                self._write(normalized_rows)
            return normalized_rows

    def clear(self) -> None:
        """Очищает всю историю поиска."""

        with self._lock:
            self._write([])

    def remove(self, item_id: str) -> bool:
        """Удаляет одну запись истории по id."""

        with self._lock:
            rows = self._read()
            normalized_rows, _ = ensure_history_ids(rows)
            filtered_rows = [
                row for row in normalized_rows if row.get("item_id") != item_id
            ]
            if len(filtered_rows) == len(normalized_rows):
                self._write(normalized_rows)
                return False

            self._write(filtered_rows)
            return True

    def _read(self) -> list[dict[str, object]]:
        """Читает историю без захвата lock."""

        if not self.path.exists():
            return []

        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        if not isinstance(value, list):
            return []

        return [row for row in value if isinstance(row, dict)]

    def _write(self, rows: list[dict[str, object]]) -> None:
        """Записывает историю без захвата lock."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class QuotaExceededError(ValueError):
    """Ошибка превышения лимита отложенных запросов."""

    def __init__(self, quota: QuotaEstimate) -> None:
        self.quota = quota
        super().__init__(quota_error_message(quota))


def ensure_history_ids(
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], bool]:
    """Добавляет id старым записям истории, если их ещё нет."""

    changed = False
    normalized_rows: list[dict[str, object]] = []

    for row in rows:
        normalized_row = dict(row)
        if not normalized_row.get("item_id"):
            normalized_row["item_id"] = uuid4().hex
            changed = True
        normalized_rows.append(normalized_row)

    return normalized_rows, changed


class JobManager:
    """Создаёт и ведёт фоновые задачи обработки файлов."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SearchJob] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self.history = HistoryStore()
        self.usage = HourlyUsage()

    def create_file_job(self, input_path: Path, filename: str) -> SearchJob:
        """Создаёт фоновую задачу для загруженного файла."""

        queries = load_queries_from_file(input_path)
        quota = self.usage.estimate(len(queries))
        if quota.over_limit:
            raise QuotaExceededError(quota)

        cost = estimate_deferred_cost(len(queries))
        job = SearchJob(
            job_id=uuid4().hex,
            input_path=input_path,
            filename=filename,
            total=len(queries),
            cost=cost,
        )

        with self._lock:
            self._jobs[job.job_id] = job
            self._cancel_events[job.job_id] = threading.Event()

        thread = threading.Thread(
            target=self._run_file_job,
            args=(job.job_id, queries),
            daemon=True,
        )
        thread.start()
        return job

    def get_job(self, job_id: str) -> SearchJob | None:
        """Возвращает задачу по id."""

        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> SearchJob | None:
        """Запрашивает остановку фоновой обработки файла."""

        with self._lock:
            job = self._jobs.get(job_id)
            cancel_event = self._cancel_events.get(job_id)
            if job is None:
                return None

            if job.status in {"done", "error", "canceled"}:
                return job

            job.cancel_requested = True
            if job.status == "queued":
                job.status = "canceled"
                job.error = "Обработка отменена"
            else:
                job.status = "canceling"
                job.error = "Останавливаю обработку"

            if cancel_event is not None:
                cancel_event.set()

            return job

    def search_once(self, query: str) -> list[str]:
        """Выполняет одиночный поиск и добавляет запись в историю."""

        quota = self.usage.estimate(1)
        if quota.over_limit:
            raise QuotaExceededError(quota)

        urls = find_product_links_strict(query)
        self.usage.add(1, tariff_code=estimate_deferred_cost(1).tariff_code)
        self.history.add(
            SearchHistoryItem(
                created_at=datetime.now().isoformat(timespec="seconds"),
                kind="single",
                query=query,
                status="found" if urls else "not_found",
                urls=urls,
            )
        )
        return urls

    def _run_file_job(self, job_id: str, queries: list[BatchQuery]) -> None:
        job = self.get_job(job_id)
        if job is None:
            return

        cancel_event = self._get_cancel_event(job_id)
        if cancel_event is not None and cancel_event.is_set():
            self._update_job(
                job_id,
                status="canceled",
                error="Обработка отменена",
                cancel_requested=True,
            )
            return

        self._update_job(job_id, status="running")

        try:
            results = process_queries(
                queries,
                workers=get_web_default_workers(),
                search=find_product_links_strict,
                progress=lambda done, total: self._update_job(
                    job_id,
                    done=done,
                    total=total,
                ),
                should_stop=cancel_event.is_set if cancel_event else None,
            )
            if cancel_event is not None and cancel_event.is_set():
                completed_count = len(results)
                if completed_count:
                    self.usage.add(
                        completed_count,
                        tariff_code=job.cost.tariff_code,
                    )
                self._update_job(
                    job_id,
                    done=completed_count,
                    status="canceled",
                    error="Обработка отменена",
                    cancel_requested=True,
                )
                self.history.add(
                    SearchHistoryItem(
                        created_at=datetime.now().isoformat(timespec="seconds"),
                        kind="file",
                        query=job.filename,
                        status="canceled",
                        total=len(queries),
                    )
                )
                return

            output_path = write_results_csv(
                (result_to_csv_row(result) for result in results),
                input_path=job.input_path,
                output_dir=WEB_RESULTS_DIR,
                result_stem=Path(job.filename).stem,
            )
        except (BatchFileError, ValueError, OSError) as exc:
            self._update_job(job_id, status="error", error=str(exc))
            return
        except Exception as exc:
            self._update_job(
                job_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        self.usage.add(len(queries), tariff_code=job.cost.tariff_code)
        self._update_job(
            job_id,
            done=len(queries),
            status="done",
            output_path=output_path,
        )
        self.history.add(
            SearchHistoryItem(
                created_at=datetime.now().isoformat(timespec="seconds"),
                kind="file",
                query=job.filename,
                status="done",
                filename=output_path.name,
                total=len(queries),
            )
        )

    def _get_cancel_event(self, job_id: str) -> threading.Event | None:
        """Возвращает флаг отмены фоновой задачи."""

        with self._lock:
            return self._cancel_events.get(job_id)

    def _update_job(self, job_id: str, **changes: object) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            for name, value in changes.items():
                setattr(job, name, value)


def ensure_web_dirs() -> None:
    """Создаёт рабочие папки web-интерфейса."""

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    WEB_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def get_web_default_workers() -> int:
    """Возвращает количество параллельных web-запросов с учётом лимита API."""

    raw_value = os.getenv(WEB_DEFAULT_WORKERS_ENV, str(WEB_DEFAULT_WORKERS))
    try:
        workers = int(raw_value)
    except ValueError:
        workers = WEB_DEFAULT_WORKERS

    return min(max(workers, 1), DEFERRED_REQUEST_SECOND_QUOTA)


def quota_error_message(quota: QuotaEstimate) -> str:
    """Возвращает понятный текст ошибки по лимиту."""

    return (
        "Превышен лимит отложенных запросов. "
        f"Доступно: {quota.remaining}, в списке: {quota.requested}."
    )


def is_supported_upload(filename: str) -> bool:
    """Проверяет формат загружаемого файла."""

    return Path(filename).suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES


def job_to_dict(job: SearchJob) -> dict[str, object]:
    """Преобразует задачу в JSON-словарь."""

    return {
        "id": job.job_id,
        "filename": job.filename,
        "total": job.total,
        "done": job.done,
        "percent": job.percent,
        "status": job.status,
        "error": job.error,
        "cancelRequested": job.cancel_requested,
        "downloadUrl": f"/api/download/{job.job_id}" if job.output_path else "",
        "cost": asdict(job.cost),
        "createdAt": job.created_at,
    }
