import re
from decimal import Decimal, InvalidOperation

from core import config
from rapidfuzz import fuzz

MIN_SCORE = config.SCORING_MIN_SCORE

SPACES_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
MM_VALUE_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*мм\b", re.IGNORECASE)
SDS_RE = re.compile(r"\bsds\s*(?:[-+]|\s*plus)?", re.IGNORECASE)

DIMENSION_RE = re.compile(
    r"(?<!\d)"
    r"(\d+(?:[.,]\d+)?)"
    r"\s*(?:x|х|×|\*)\s*"
    r"(\d+(?:[.,]\d+)?)"
    r"(?:\s*(?:x|х|×|\*)\s*(\d+(?:[.,]\d+)?))?"
    r"(?:\s*мм)?",
    re.IGNORECASE,
)

BAD_PAGE_WORDS = {
    "обзор",
    "инструкция",
    "ремонт",
    "запчасти",
    "dummy",
}


def evaluate_search_result(
    *,
    query: str,
    title: str,
    url: str = "",
    passages: list[str] | None = None,
    min_score: int = MIN_SCORE,
) -> tuple[int, str]:
    # Считает итоговую релевантность одной ссылки поисковому запросу.

    query_norm = normalize_text(query)
    title_norm = normalize_text(title)
    target_text_norm = normalize_text(url)
    passages_text = " ".join(passages or [])
    passages_norm = normalize_text(passages_text)

    if not query_norm:
        return 0, "Пустой поисковый запрос"

    if not title_norm and not target_text_norm and not passages_norm:
        return 0, "Нет данных страницы для сравнения"

    target = f"{title_norm} {target_text_norm} {passages_norm}".strip()

    # RapidFuzz дает нечеткое сравнение строк, устойчивое к порядку слов.
    title_score = fuzz.token_set_ratio(query_norm, title_norm) if title_norm else 0
    target_score = (
        fuzz.partial_token_set_ratio(
            query_norm,
            target_text_norm[: config.SCORING_TARGET_TEXT_LIMIT],
        )
        if target_text_norm
        else 0
    )
    passage_score = (
        fuzz.partial_token_set_ratio(
            query_norm,
            passages_norm[: config.SCORING_TARGET_TEXT_LIMIT],
        )
        if passages_norm
        else 0
    )
    title_word_score = token_coverage(query_norm, title_norm)
    target_word_score = token_coverage(
        query_norm,
        target_text_norm[: config.SCORING_TARGET_WORD_TEXT_LIMIT],
    )
    passage_word_score = token_coverage(
        query_norm,
        passages_norm[: config.SCORING_TARGET_WORD_TEXT_LIMIT],
    )
    word_score = max(
        title_word_score,
        target_word_score * config.SCORING_TARGET_WORD_FACTOR,
        passage_word_score * config.SCORING_PASSAGE_WORD_FACTOR,
    )
    digit_score = number_score(query_norm, target)

    title_points = title_score * config.SCORING_TITLE_WEIGHT
    target_points = target_score * config.SCORING_TARGET_WEIGHT
    passage_points = passage_score * config.SCORING_PASSAGE_WEIGHT
    word_points = word_score * config.SCORING_WORDS_WEIGHT
    digit_points = digit_score * config.SCORING_NUMS_WEIGHT

    # Штрафы смотрим по title/h1, чтобы шум в дополнительном тексте не ломал карточку.
    page_penalty, penalty_words = calculate_bad_word_penalty(query_norm, title_norm)
    dimension_penalty, missing_sizes = calculate_size_penalty(query_norm, title_norm)
    title_attribute_points, title_attribute_reason = score_title_attribute_match(
        query_norm,
        title_norm,
    )
    title_penalty = calculate_weak_title_penalty(
        title_score,
        max(target_score, passage_score),
        title_norm,
        title_attribute_points=title_attribute_points,
    )
    # Собираем итоговый балл из fuzzy, слов, чисел и title-атрибутов.
    score = (
        title_points
        + target_points
        + passage_points
        + word_points
        + digit_points
        + title_attribute_points
        - page_penalty
        - dimension_penalty
        - title_penalty
    )

    final = max(
        config.SCORING_MIN_VALUE,
        min(config.SCORING_MAX_VALUE, round(score)),
    )
    status = "Прошло" if final >= min_score else "Не прошло"

    penalty_str = f" ({','.join(penalty_words)})" if penalty_words else ""
    size_str = (
        f", size_penalty=-{dimension_penalty} ({','.join(missing_sizes)})"
        if missing_sizes
        else ""
    )
    title_penalty_str = f", title_penalty=-{title_penalty}" if title_penalty else ""
    title_attribute_str = (
        f", title_attrs={title_attribute_points} ({title_attribute_reason})"
        if title_attribute_reason
        else f", title_attrs={title_attribute_points}"
    )

    return (
        final,
        (
            f"{status}: score={final}, "
            f"title={int(title_score)}, "
            f"target={int(target_score)}, "
            f"passages={int(passage_score)}, "
            f"words={int(word_score)}, "
            f"nums={int(digit_score)}, "
            f"penalty={page_penalty}{penalty_str}{size_str}"
            f"{title_penalty_str}"
            f"{title_attribute_str}"
        ),
    )


def normalize_text(value: str) -> str:
    # Приводит текст к единому виду для fuzzy- и regex-сравнений.

    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^\w\s.,/*+-]+", " ", value)
    return SPACES_RE.sub(" ", value).strip()


def tokens(value: str) -> list[str]:
    # Разбивает текст на слова и отсекает только слишком короткие токены.

    return [
        token
        for token in WORD_RE.findall(value.lower())
        if len(token) >= config.SCORING_MIN_TOKEN_LENGTH
    ]


def token_coverage(query: str, target: str) -> float:
    # Считает, какая доля слов запроса есть в проверяемом тексте.

    query_tokens = set(tokens(query))
    if not query_tokens:
        return 0

    target_tokens = set(tokens(target))
    matched = query_tokens & target_tokens

    return len(matched) / len(query_tokens) * config.SCORING_MAX_VALUE


def number_score(query: str, target: str) -> float:
    # Считает, какая доля чисел из запроса встретилась в данных страницы.

    query_nums = set(NUM_RE.findall(query))
    if not query_nums:
        return config.SCORING_MAX_VALUE

    target_nums = set(NUM_RE.findall(target))
    matched = query_nums & target_nums

    return len(matched) / len(query_nums) * config.SCORING_MAX_VALUE


def calculate_weak_title_penalty(
    title_score: float,
    target_score: float,
    title: str,
    *,
    title_attribute_points: int = 0,
) -> int:
    # Штрафует страницу, если URL похожий, а title слабо подтверждает товар.

    if not title:
        return 0

    if title_attribute_points >= config.SCORING_TITLE_ATTR_PENALTY_BYPASS:
        return 0

    if target_score < config.SCORING_WEAK_TITLE_TARGET_SCORE:
        return 0

    if title_score >= config.SCORING_WEAK_TITLE_SCORE:
        return 0

    return config.SCORING_WEAK_TITLE_PENALTY


def score_title_attribute_match(query: str, target: str) -> tuple[int, str]:
    # Даёт небольшой бонус, когда размеры или SDS-хвостовик совпали в title.

    score = 0
    details: list[str] = []

    query_sizes = extract_sizes(query)
    target_sizes = extract_sizes(target)
    matched_sizes = sorted(query_sizes & target_sizes)

    if matched_sizes:
        score += config.SCORING_TITLE_ATTR_SIZE_POINTS
        details.append(f"sizes={','.join(matched_sizes)}")

    query_values = extract_mm_values(query)
    target_values = extract_mm_values(target)
    matched_values = sorted(query_values & target_values, key=float)

    if matched_values and not matched_sizes:
        if len(matched_values) >= 2:
            score += config.SCORING_TITLE_ATTR_MULTI_MM_POINTS
        else:
            score += config.SCORING_TITLE_ATTR_SINGLE_MM_POINTS
        details.append(f"mm={','.join(matched_values)}")

    if has_sds(query) and has_sds(target):
        score += config.SCORING_TITLE_ATTR_SDS_POINTS
        details.append("shank=sds")

    return min(config.SCORING_TITLE_ATTR_MAX_POINTS, score), "; ".join(details)


def extract_mm_values(value: str) -> set[str]:
    # Извлекает числовые значения размеров в миллиметрах из текста.

    values = {normalize_number(item) for item in MM_VALUE_RE.findall(value)}

    for match in DIMENSION_RE.findall(value):
        values.update(normalize_number(item) for item in match if item)

    for match in re.findall(
        r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:x|х|×|\*)\s*"
        r"(\d+(?:[.,]\d+)?)(?:\s*/\s*(\d+(?:[.,]\d+)?))?\s*(?:мм)?",
        value,
        re.IGNORECASE,
    ):
        values.update(normalize_number(item) for item in match if item)

    return values


def has_sds(value: str) -> bool:
    # Проверяет, упоминается ли SDS/SDS+ хвостовик.

    return bool(SDS_RE.search(value))


def calculate_size_penalty(query: str, target: str) -> tuple[int, list[str]]:
    # Штрафует title, если в нём указан другой размер из запроса.

    query_sizes = extract_sizes(query)
    if not query_sizes:
        return 0, []

    target_sizes = extract_sizes(target)
    if not target_sizes:
        return 0, []

    missing_sizes = sorted(query_sizes - target_sizes)
    if missing_sizes:
        return config.SCORING_SIZE_MISMATCH_PENALTY, missing_sizes

    return 0, []


def extract_sizes(value: str) -> set[str]:
    # Извлекает размеры формата 600x900, 20х460, 20*460.

    found: set[str] = set()

    for left, middle, right in DIMENSION_RE.findall(value):
        parts = [part for part in (left, middle, right) if part]
        found.add(normalize_dimension(parts))

    return found


def normalize_dimension(parts: list[str]) -> str:
    # Нормализует размер, чтобы 320x240 и 240x320 считались одним размером.

    normalized_parts = [normalize_number(part) for part in parts]
    return "x".join(sorted(normalized_parts, key=dimension_sort_key))


def dimension_sort_key(value: str) -> Decimal:
    # Преобразует часть размера в число для стабильной сортировки.
    # Сам Decimal не допускает 0.1 + 0.2 = 0.300000000211

    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal(0)


def normalize_number(value: str) -> str:
    # Убирает лишние нули и приводит десятичную запятую к точке. Чтобы было проще
    # в размерах: 600,0 -> 600, 10.50 -> 10.5.
    value = value.replace(",", ".")
    if "." not in value:
        return value

    return value.rstrip("0").rstrip(".")


def calculate_bad_word_penalty(query: str, target: str) -> tuple[int, list[str]]:
    # Штрафует title за плохие слова =), характерные для нецелевых страниц.

    query_tokens = set(tokens(query))
    target_tokens = set(tokens(target))

    bad_words = BAD_PAGE_WORDS & target_tokens
    unexpected_bad_words = bad_words - query_tokens

    if unexpected_bad_words:
        return config.SCORING_BAD_WORD_PENALTY, sorted(unexpected_bad_words)

    return 0, []
