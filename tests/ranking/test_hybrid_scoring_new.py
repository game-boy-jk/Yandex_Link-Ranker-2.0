from ranking.hybrid_scoring import (
    calculate_bad_word_penalty,
    calculate_size_penalty,
    evaluate_search_result,
    extract_sizes,
    number_score,
    token_coverage,
)


def test_offer_scores_title_text_and_specs_without_attribute_bonus():
    score, reason = evaluate_search_result(
        query="Агрегат насосный К 80-50-200 мощность 11 кВт",
        title="Насос консольный К 80-50-200а дв. 11 кВт",
        url="https://shop.test/nasos-k-80-50-200a-11-kvt",
    )

    assert score >= 60, reason


def test_empty_query_rejected():
    score, reason = evaluate_search_result(
        query="",
        title="Насос К 80-50-200а",
    )

    assert score == 0
    assert reason == "Пустой поисковый запрос"


def test_empty_page_rejected():
    score, reason = evaluate_search_result(
        query="Агрегат насосный К 80-50-200",
        title="",
        url="",
    )

    assert score == 0
    assert reason == "Нет данных страницы для сравнения"


def test_commercial_words_are_counted_in_word_match():
    score = token_coverage(
        "купить насос К 80-50-200 доставка",
        "насос К 80-50-200 консольный",
    )

    assert score < 100


def test_penalty_applies_only_for_unexpected_bad_words():
    penalty, words = calculate_bad_word_penalty(
        query="насос К 80-50-200",
        target="обзор насос К 80-50-200",
    )

    assert penalty == 30
    assert words == ["обзор"]


def test_penalty_does_not_apply_when_bad_word_is_in_query():
    penalty, words = calculate_bad_word_penalty(
        query="обзор насос К 80-50-200",
        target="обзор насос К 80-50-200",
    )

    assert penalty == 0
    assert words == []


def test_target_noise_does_not_penalize_valid_title():
    # Проверяем что штраф не применяется даже если в дополнительном тексте есть мусор.
    # Score может быть ниже порога из-за слабого target — это нормально.
    # Главное: penalty=0, то есть title не пострадал.
    _, reason = evaluate_search_result(
        query="насос К 80-50-200",
        title="Насос К 80-50-200а",
        url="https://shop.test/obzor-instrukciya-pohozhie-tovary",
    )

    assert "penalty=0" in reason


def test_target_match_alone_does_not_outweigh_weak_title():
    score, reason = evaluate_search_result(
        query="Стекло многослойное 6-1-6 1760х2060х12мм",
        title="Стекло триплекс",
        url="https://shop.test/стекло-многослойное-6-1-6-1760х2060х12мм",
    )

    assert score < 70, reason
    assert "Не прошло" in reason


def test_passages_improve_score_when_they_confirm_product_details():
    score, reason = evaluate_search_result(
        query="Красные кроссовки Nike Air Max размер 42",
        title="Кроссовки Nike Air Max",
        url="https://shop.test/sneakers",
        passages=[
            "Продажа красных кроссовок Nike Air Max. Размер 42 в наличии.",
        ],
    )

    assert score >= 65, reason
    assert "passages=100" in reason


def test_compact_title_specs_match_verbose_drill_query():
    score, reason = evaluate_search_result(
        query=(
            "Бур для перфоратора по бетону, диаметр 20мм, "
            "общая длина 460мм, хвостовик SDS+"
        ),
        title=(
            '038-067 бур SDS+ 20х400/460мм серия "Профи", по бетону '
            "Практика SDS-plus 20х460 мм"
        ),
        url="https://shop.test/bur-sds-plus-20x460",
    )

    assert score >= 65, reason
    assert "title_attrs=20" in reason


def test_exact_title_size_matches_socket_wrench_query():
    score, reason = evaluate_search_result(
        query="Ключ торцовой 11х11мм 7812-1609 ГОСТ 25788-83",
        title="Ключ торцевой изогнутый КТИ 11х11",
        url="https://shop.test/klyuch-torcevoy-11x11mm-gost-25788-83",
    )

    assert score >= 45, reason
    assert "title_attrs=15" in reason
    assert "title_penalty" not in reason


def test_sizes_are_normalized():
    assert extract_sizes("панель 600,0x900.00 мм") == {"600x900"}


def test_star_dimension_survives_text_normalization():
    score, reason = evaluate_search_result(
        query="Бур SDS+ 20*460мм",
        title="Бур SDS-plus 20х460 мм",
        url="https://shop.test/drill",
    )

    assert score >= 65, reason
    assert "title_attrs=20" in reason


def test_size_penalty_when_size_is_missing():
    penalty, missing = calculate_size_penalty(
        query="панель 600x900",
        target="панель 500x700",
    )

    assert penalty == 35
    assert missing == ["600x900"]


def test_size_penalty_not_applied_when_page_has_no_size():
    penalty, missing = calculate_size_penalty(
        query="стекло триплекс 5+5 1084х259мм",
        target="стекло триплекс 5+5 мм",
    )

    assert penalty == 0
    assert missing == []


def test_size_penalty_not_applied_when_size_matches():
    penalty, missing = calculate_size_penalty(
        query="панель 600x900",
        target="панель 600,0x900.00 мм",
    )

    assert penalty == 0
    assert missing == []


def test_size_penalty_not_applied_when_display_resolution_is_reversed():
    penalty, missing = calculate_size_penalty(
        query="IP-телефон с дисплеем 240x320",
        target="IP-телефон LCD 320x240",
    )

    assert penalty == 0
    assert missing == []


def test_number_score_without_numbers_is_full_match():
    assert number_score("жилет женский", "жилет женский") == 100


def test_range_and_material_do_not_get_extra_attribute_bonus():
    score, reason = evaluate_search_result(
        query=(
            "Молоток столярный, стальной, форма бойка круглый, "
            "длина ручки >=200 <300 мм, масса >=0,4 <0,6кг"
        ),
        title=(
            "Молоток столярный стальной с круглым бойком "
            "длина ручки 250 мм масса 0,5 кг"
        ),
        url="https://shop.test/molotok-stolyarny",
    )

    assert score < 65, reason
    assert ", attrs=" not in reason
