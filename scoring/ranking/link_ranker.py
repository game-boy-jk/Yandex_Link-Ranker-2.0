import logging
import re
from collections.abc import Callable
from urllib.parse import quote, urlparse

import httpx
from core import config
from core.blocklists import NON_PRODUCT_DOMAINS
from core.models import RankedLink, RawSearchItem, SearchItem
from core.text_utils import clean_text
from ranking.hybrid_scoring import MIN_SCORE, evaluate_search_result
from search.yandex_search import (
    YandexSearchConfigError,
    YandexSearchResponseError,
    search_yandex,
)

logger = logging.getLogger(__name__)


FETCH_LIMIT = config.SEARCH_FETCH_LIMIT
TOP_LIMIT = config.TOP_LINKS_LIMIT
MODEL_IDENTIFIER_RE = re.compile(
    r"\b[a-zа-яё0-9]+(?:[-_/][a-zа-яё0-9]+)+\b",
    re.IGNORECASE,
)
DOT_IDENTIFIER_RE = re.compile(r"\b\d{2,4}(?:\.\d{1,4}){2,}\b")
COMPACT_IDENTIFIER_RE = re.compile(
    r"\b(?=[a-zа-яё0-9]{4,}\b)(?=[a-zа-яё0-9]*\d)"
    r"[a-zа-яё]{1,8}\d[a-zа-яё0-9]*\b",
    re.IGNORECASE,
)
CONTENT_WORD_RE = re.compile(r"\b[a-zа-яё][a-zа-яё0-9]*\b", re.IGNORECASE)

# Минимальная длина слова, которое считаем товарным термином.
# Короткие слова часто дают шум и не помогают подтвердить товар.
CONTENT_TOKEN_MIN_LENGTH = 4

# Минимальная длина слова для мягкого сравнения по общему началу.
# Например, чтобы близкие формы одного слова могли совпасть по префиксу.
CONTENT_PREFIX_MIN_LENGTH = 5
SearchProductFunction = Callable[..., list[RawSearchItem]]


def find_product_links(
    product: str,
    *,
    limit: int = TOP_LIMIT,
    search_limit: int = FETCH_LIMIT,
    unique_domain: bool = True,
) -> list[str]:
    return find_product_links_with_search(
        product,
        limit=limit,
        search_limit=search_limit,
        unique_domain=unique_domain,
        search=search_product,
    )


def find_product_links_strict(
    product: str,
    *,
    limit: int = TOP_LIMIT,
    search_limit: int = FETCH_LIMIT,
    unique_domain: bool = True,
) -> list[str]:
    """Ищет ссылки без скрытия ошибок Yandex Search API."""

    return find_product_links_with_search(
        product,
        limit=limit,
        search_limit=search_limit,
        unique_domain=unique_domain,
        search=search_product_strict,
    )


def find_product_links_with_search(
    product: str,
    *,
    limit: int,
    search_limit: int,
    unique_domain: bool,
    search: SearchProductFunction,
) -> list[str]:
    query = product.strip()
    if not query:
        return []

    search_items = search(query, limit=search_limit)
    if not search_items:
        return []

    return select_top_urls(
        query,
        search_items,
        limit=limit,
        unique_domain=unique_domain,
    )


def search_product(query: str, *, limit: int = FETCH_LIMIT) -> list[RawSearchItem]:
    query = query.strip()
    if not query:
        return []

    search_items: list[RawSearchItem] = []
    seen_urls: set[str] = set()

    for item in fetch_search_items(query, limit=limit):
        url = extract_field(item, "url", "link", "href")
        if not url or url in seen_urls or is_bad_url(url):
            continue

        search_items.append(item)
        seen_urls.add(url)

    return search_items


def search_product_strict(
    query: str,
    *,
    limit: int = FETCH_LIMIT,
) -> list[RawSearchItem]:
    """Возвращает результаты поиска, не скрывая ошибки Yandex API."""

    query = query.strip()
    if not query:
        return []

    search_items: list[RawSearchItem] = []
    seen_urls: set[str] = set()

    for item in fetch_search_items_strict(query, limit=limit):
        url = extract_field(item, "url", "link", "href")
        if not url or url in seen_urls or is_bad_url(url):
            continue

        search_items.append(item)
        seen_urls.add(url)

    return search_items


def fetch_search_items(
    query: str,
    *,
    limit: int,
) -> list[RawSearchItem]:
    try:
        return fetch_search_items_strict(query, limit=limit)
    except YandexSearchConfigError as exc:
        logger.warning("search_error: %s. Use test_data/sample_query.json.", exc)
        return []
    except (YandexSearchResponseError, httpx.HTTPError) as exc:
        logger.warning("search_error: %s", exc)
        return []


def fetch_search_items_strict(
    query: str,
    *,
    limit: int,
) -> list[RawSearchItem]:
    """Запрашивает Yandex Search API без скрытия ошибок."""

    return search_yandex(query, limit=limit)


def select_top_urls(
    query: str,
    search_items: list[RawSearchItem],
    *,
    limit: int = TOP_LIMIT,
    unique_domain: bool = True,
) -> list[str]:
    ranked_links = rank_links(
        query,
        search_items,
        limit=limit,
        unique_domain=unique_domain,
    )
    return [browser_safe_url(ranked_link.url) for ranked_link in ranked_links]


def rank_links(
    query: str,
    search_items: list[RawSearchItem],
    *,
    limit: int = TOP_LIMIT,
    min_score: int = MIN_SCORE,
    unique_domain: bool = True,
) -> list[RankedLink]:
    ranked_links: list[RankedLink] = []
    fallback_links: list[RankedLink] = []

    for idx, raw_item in enumerate(search_items, start=1):
        item = parse_item(raw_item)
        logger.debug("[%d] URL: %s", idx, item.url or "<empty>")

        if not item.url or is_bad_url(item.url):
            logger.debug("SKIP: empty or unsupported URL\n---")
            continue

        target_text = (
            f"{item.title} {item.url} {' '.join(item.passages)} {' '.join(item.offers)}"
        ).strip()

        if not has_required_identifier_match(query, target_text):
            logger.debug(
                "title: %s\nSKIP: model identifier mismatch\n---",
                item.title,
            )
            continue

        score, reason = evaluate_search_result(
            query=query,
            title=item.title,
            url=item.url,
            passages=list(item.passages),
            min_score=min_score,
        )
        if not has_required_content_term_match(query, target_text):
            score = max(
                config.SCORING_MIN_VALUE,
                score - config.SCORING_CONTENT_TERM_MISMATCH_PENALTY,
            )
            reason = (
                f"{reason}, "
                f"content_term_penalty=-{config.SCORING_CONTENT_TERM_MISMATCH_PENALTY}"
                f", adjusted_score={score}"
            )
        if item.offers:
            score = min(config.SCORING_MAX_VALUE, score + config.SCORING_OFFERS_BONUS)
            reason = (
                f"{reason}, offers_bonus=+{config.SCORING_OFFERS_BONUS}"
                f", adjusted_score={score}"
            )

        ranked_link = RankedLink(
            url=item.url,
            score=score,
            title=item.title,
            reason=reason,
            source_pos=idx,
        )

        if score < min_score:
            logger.debug("title: %s\nREJECT: %s\n---", item.title, reason)
            if min_score == MIN_SCORE and score >= config.SCORING_FALLBACK_MIN_SCORE:
                fallback_links.append(ranked_link)
            continue

        logger.debug("title: %s\nPASS: %s\n---", item.title, reason)
        ranked_links.append(ranked_link)

    sort_ranked_links(ranked_links)
    sort_ranked_links(fallback_links)
    return limit_rows(
        ranked_links,
        fallback_links=fallback_links,
        limit=limit,
        unique_domain=unique_domain,
    )


def parse_item(raw_item: RawSearchItem) -> SearchItem:
    return SearchItem(
        url=extract_field(raw_item, "url", "link", "href"),
        title=extract_field(raw_item, "title", "name"),
        passages=extract_passages(raw_item),
        offers=extract_offers(raw_item),
    )


def extract_passages(raw_item: RawSearchItem) -> tuple[str, ...]:
    value = raw_item.get("passages")
    if isinstance(value, list):
        return tuple(clean_text(str(item)) for item in value if item)
    if value:
        return (clean_text(str(value)),)
    return ()


def extract_offers(raw_item: RawSearchItem) -> tuple[str, ...]:
    value = raw_item.get("offers")
    if isinstance(value, list):
        return tuple(clean_text(str(item)) for item in value if item)
    if value:
        return (clean_text(str(value)),)
    return ()


def extract_field(raw_item: RawSearchItem, *names: str) -> str:
    for name in names:
        value = raw_item.get(name)
        if isinstance(value, list):
            return clean_text(" ".join(map(str, value)))
        if value:
            return clean_text(str(value))
    return ""


def is_bad_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    host = extract_domain(url)
    if host == "yandex.ru" and parsed.path.startswith("/images"):
        return True
    if any(
        host == domain or host.endswith(f".{domain}") for domain in NON_PRODUCT_DOMAINS
    ):
        return True
    return False


def has_required_identifier_match(query: str, target: str) -> bool:
    identifiers = extract_model_identifiers(query)
    if not identifiers:
        return True

    normalized_target = normalize_identifier_text(target)
    return any(identifier in normalized_target for identifier in identifiers)


def extract_model_identifiers(text: str) -> set[str]:
    identifiers: set[str] = set()
    normalized_text = normalize_identifier_text(text)

    identifiers.update(DOT_IDENTIFIER_RE.findall(normalized_text))
    identifiers.update(COMPACT_IDENTIFIER_RE.findall(normalized_text))

    for raw_identifier in MODEL_IDENTIFIER_RE.findall(normalized_text):
        if not raw_identifier[0].isalpha():
            continue
        if not any(char.isdigit() for char in raw_identifier):
            continue
        if not any(char.isalpha() for char in raw_identifier):
            continue
        identifiers.add(raw_identifier)

    return identifiers


def has_required_content_term_match(query: str, target: str) -> bool:
    if not extract_model_identifiers(query):
        return True

    query_terms = extract_content_terms(query)
    if not query_terms:
        return True

    target_terms = extract_content_terms(target)
    return any(
        content_terms_match(query_term, target_term)
        for query_term in query_terms
        for target_term in target_terms
    )


def extract_content_terms(text: str) -> set[str]:
    normalized_text = normalize_identifier_text(text)
    identifiers = extract_model_identifiers(normalized_text)
    terms: set[str] = set()

    for token in CONTENT_WORD_RE.findall(normalized_text):
        if len(token) < CONTENT_TOKEN_MIN_LENGTH:
            continue
        if token in identifiers:
            continue
        if token.isdigit() or any(char.isdigit() for char in token):
            continue

        terms.add(token)

    return terms


def content_terms_match(left: str, right: str) -> bool:
    if left == right:
        return True

    min_length = min(len(left), len(right))
    if min_length < CONTENT_PREFIX_MIN_LENGTH:
        return False

    return left[:min_length] == right[:min_length]


def normalize_identifier_text(text: str) -> str:
    value = text.lower().replace("ё", "е")
    value = value.replace("×", "x").replace("х", "x")
    return re.sub(r"\s+", " ", value)


def extract_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def browser_safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    return parsed._replace(
        path=quote(parsed.path, safe="/%:@!$&'*,;=+-._~"),
        query=quote(parsed.query, safe="=&?/%:@!$'*,;+-._~"),
        fragment=quote(parsed.fragment, safe="/?%:@!$&'*,;=+-._~"),
    ).geturl()


def sort_ranked_links(ranked_links: list[RankedLink]) -> None:
    ranked_links.sort(
        key=lambda ranked_link: (
            -ranked_link.score,
            ranked_link.source_pos,
        )
    )


def limit_rows(
    ranked_links: list[RankedLink],
    *,
    fallback_links: list[RankedLink] | None = None,
    limit: int,
    unique_domain: bool,
) -> list[RankedLink]:
    selected: list[RankedLink] = []
    seen: set[str] = set()

    for ranked_link in [*ranked_links, *(fallback_links or [])]:
        row_domain = extract_domain(ranked_link.url)
        if unique_domain and row_domain in seen:
            continue

        selected.append(ranked_link)
        seen.add(row_domain)

        if len(selected) == limit:
            break

    return selected
