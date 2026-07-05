from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

from webapp.metrics import DAY_DEFERRED_REQUEST_PRICE_PER_1000
from webapp.metrics import NIGHT_DEFERRED_REQUEST_PRICE_PER_1000
from webapp.metrics import estimate_deferred_cost, get_deferred_tariff


def test_deferred_tariff_uses_day_price() -> None:
    tariff = get_deferred_tariff(datetime(2026, 6, 20, 9, tzinfo=timezone.utc))
    cost = estimate_deferred_cost(1000, tariff=tariff)

    assert tariff.code == "day"
    assert cost.price_per_1000 == DAY_DEFERRED_REQUEST_PRICE_PER_1000
    assert cost.total_price == 30.5


def test_deferred_tariff_uses_night_price() -> None:
    tariff = get_deferred_tariff(datetime(2026, 6, 19, 22, tzinfo=timezone.utc))
    cost = estimate_deferred_cost(1000, tariff=tariff)

    assert tariff.code == "night"
    assert cost.price_per_1000 == NIGHT_DEFERRED_REQUEST_PRICE_PER_1000
    assert cost.total_price == 25.41


def test_deferred_tariff_works_without_tzdata(monkeypatch) -> None:
    def missing_timezone(name: str) -> None:
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr("webapp.metrics.ZoneInfo", missing_timezone)

    tariff = get_deferred_tariff(datetime(2026, 6, 20, 9, tzinfo=timezone.utc))
    cost = estimate_deferred_cost(1000, tariff=tariff)

    assert tariff.code == "day"
    assert cost.total_price == 30.5
