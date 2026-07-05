# Сколько ссылок показываем пользователю и сколько результатов берём из Яндекса.
TOP_LINKS_LIMIT = 3
SEARCH_FETCH_LIMIT = 30

# Общие границы итогового скоринга.
SCORING_MIN_SCORE = 68
SCORING_FALLBACK_MIN_SCORE = 62
SCORING_MIN_VALUE = 0
SCORING_MAX_VALUE = 100
SCORING_MIN_TOKEN_LENGTH = 2

# Веса основных источников релевантности: title, url, passages, слова и числа.
SCORING_TITLE_WEIGHT = 0.45
SCORING_TARGET_WEIGHT = 0.15
SCORING_PASSAGE_WEIGHT = 0.20
SCORING_WORDS_WEIGHT = 0.30
SCORING_NUMS_WEIGHT = 0.10

# URL/passages шумнее title, поэтому их словесное покрытие ослабляем.
SCORING_TARGET_WORD_FACTOR = 0.40
SCORING_PASSAGE_WORD_FACTOR = 0.70

# Ограничения длины текста, чтобы не гонять fuzzy/regex по слишком большим строкам.
SCORING_TARGET_TEXT_LIMIT = 20_000
SCORING_TARGET_WORD_TEXT_LIMIT = 5_000

# Штраф за слабый title, когда совпадение держится в основном на url/passages.
SCORING_WEAK_TITLE_SCORE = 60
SCORING_WEAK_TITLE_TARGET_SCORE = 80
SCORING_WEAK_TITLE_PENALTY = 20
SCORING_TITLE_ATTR_PENALTY_BYPASS = 15

# Бонусы за подтверждение важных характеристик прямо в title.
SCORING_TITLE_ATTR_SIZE_POINTS = 15
SCORING_TITLE_ATTR_MULTI_MM_POINTS = 15
SCORING_TITLE_ATTR_SINGLE_MM_POINTS = 8
SCORING_TITLE_ATTR_SDS_POINTS = 5
SCORING_TITLE_ATTR_MAX_POINTS = 20
SCORING_OFFERS_BONUS = 5

# Штрафы за явные признаки неподходящей страницы.
SCORING_SIZE_MISMATCH_PENALTY = 35
SCORING_BAD_WORD_PENALTY = 30
SCORING_CONTENT_TERM_MISMATCH_PENALTY = 15
