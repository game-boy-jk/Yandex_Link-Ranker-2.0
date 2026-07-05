import pytest

from batch.search_runner import BatchResult, limit_workers, process_queries
from batch.search_runner import result_to_csv_row
from batch.table_files import BatchQuery
from core.quotas import DEFERRED_REQUEST_SECOND_QUOTA
from search.yandex_search import YandexSearchResponseError


def test_process_queries_preserves_input_order_with_workers():
    queries = [
        BatchQuery(row_number=2, query="лопата"),
        BatchQuery(row_number=3, query="краска"),
    ]

    def fake_search(query: str) -> list[str]:
        return [f"https://example.test/{query}"]

    results = process_queries(queries, workers=2, search=fake_search)

    assert results == [
        BatchResult(
            row_number=2,
            query="лопата",
            urls=("https://example.test/лопата",),
        ),
        BatchResult(
            row_number=3,
            query="краска",
            urls=("https://example.test/краска",),
        ),
    ]


def test_process_queries_reports_progress_with_workers():
    queries = [
        BatchQuery(row_number=2, query="лопата"),
        BatchQuery(row_number=3, query="краска"),
    ]
    progress_calls: list[tuple[int, int]] = []

    def fake_search(query: str) -> list[str]:
        return [f"https://example.test/{query}"]

    process_queries(
        queries,
        workers=2,
        search=fake_search,
        progress=lambda done, total: progress_calls.append((done, total)),
    )

    assert progress_calls == [(1, 2), (2, 2)]


def test_process_queries_stops_before_next_query_when_requested():
    queries = [
        BatchQuery(row_number=2, query="лопата"),
        BatchQuery(row_number=3, query="краска"),
    ]
    progress_calls: list[tuple[int, int]] = []
    should_stop = False

    def fake_search(query: str) -> list[str]:
        nonlocal should_stop
        should_stop = True
        return [f"https://example.test/{query}"]

    results = process_queries(
        queries,
        workers=1,
        search=fake_search,
        progress=lambda done, total: progress_calls.append((done, total)),
        should_stop=lambda: should_stop,
    )

    assert results == [
        BatchResult(
            row_number=2,
            query="лопата",
            urls=("https://example.test/лопата",),
        ),
    ]
    assert progress_calls == [(1, 2)]


def test_process_queries_rejects_invalid_workers():
    with pytest.raises(ValueError, match="workers"):
        process_queries([], workers=0, search=lambda query: [])


def test_limit_workers_caps_value_by_api_second_quota():
    assert limit_workers(DEFERRED_REQUEST_SECOND_QUOTA + 5) == (
        DEFERRED_REQUEST_SECOND_QUOTA
    )


def test_process_queries_keeps_row_error_without_stopping_batch():
    queries = [
        BatchQuery(row_number=2, query="лопата"),
        BatchQuery(row_number=3, query="краска"),
    ]

    def fake_search(query: str) -> list[str]:
        if query == "лопата":
            raise YandexSearchResponseError("поиск временно недоступен")
        return [f"https://example.test/{query}"]

    results = process_queries(queries, search=fake_search)

    assert results == [
        BatchResult(
            row_number=2,
            query="лопата",
            urls=(),
            error="YandexSearchResponseError: поиск временно недоступен",
        ),
        BatchResult(
            row_number=3,
            query="краска",
            urls=("https://example.test/краска",),
        ),
    ]


def test_process_queries_does_not_hide_unexpected_errors():
    queries = [BatchQuery(row_number=2, query="лопата")]

    def fake_search(query: str) -> list[str]:
        raise RuntimeError("сломалась логика ранжирования")

    with pytest.raises(RuntimeError, match="сломалась логика"):
        process_queries(queries, search=fake_search)


def test_result_to_csv_row_limits_urls_to_three():
    result = BatchResult(
        row_number=5,
        query="лопата",
        urls=(
            "https://one.test",
            "https://two.test",
            "https://three.test",
            "https://four.test",
        ),
    )

    assert result_to_csv_row(result) == {
        "row": "5",
        "status": "found",
        "query": "лопата",
        "url_1": "https://one.test",
        "url_2": "https://two.test",
        "url_3": "https://three.test",
        "error": "",
    }


def test_result_to_csv_row_serializes_error_status():
    result = BatchResult(
        row_number=7,
        query="лопата",
        urls=(),
        error="RuntimeError: поиск временно недоступен",
    )

    assert result_to_csv_row(result) == {
        "row": "7",
        "status": "error",
        "query": "лопата",
        "url_1": "",
        "url_2": "",
        "url_3": "",
        "error": "RuntimeError: поиск временно недоступен",
    }
