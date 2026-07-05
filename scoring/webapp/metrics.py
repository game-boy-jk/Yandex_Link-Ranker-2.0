from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.quotas import (
    DEFERRED_REQUEST_HOURLY_QUOTA as DEFERRED_REQUEST_HOURLY_QUOTA,
)
from core.quotas import (
    DEFERRED_REQUEST_SECOND_QUOTA as DEFERRED_REQUEST_SECOND_QUOTA,
)


DAY_DEFERRED_REQUEST_PRICE_PER_1000 = 30.5
NIGHT_DEFERRED_REQUEST_PRICE_PER_1000 = 25.41
MAX_QUERY_LENGTH = 400
TARIFF_TIMEZONE = "Europe/Moscow"
MOSCOW_TIMEZONE = timezone(timedelta(hours=3), name="MSK")
NIGHT_TARIFF_START_HOUR = 0
NIGHT_TARIFF_END_HOUR = 7


@dataclass(frozen=True, slots=True)
class TariffInfo:
    """Текущий тариф отложенных запросов."""

    code: str
    label: str
    price_per_1000: float
    is_night: bool
    timezone: str
    night_start_hour: int
    night_end_hour: int


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Расчёт стоимости отложенных запросов."""

    requests_count: int
    price_per_1000: float
    total_price: float
    tariff_code: str
    tariff_label: str


@dataclass(frozen=True, slots=True)
class QuotaEstimate:
    """Расчёт заполнения лимита с учётом нового списка запросов."""

    current_hour: int
    requested: int
    limit: int
    remaining: int
    total_after_request: int
    over_limit: bool


def get_deferred_tariff(now: datetime | None = None) -> TariffInfo:
    """Возвращает дневной или ночной тариф по московскому времени."""

    tariff_timezone = get_tariff_timezone()
    current_time = now or datetime.now(tariff_timezone)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=tariff_timezone)
    else:
        current_time = current_time.astimezone(tariff_timezone)

    is_night = is_night_tariff_hour(current_time.hour)
    if is_night:
        return TariffInfo(
            code="night",
            label="Ночной",
            price_per_1000=NIGHT_DEFERRED_REQUEST_PRICE_PER_1000,
            is_night=True,
            timezone=TARIFF_TIMEZONE,
            night_start_hour=NIGHT_TARIFF_START_HOUR,
            night_end_hour=NIGHT_TARIFF_END_HOUR,
        )

    return TariffInfo(
        code="day",
        label="Дневной",
        price_per_1000=DAY_DEFERRED_REQUEST_PRICE_PER_1000,
        is_night=False,
        timezone=TARIFF_TIMEZONE,
        night_start_hour=NIGHT_TARIFF_START_HOUR,
        night_end_hour=NIGHT_TARIFF_END_HOUR,
    )


def get_tariff_timezone() -> tzinfo:
    """Возвращает московский часовой пояс без обязательного пакета tzdata."""

    try:
        return ZoneInfo(TARIFF_TIMEZONE)
    except ZoneInfoNotFoundError:
        return MOSCOW_TIMEZONE


def is_night_tariff_hour(hour: int) -> bool:
    """Проверяет, попадает ли час в ночной тариф."""

    if NIGHT_TARIFF_START_HOUR < NIGHT_TARIFF_END_HOUR:
        return NIGHT_TARIFF_START_HOUR <= hour < NIGHT_TARIFF_END_HOUR

    return hour >= NIGHT_TARIFF_START_HOUR or hour < NIGHT_TARIFF_END_HOUR


def estimate_deferred_cost(
    requests_count: int,
    *,
    tariff: TariffInfo | None = None,
) -> CostEstimate:
    """Считает стоимость отложенных запросов по тарифу за 1000 запросов."""

    active_tariff = tariff or get_deferred_tariff()
    safe_count = max(requests_count, 0)
    total_price = safe_count / 1000 * active_tariff.price_per_1000

    return CostEstimate(
        requests_count=safe_count,
        price_per_1000=active_tariff.price_per_1000,
        total_price=round(total_price, 2),
        tariff_code=active_tariff.code,
        tariff_label=active_tariff.label,
    )


def estimate_deferred_quota(
    *,
    current_hour: int,
    requested: int,
    limit: int = DEFERRED_REQUEST_HOURLY_QUOTA,
) -> QuotaEstimate:
    """Считает, влезает ли список запросов в лимит текущего часа."""

    safe_current_hour = max(current_hour, 0)
    safe_requested = max(requested, 0)
    safe_limit = max(limit, 0)
    total_after_request = safe_current_hour + safe_requested
    remaining = max(safe_limit - safe_current_hour, 0)

    return QuotaEstimate(
        current_hour=safe_current_hour,
        requested=safe_requested,
        limit=safe_limit,
        remaining=remaining,
        total_after_request=total_after_request,
        over_limit=total_after_request > safe_limit,
    )
