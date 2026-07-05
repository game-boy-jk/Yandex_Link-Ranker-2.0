import base64
import binascii
import os

import time
from typing import Any
from xml.etree import ElementTree

import httpx
from core.models import RawSearchItem
from core.quotas import DEFERRED_REQUEST_SECOND_QUOTA
from core.rate_limit import SlidingWindowRateLimiter
from dotenv import load_dotenv


load_dotenv(override=True)

YANDEX_SEARCH_API_URL = "https://searchapi.api.cloud.yandex.net/v2/web/searchAsync"
YANDEX_OPERATION_API_URL = "https://operation.api.cloud.yandex.net/operations"

YANDEX_API_KEY_ENV = "YANDEX_API_KEY"
YANDEX_FOLDER_ID_ENV = "YANDEX_FOLDER_ID"
YANDEX_SEARCH_TYPE = "SEARCH_TYPE_RU"
YANDEX_REGION = "213"
YANDEX_LANGUAGE = "LOCALIZATION_RU"
YANDEX_PAGE = "0"
YANDEX_MAX_PASSAGES = "5"
YANDEX_MAX_ITEMS_COUNT = 30
YANDEX_DOCS_IN_GROUP = "1"
YANDEX_GROUP_MODE = "GROUP_MODE_DEEP"
YANDEX_RESPONSE_FORMAT = "FORMAT_XML"
YANDEX_REQUEST_TIMEOUT = 10
YANDEX_REQUEST_RETRIES = 3
YANDEX_REQUEST_RETRY_DELAY = 2
YANDEX_OPERATION_POLL_INTERVAL = 3
YANDEX_OPERATION_MAX_POLLS = 180
YANDEX_REQUEST_RATE_LIMITER = SlidingWindowRateLimiter(
    max_calls=DEFERRED_REQUEST_SECOND_QUOTA,
)


class YandexSearchConfigError(RuntimeError):
    """Ошибка настройки доступа к Yandex Search API."""


class YandexSearchResponseError(RuntimeError):
    """Ошибка формата ответа от Yandex Search API."""


def get_yandex_config() -> tuple[str, str]:
    """Берёт API-ключ и folder_id из переменных окружения."""

    api_key = os.getenv(YANDEX_API_KEY_ENV)
    folder_id = os.getenv(YANDEX_FOLDER_ID_ENV)

    if not api_key or not folder_id:
        raise YandexSearchConfigError(
            f"Set {YANDEX_API_KEY_ENV} and {YANDEX_FOLDER_ID_ENV}"
        )

    return api_key, folder_id


def search_yandex(query: str, *, limit: int = 10) -> list[RawSearchItem]:
    """Отправляет отложенный запрос в Yandex Search API и возвращает найденные ссылки. Если найдёт"""

    query = query.strip()
    if not query or limit <= 0:
        return []

    api_key, folder_id = get_yandex_config()
    items_count = min(limit, YANDEX_MAX_ITEMS_COUNT)

    operation = create_yandex_operation(
        api_key=api_key,
        folder_id=folder_id,
        query=query,
        items_count=items_count,
    )
    if operation.get("done"):
        operation_result = operation
    else:
        operation_result = wait_yandex_operation(
            api_key,
            get_operation_id(operation),
        )

    if operation_result.get("error"):
        raise YandexSearchResponseError(
            f"Yandex Search operation failed: {operation_result['error']}"
        )

    return extract_yandex_items(operation_result, limit=limit)


def create_yandex_operation(
    *,
    api_key: str,
    folder_id: str,
    query: str,
    items_count: int,
) -> dict[str, Any]:
    response = request_yandex(
        "POST",
        YANDEX_SEARCH_API_URL,
        headers={
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "query": {
                "searchType": YANDEX_SEARCH_TYPE,
                "queryText": query,
                "page": YANDEX_PAGE,
                "maxPassages": YANDEX_MAX_PASSAGES,
            },
            "groupSpec": {
                "groupMode": YANDEX_GROUP_MODE,
                "groupsOnPage": str(items_count),
                "docsInGroup": YANDEX_DOCS_IN_GROUP,
            },
            "region": YANDEX_REGION,
            "l10N": YANDEX_LANGUAGE,
            "folderId": folder_id,
            "responseFormat": YANDEX_RESPONSE_FORMAT,
        },
    )
    return response.json()


def request_yandex(method: str, url: str, **kwargs: Any) -> httpx.Response:
    last_error: Exception | None = None

    for attempt in range(1, YANDEX_REQUEST_RETRIES + 1):
        try:
            YANDEX_REQUEST_RATE_LIMITER.wait()
            if method == "POST":
                response = httpx.post(
                    url,
                    timeout=YANDEX_REQUEST_TIMEOUT,
                    **kwargs,
                )
            elif method == "GET":
                response = httpx.get(
                    url,
                    timeout=YANDEX_REQUEST_TIMEOUT,
                    **kwargs,
                )
            else:
                raise ValueError(f"Unsupported Yandex request method: {method}")

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            last_error = exc
            if not is_retryable_status(exc.response.status_code):
                raise
        except httpx.RequestError as exc:
            last_error = exc

        if attempt < YANDEX_REQUEST_RETRIES:
            time.sleep(YANDEX_REQUEST_RETRY_DELAY)

    raise YandexSearchResponseError(
        f"Yandex Search request failed after {YANDEX_REQUEST_RETRIES} attempts: "
        f"{last_error}"
    ) from last_error


def is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or status_code >= 500


def get_operation_id(operation: dict[str, Any]) -> str:
    operation_id = operation.get("id")
    if not operation_id:
        raise YandexSearchResponseError("Yandex Search API returned no operation id")

    return str(operation_id)


def wait_yandex_operation(api_key: str, operation_id: str) -> dict[str, Any]:
    url = f"{YANDEX_OPERATION_API_URL}/{operation_id}"
    headers = {"Authorization": f"Api-Key {api_key}"}

    for attempt in range(YANDEX_OPERATION_MAX_POLLS):
        response = request_yandex(
            "GET",
            url,
            headers=headers,
        )
        operation = response.json()

        if operation.get("done"):
            if operation.get("error"):
                raise YandexSearchResponseError(
                    f"Yandex Search operation failed: {operation['error']}"
                )
            if not operation.get("response"):
                raise YandexSearchResponseError(
                    "Yandex Search operation completed without response"
                )
            return operation

        if attempt + 1 < YANDEX_OPERATION_MAX_POLLS:
            time.sleep(YANDEX_OPERATION_POLL_INTERVAL)

    raise YandexSearchResponseError(
        f"Yandex Search operation {operation_id} did not finish in time"
    )


def extract_yandex_items(data: dict[str, Any], *, limit: int) -> list[RawSearchItem]:
    """Приводит XML-ответ Яндекса к формату, который ждёт link_ranker."""

    response = data.get("response")
    if not isinstance(response, dict):
        raise YandexSearchResponseError("Yandex Search API returned no response")

    raw_data = response.get("rawData")
    if not isinstance(raw_data, str):
        raise YandexSearchResponseError("Yandex Search API returned no rawData")

    try:
        xml = base64.b64decode(raw_data, validate=True).decode(
            "utf-8",
            errors="replace",
        )
    except (ValueError, binascii.Error) as exc:
        raise YandexSearchResponseError(
            "Yandex Search API returned invalid rawData"
        ) from exc

    return extract_yandex_xml_items(xml, limit=limit)


def extract_yandex_xml_items(xml: str, *, limit: int) -> list[RawSearchItem]:
    result: list[RawSearchItem] = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise YandexSearchResponseError(
            "Yandex Search API returned invalid XML"
        ) from exc

    for doc in root.iter():
        if local_name(doc.tag) != "doc":
            continue

        url = child_text(doc, "url")
        if not url:
            continue

        item: RawSearchItem = {
            "url": url,
            "title": child_text(doc, "title"),
        }
        passages = child_texts(doc, "passages", "passage")
        if passages:
            item["passages"] = passages
        offers = child_texts(doc, "offers", "offer")
        if offers:
            item["offers"] = offers

        result.append(item)

        if len(result) == limit:
            break

    return result


def child_text(element: ElementTree.Element, name: str) -> str:
    for child in element:
        if local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def child_texts(
    element: ElementTree.Element,
    container_name: str,
    item_name: str,
) -> list[str]:
    for child in element:
        if local_name(child.tag) != container_name:
            continue

        values: list[str] = []
        for item in child:
            if local_name(item.tag) != item_name:
                continue

            value = "".join(item.itertext()).strip()
            if value:
                values.append(value)

        return values

    return []


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
