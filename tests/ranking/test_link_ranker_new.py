import ranking.link_ranker as link_ranker
from ranking.link_ranker import (
    fetch_search_items,
    find_product_links,
    find_product_links_strict,
    has_required_content_term_match,
    has_required_identifier_match,
    is_bad_url,
    rank_links,
    select_top_urls,
)
from search.yandex_search import YandexSearchResponseError


QUERY = "Жилет женский фирменный, размер 96/164"

ITEMS = [
    {
        "url": "https://example.test/catalog/vest-96-164",
        "title": "Жилет женский фирменный размер 96/164",
    }
]


def test_rank_links_scores_raw_yandex_title():
    rows = rank_links(QUERY, ITEMS)

    assert len(rows) == 1
    assert rows[0].url == ITEMS[0]["url"]
    assert rows[0].score >= 70


def test_rank_links_adds_small_bonus_for_offers():
    items = [
        {
            "url": ITEMS[0]["url"],
            "title": ITEMS[0]["title"],
            "offers": ["Жилет женский фирменный размер 96/164 1000 RUB"],
        }
    ]

    base_row = rank_links(QUERY, ITEMS)[0]
    offer_row = rank_links(QUERY, items)[0]

    assert offer_row.score == min(100, base_row.score + 5)
    assert "offers_bonus=+5" in offer_row.reason


def test_rank_links_does_not_score_search_snippet():
    items = [
        {
            "url": "https://example.test/catalog/vests",
            "title": "Жилет из пуха яка",
            "snippet": "Жилет женский фирменный, размер 96/164, купить",
        }
    ]

    assert rank_links(QUERY, items) == []


def test_rank_links_skips_empty_url_and_allows_file_url():
    items = [
        {"url": "", "title": QUERY},
        {"url": "https://example.test/file.pdf", "title": QUERY},
        {"url": "https://example.test/product", "title": QUERY},
    ]

    rows = rank_links(QUERY, items, unique_domain=False)

    assert [row.url for row in rows] == [
        "https://example.test/file.pdf",
        "https://example.test/product",
    ]


def test_rank_links_skips_reference_sites():
    items = [
        {"url": "https://studfile.net/preview/12409812/page:3/", "title": QUERY},
        {"url": "https://lektsia.com/8x2274.html", "title": QUERY},
        {"url": "https://ru.wikipedia.org/wiki/Двигатель", "title": QUERY},
    ]

    assert rank_links("Двигатель тяговый ДАТЭ-170-4У2", items) == []


def test_model_identifier_must_match_when_query_has_model_code():
    assert has_required_identifier_match(
        "Двигатель тяговый ДАТЭ-170-4У2 № 17251",
        "Двигатель тяговый ДАТЭ-170-4У2 купить",
    )
    assert not has_required_identifier_match(
        "Двигатель тяговый ДАТЭ-170-4У2 № 17251",
        "Двигатель в сборе купить",
    )


def test_dot_separated_identifier_must_match():
    assert has_required_identifier_match(
        "Диафрагма 845.01.736",
        "Диафрагма резиновая 845.01.736",
    )
    assert not has_required_identifier_match(
        "Диафрагма 845.01.736",
        "Диафрагма резиновая МТВ-8002",
    )


def test_common_specs_do_not_require_model_identifier_match():
    assert has_required_identifier_match(
        "IP-телефон с дисплеем 240x320 Li-ion 2600мА/ч",
        "IP-телефон с дисплеем 320x240",
    )


def test_compact_identifier_is_treated_as_model_code():
    assert has_required_identifier_match(
        "Микросхема КР574УД2А",
        "КР574УД2А Купить в Москве",
    )


def test_content_term_must_match_when_query_has_model_code():
    assert has_required_content_term_match(
        "Микросхема КР574УД2А",
        "КР574УД2А микросхема DIP-8",
    )
    assert not has_required_content_term_match(
        "Микросхема КР574УД2А",
        "КР574УД2А Операционный усилитель сдвоенный с низким уровнем шума",
    )


def test_commercial_words_can_confirm_model_code_query():
    assert has_required_content_term_match(
        "Микросхема КР574УД2А купить",
        "КР574УД2А купить в Москве",
    )


def test_rank_links_can_use_url_for_identifier_match():
    query = "Диафрагма 845.01.736"
    items = [
        {
            "url": "https://shop.test/catalog/845.01.736",
            "title": "Диафрагма резиновая 845.01.736",
        }
    ]

    rows = rank_links(query, items)

    assert [row.url for row in rows] == [items[0]["url"]]


def test_rank_links_rejects_code_only_match_without_product_term():
    query = "Микросхема КР574УД2А"
    items = [
        {
            "url": "https://shop.test/kr574ud2a",
            "title": "Микросхема КР574УД2А DIP-8",
            "passages": ["Микросхема КР574УД2А есть в наличии."],
        },
        {
            "url": "https://onelec.ru/products/kr574ud2a",
            "title": "КР574УД2А Купить в Москве",
            "passages": [
                "Операционный усилитель сдвоенный с низким уровнем шума.",
            ],
        },
    ]

    rows = rank_links(query, items)

    assert [row.url for row in rows] == ["https://shop.test/kr574ud2a"]


def test_rank_links_can_use_passages_for_identifier_match():
    query = "Диафрагма резиновая 845.01.736"
    items = [
        {
            "url": "https://shop.test/catalog/product",
            "title": "Диафрагма резиновая",
            "passages": ["Диафрагма резиновая 845.01.736 есть в наличии."],
        }
    ]

    rows = rank_links(query, items)

    assert [row.url for row in rows] == [items[0]["url"]]


def test_rank_links_rejects_items_below_strict_threshold():
    query = (
        "Бур для перфоратора по бетону, диаметр 20мм, общая длина 460мм, хвостовик SDS+"
    )
    items = [
        {
            "url": "https://shop.test/drill-20-460-sds",
            "title": '038-067 бур SDS+ 20х400/460мм серия "Профи"',
        }
    ]

    assert rank_links(query, items, min_score=90) == []


def test_select_top_urls_returns_only_urls():
    assert select_top_urls(QUERY, ITEMS) == [ITEMS[0]["url"]]


def test_select_top_urls_encodes_parentheses_for_clickable_output():
    query = "cable kppgng(a)-hf 10x1"
    items = [{"url": "https://example.test/kppgng(a)-hf-10h1/", "title": query}]

    assert select_top_urls(query, items) == [
        "https://example.test/kppgng%28a%29-hf-10h1/"
    ]


def test_find_product_links_searches_product_and_returns_three_urls(monkeypatch):
    items = [
        {"url": "https://shop-a.test/product", "title": QUERY},
        {"url": "https://shop-b.test/product", "title": QUERY},
        {"url": "https://shop-c.test/product", "title": QUERY},
        {"url": "https://shop-d.test/product", "title": QUERY},
    ]

    def fake_search_product(query, *, limit):
        assert query == QUERY
        assert limit == link_ranker.FETCH_LIMIT
        return items

    monkeypatch.setattr(link_ranker, "search_product", fake_search_product)

    assert find_product_links(QUERY) == [
        "https://shop-a.test/product",
        "https://shop-b.test/product",
        "https://shop-c.test/product",
    ]


def test_find_product_links_ignores_empty_product(monkeypatch):
    def fail_search_product(query, *, limit):
        raise AssertionError("search should not be called for an empty product")

    monkeypatch.setattr(link_ranker, "search_product", fail_search_product)

    assert find_product_links("   ") == []


def test_find_product_links_strict_does_not_hide_yandex_errors(monkeypatch):
    def fail_search_yandex(query, *, limit):
        raise YandexSearchResponseError("operation failed")

    monkeypatch.setattr(link_ranker, "search_yandex", fail_search_yandex)

    assert find_product_links(QUERY) == []

    try:
        find_product_links_strict(QUERY)
    except YandexSearchResponseError as exc:
        assert str(exc) == "operation failed"
    else:
        raise AssertionError("strict search should raise YandexSearchResponseError")


def test_fetch_search_items_uses_yandex_search_api(monkeypatch):
    def fake_search_yandex(query, *, limit):
        assert query == QUERY
        assert limit == 10
        return [
            {"url": "https://shop-a.test/product", "title": "First"},
            {"url": "https://shop-b.test/product", "title": "Second"},
        ]

    monkeypatch.setattr(link_ranker, "search_yandex", fake_search_yandex)

    assert fetch_search_items(QUERY, limit=10) == [
        {"url": "https://shop-a.test/product", "title": "First"},
        {"url": "https://shop-b.test/product", "title": "Second"},
    ]


def test_search_product_uses_original_query_once(monkeypatch):
    def fake_fetch_search_items(query, *, limit):
        assert query == QUERY
        assert limit == 15
        return [
            {"url": f"https://shop-{index}.test/product", "title": QUERY}
            for index in range(limit)
        ]

    monkeypatch.setattr(link_ranker, "fetch_search_items", fake_fetch_search_items)

    items = link_ranker.search_product(QUERY, limit=15)

    assert len(items) == 15


def test_rank_links_respects_unique_domain():
    items = [
        {"url": "https://shop.test/product-1", "title": QUERY},
        {"url": "https://shop.test/product-2", "title": QUERY},
        {"url": "https://other.test/product-3", "title": QUERY},
    ]

    rows = rank_links(QUERY, items, unique_domain=True)

    assert [row.url for row in rows] == [
        "https://shop.test/product-1",
        "https://other.test/product-3",
    ]


def test_file_url_is_allowed():
    assert is_bad_url("https://example.test/file.pdf") is False
    assert is_bad_url("https://example.test/product") is False


def test_non_product_domains_are_skipped():
    assert is_bad_url("https://www.avito.ru/moskva?q=гайка") is True
    assert is_bad_url("https://ru.pinterest.com/pin/123") is True
    assert is_bad_url("https://www.pinterest.com/pin/123") is True
    assert is_bad_url("https://base.garant.ru/123456/") is True
    assert is_bad_url("https://internet-law.ru/gosts/gost/1234/") is True
    assert (
        is_bad_url("https://istock.info/standard/00000000-0009-065f-17a7-9c1bb514641a")
        is True
    )
    assert is_bad_url("https://normadocs.ru/gost_25788-83") is True
    assert is_bad_url("https://satisfactory-game.fandom.com/ru/wiki/Мотор") is True
    assert is_bad_url("https://example.test/product") is False


def test_search_listing_url_is_allowed():
    assert is_bad_url("https://vkupiprodai.ru/search/page1/?q=пиджак") is False
    assert is_bad_url("https://market.yandex.ru/search?text=пиджак") is False
    assert is_bad_url("https://example.test/product?id=123") is False


def test_yandex_images_search_url_is_skipped():
    assert (
        is_bad_url(
            "https://yandex.ru/images/search?text=%D0%BC%D0%B5%D1%82%D0%BB%D0%B0"
        )
        is True
    )
    assert is_bad_url("https://www.yandex.ru/images/search?text=лопата") is True
    assert is_bad_url("https://market.yandex.ru/search?text=лопата") is False
