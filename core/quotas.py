DEFERRED_REQUEST_HOURLY_QUOTA = 35_000
DEFERRED_REQUEST_SECOND_QUOTA = 10


class QuotaLimitError(ValueError):
    """Запрос не выполнен из-за локального лимита API."""
