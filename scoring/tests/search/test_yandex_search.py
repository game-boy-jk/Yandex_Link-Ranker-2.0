import base64

import pytest
import httpx

import search.yandex_search as yandex_search
from search.yandex_search import YandexSearchConfigError, search_yandex


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeRateLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


@pytest.fixture(autouse=True)
def fake_rate_limiter(monkeypatch) -> FakeRateLimiter:
    limiter = FakeRateLimiter()
    monkeypatch.setattr(yandex_search, "YANDEX_REQUEST_RATE_LIMITER", limiter)
    return limiter


def test_search_yandex_posts_async_request_and_polls_operation(
    monkeypatch,
    fake_rate_limiter,
):
    raw_xml = """
    <yandexsearch>
      <response>
        <results>
          <grouping>
            <group>
              <doc>
                <url>https://example.test/product</url>
                <title>Насос</title>
              </doc>
            </group>
          </grouping>
        </results>
      </response>
    </yandexsearch>
    """
    raw_data = base64.b64encode(raw_xml.encode("utf-8")).decode("ascii")

    def fake_post(url, *, headers, json, timeout):
        assert url == yandex_search.YANDEX_SEARCH_API_URL
        assert headers == {
            "Authorization": "Api-Key test-key",
            "Content-Type": "application/json",
        }
        assert json == {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": "насос",
                "page": "0",
                "maxPassages": "5",
            },
            "groupSpec": {
                "groupMode": "GROUP_MODE_DEEP",
                "groupsOnPage": str(min(20, yandex_search.YANDEX_MAX_ITEMS_COUNT)),
                "docsInGroup": "1",
            },
            "region": "213",
            "l10N": "LOCALIZATION_RU",
            "folderId": "test-folder",
            "responseFormat": "FORMAT_XML",
        }
        assert timeout == yandex_search.YANDEX_REQUEST_TIMEOUT
        return FakeResponse({"done": False, "id": "operation-id"})

    def fake_get(url, *, headers, timeout):
        assert url == f"{yandex_search.YANDEX_OPERATION_API_URL}/operation-id"
        assert headers == {"Authorization": "Api-Key test-key"}
        assert timeout == yandex_search.YANDEX_REQUEST_TIMEOUT
        return FakeResponse(
            {
                "done": True,
                "response": {
                    "@type": "type.googleapis.com/yandex.cloud.searchapi.v2.WebSearchResponse",
                    "rawData": raw_data,
                },
            }
        )

    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    monkeypatch.setattr(yandex_search.httpx, "post", fake_post)
    monkeypatch.setattr(yandex_search.httpx, "get", fake_get)

    assert search_yandex(" насос ", limit=20) == [
        {"url": "https://example.test/product", "title": "Насос"}
    ]
    assert fake_rate_limiter.calls == 2


def test_search_yandex_requires_api_key_and_folder_id(monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)

    with pytest.raises(YandexSearchConfigError):
        search_yandex("насос")


def test_extract_yandex_xml_items_includes_passages():
    raw_xml = """
    <yandexsearch>
      <response>
        <results>
          <grouping>
            <group>
              <doc>
                <url>https://example.test/sneakers-red</url>
                <title>Купить красные кроссовки</title>
                <passages>
                  <passage>Продажа <hlword>красных кроссовок</hlword> Nike.</passage>
                  <passage>Модель Air Max доступна в размерах 40-45.</passage>
                </passages>
                <offers>
                  <offer>
                    <name>Кроссовки Nike Air Max красные</name>
                    <price>5990</price>
                    <currency>RUB</currency>
                    <seller>Example Store</seller>
                  </offer>
                </offers>
              </doc>
            </group>
          </grouping>
        </results>
      </response>
    </yandexsearch>
    """

    assert yandex_search.extract_yandex_xml_items(raw_xml, limit=10) == [
        {
            "url": "https://example.test/sneakers-red",
            "title": "Купить красные кроссовки",
            "passages": [
                "Продажа красных кроссовок Nike.",
                "Модель Air Max доступна в размерах 40-45.",
            ],
            "offers": [
                "Кроссовки Nike Air Max красные\n                    "
                "5990\n                    "
                "RUB\n                    "
                "Example Store",
            ],
        }
    ]


def test_search_yandex_retries_transient_connect_timeout(monkeypatch):
    raw_xml = """
    <yandexsearch>
      <response>
        <results>
          <grouping>
            <group>
              <doc>
                <url>https://example.test/retry-product</url>
                <title>Бур</title>
              </doc>
            </group>
          </grouping>
        </results>
      </response>
    </yandexsearch>
    """
    raw_data = base64.b64encode(raw_xml.encode("utf-8")).decode("ascii")
    calls = 0

    def fake_post(url, *, headers, json, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectTimeout("_ssl.c:1063: handshake operation timed out")
        return FakeResponse(
            {
                "done": True,
                "response": {
                    "@type": "type.googleapis.com/yandex.cloud.searchapi.v2.WebSearchResponse",
                    "rawData": raw_data,
                },
            }
        )

    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    monkeypatch.setattr(yandex_search.httpx, "post", fake_post)
    monkeypatch.setattr(yandex_search.time, "sleep", lambda seconds: None)

    assert search_yandex("бур", limit=10) == [
        {"url": "https://example.test/retry-product", "title": "Бур"}
    ]
    assert calls == 2
