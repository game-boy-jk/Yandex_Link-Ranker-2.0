import main as app
from search.yandex_search import YandexSearchConfigError


def test_main_shows_friendly_message_when_yandex_config_is_missing(
    monkeypatch,
    capsys,
):
    def fake_get_yandex_config():
        raise YandexSearchConfigError("missing config")

    def fail_find_product_links(query):
        raise AssertionError("search should not run without Yandex config")

    monkeypatch.setattr(app, "get_yandex_config", fake_get_yandex_config)
    monkeypatch.setattr(app, "find_product_links", fail_find_product_links)

    exit_code = app.main(["насос"])

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "API_KEY и FOLDER_ID ушли за кофе." in captured.err
    assert "Верните их в .env, и я снова начну считать." in captured.err
    assert "YANDEX_API_KEY и YANDEX_FOLDER_ID" in captured.err


def test_main_passages_prints_raw_yandex_items(monkeypatch, capsys):
    def fake_get_yandex_config():
        return "test-key", "test-folder"

    def fake_search_yandex(query, *, limit):
        assert query == "красные кроссовки"
        assert limit == app.RAW_RESULTS_LIMIT
        return [
            {
                "url": "https://example.test/sneakers-red",
                "title": "Купить красные кроссовки",
                "passages": [
                    "Продажа красных кроссовок Nike.",
                    "Модель Air Max доступна в размерах 40-45.",
                ],
            }
        ]

    def fail_find_product_links(query):
        raise AssertionError("ranker should not run in passages mode")

    monkeypatch.setattr(app, "get_yandex_config", fake_get_yandex_config)
    monkeypatch.setattr(app, "search_yandex", fake_search_yandex)
    monkeypatch.setattr(app, "find_product_links", fail_find_product_links)

    exit_code = app.main(["--passages", "красные", "кроссовки"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "1. https://example.test/sneakers-red" in captured.out
    assert "title: Купить красные кроссовки" in captured.out
    assert "passage: Продажа красных кроссовок Nike." in captured.out
    assert "passage: Модель Air Max доступна в размерах 40-45." in captured.out


def test_main_batch_mode_writes_result_file(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "queries.csv"
    output_dir = tmp_path / "results"
    input_path.write_text("query\nЛопата\nКраска\n", encoding="utf-8")

    def fake_get_yandex_config():
        return "test-key", "test-folder"

    def fake_find_product_links(query):
        return [f"https://example.test/{query}"]

    monkeypatch.setattr(app, "get_yandex_config", fake_get_yandex_config)
    monkeypatch.setattr(app, "find_product_links_strict", fake_find_product_links)

    exit_code = app.main(
        [
            "--file",
            str(input_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    output_path = output_dir / captured.out.strip().split("\\")[-1]

    assert exit_code == 0
    assert output_path.exists()
    assert "Лопата" in output_path.read_text(encoding="utf-8-sig")
    assert "https://example.test/Краска" in output_path.read_text(encoding="utf-8-sig")
