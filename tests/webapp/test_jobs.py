from datetime import datetime

from webapp.jobs import HourlyUsage


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
