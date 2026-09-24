from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.quotas import DEFERRED_REQUEST_HOURLY_QUOTA
from webapp.jobs import HourlyUsage, JobManager, QuotaExceededError


def test_hourly_usage_splits_day_and_night_requests() -> None:
    usage = HourlyUsage()

    usage.add(1000, tariff_code="day")
    usage.add(500, tariff_code="night")

    by_tariff = usage.by_tariff()

    assert usage.get() == 1500
    assert by_tariff["day"].requests == 1000
    assert by_tariff["night"].requests == 500


def test_hourly_usage_resets_on_new_hour(monkeypatch) -> None:
    current_hour = datetime(2026, 6, 20, 10)
    monkeypatch.setattr(
        "webapp.jobs.current_hour_key",
        lambda: current_hour,
    )
    usage = HourlyUsage()

    usage.add(1000, tariff_code="day")
    current_hour = datetime(2026, 6, 20, 11)

    assert usage.get() == 0
    assert usage.by_tariff()["day"].requests == 0


def test_hourly_usage_reserves_atomically_for_parallel_requests() -> None:
    usage = HourlyUsage()
    usage.add(DEFERRED_REQUEST_HOURLY_QUOTA - 1, tariff_code="day")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(usage.reserve, 1, tariff_code="day")
            for _ in range(2)
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except QuotaExceededError as exc:
                outcomes.append(exc)

    assert sum(not isinstance(item, QuotaExceededError) for item in outcomes) == 1
    assert usage.get() == DEFERRED_REQUEST_HOURLY_QUOTA


def test_hourly_usage_releases_unused_reservation() -> None:
    usage = HourlyUsage()
    reservation = usage.reserve(3, tariff_code="day")

    usage.claim(reservation, tariff_code="day")
    usage.release(reservation, 2)

    assert usage.get() == 1
    assert usage.by_tariff()["day"].requests == 1


def test_hourly_usage_rechecks_quota_after_hour_change(monkeypatch) -> None:
    current_hour = datetime(2026, 6, 20, 10)
    monkeypatch.setattr("webapp.jobs.current_hour_key", lambda: current_hour)
    usage = HourlyUsage()
    reservation = usage.reserve(1, tariff_code="day")
    current_hour = datetime(2026, 6, 20, 11)
    usage.add(DEFERRED_REQUEST_HOURLY_QUOTA, tariff_code="day")

    with pytest.raises(QuotaExceededError):
        usage.claim(reservation, tariff_code="day")


def test_file_job_releases_quota_when_thread_cannot_start(
    monkeypatch, tmp_path: Path
) -> None:
    input_path = tmp_path / "queries.csv"
    input_path.write_text("query\nЛопата\n", encoding="utf-8")
    manager = JobManager()
    failed_thread = Mock()
    failed_thread.return_value.start.side_effect = RuntimeError("поток не запущен")
    monkeypatch.setattr("webapp.jobs.threading.Thread", failed_thread)

    with pytest.raises(RuntimeError, match="поток не запущен"):
        manager.create_file_job(input_path, input_path.name)

    assert manager.usage.get() == 0
