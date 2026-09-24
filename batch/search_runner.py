from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

import httpx
from batch.table_files import BatchQuery
from core import config
from core.quotas import DEFERRED_REQUEST_SECOND_QUOTA, QuotaLimitError
from ranking.link_ranker import find_product_links_strict
from search.yandex_search import YandexSearchConfigError, YandexSearchResponseError


SearchFunction = Callable[[str], list[str]]
ProgressCallback = Callable[[int, int], None]
StopCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Результат поиска для одной входной строки."""

    row_number: int
    query: str
    urls: tuple[str, ...]
    error: str = ""

    @property
    def status(self) -> str:
        """Возвращает стабильный статус для CSV-вывода."""

        if self.error:
            return "error"
        if self.urls:
            return "found"
        return "not_found"


def process_queries(
    queries: list[BatchQuery],
    *,
    workers: int = 1,
    search: SearchFunction = find_product_links_strict,
    progress: ProgressCallback | None = None,
    should_stop: StopCallback | None = None,
) -> list[BatchResult]:
    """Ищет ссылки для всех запросов с сохранением порядка строк."""

    workers = limit_workers(workers)

    total = len(queries)
    if workers == 1:
        results: list[BatchResult] = []
        for done, query in enumerate(queries, start=1):
            if should_stop is not None and should_stop():
                break

            results.append(process_query(query, search=search))
            report_progress(progress, done, total)
        return results

    results: list[BatchResult | None] = [None] * total
    done_count = 0
    next_index = 0
    pending: dict[Future[BatchResult], int] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        next_index = submit_pending_queries(
            executor=executor,
            queries=queries,
            search=search,
            pending=pending,
            next_index=next_index,
            workers=workers,
            should_stop=should_stop,
        )

        while pending:
            completed, _ = wait(
                pending,
                timeout=0.2,
                return_when=FIRST_COMPLETED,
            )
            if not completed:
                if should_stop is not None and should_stop():
                    break
                continue

            for future in completed:
                index = pending.pop(future)
                results[index] = future.result()
                done_count += 1
                report_progress(progress, done_count, total)

            next_index = submit_pending_queries(
                executor=executor,
                queries=queries,
                search=search,
                pending=pending,
                next_index=next_index,
                workers=workers,
                should_stop=should_stop,
            )

        if should_stop is not None and should_stop():
            for future in pending:
                future.cancel()
            done_count = collect_pending_results(
                pending=pending,
                results=results,
                done_count=done_count,
                total=total,
                progress=progress,
            )

    return [result for result in results if result is not None]


def collect_pending_results(
    *,
    pending: dict[Future[BatchResult], int],
    results: list[BatchResult | None],
    done_count: int,
    total: int,
    progress: ProgressCallback | None,
) -> int:
    """Собирает результаты уже стартовавших задач после запроса остановки."""

    while pending:
        completed, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in completed:
            index = pending.pop(future)
            if future.cancelled():
                continue

            results[index] = future.result()
            done_count += 1
            report_progress(progress, done_count, total)

    return done_count


def submit_pending_queries(
    *,
    executor: ThreadPoolExecutor,
    queries: list[BatchQuery],
    search: SearchFunction,
    pending: dict[Future[BatchResult], int],
    next_index: int,
    workers: int,
    should_stop: StopCallback | None,
) -> int:
    """Добавляет в пул новые запросы, пока есть свободные worker-слоты."""

    while next_index < len(queries) and len(pending) < workers:
        if should_stop is not None and should_stop():
            break

        query = queries[next_index]
        pending[executor.submit(process_query, query, search=search)] = next_index
        next_index += 1

    return next_index


def limit_workers(
    workers: int,
    *,
    per_second_limit: int = DEFERRED_REQUEST_SECOND_QUOTA,
) -> int:
    """Ограничивает количество потоков лимитом Yandex Search API."""

    if workers < 1:
        raise ValueError("workers должен быть больше нуля")

    return min(workers, max(per_second_limit, 1))


def process_query(
    query: BatchQuery,
    *,
    search: SearchFunction = find_product_links_strict,
) -> BatchResult:
    """Ищет ссылки для одной входной строки."""

    try:
        urls = tuple(search(query.query))
    except (
        YandexSearchConfigError,
        YandexSearchResponseError,
        QuotaLimitError,
        httpx.HTTPError,
    ) as exc:
        return BatchResult(
            row_number=query.row_number,
            query=query.query,
            urls=(),
            error=f"{type(exc).__name__}: {exc}",
        )

    return BatchResult(
        row_number=query.row_number,
        query=query.query,
        urls=urls,
    )


def result_to_csv_row(result: BatchResult) -> dict[str, str]:
    """Преобразует один результат пакетной обработки в CSV-поля."""

    urls = list(result.urls[: config.TOP_LINKS_LIMIT])
    urls.extend([""] * (config.TOP_LINKS_LIMIT - len(urls)))

    return {
        "row": str(result.row_number),
        "status": result.status,
        "query": result.query,
        "url_1": urls[0],
        "url_2": urls[1],
        "url_3": urls[2],
        "error": result.error,
    }


def report_progress(
    progress: ProgressCallback | None,
    done: int,
    total: int,
) -> None:
    """Передаёт наружу состояние прогресса, если callback задан."""

    if progress is None:
        return

    progress(done, total)
