from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import batch.table_files as table_files
from batch.table_files import BatchFileError, BatchQuery, build_result_path
from batch.table_files import load_queries_from_file
from batch.table_files import write_results_csv


def test_load_queries_from_semicolon_csv(tmp_path: Path):
    path = tmp_path / "queries.csv"
    path.write_text(
        "id;Наименование;qty\n1;Лопата снеговая;2\n2;Краска фасадная белая;5\n",
        encoding="utf-8",
    )

    assert load_queries_from_file(path) == [
        BatchQuery(row_number=2, query="Лопата снеговая"),
        BatchQuery(row_number=3, query="Краска фасадная белая"),
    ]


def test_load_queries_from_xlsx_with_column_name(tmp_path: Path):
    path = tmp_path / "queries.xlsx"
    write_minimal_xlsx(
        path,
        [
            ["id", "product_name"],
            ["1", "Метла уличная"],
            ["2", "Эмаль зеленая"],
        ],
    )

    assert load_queries_from_file(path, column_name="product_name") == [
        BatchQuery(row_number=2, query="Метла уличная"),
        BatchQuery(row_number=3, query="Эмаль зеленая"),
    ]


def test_load_queries_from_headerless_single_column_xlsx(tmp_path: Path):
    path = tmp_path / "queries.xlsx"
    write_minimal_xlsx(
        path,
        [
            ["Метла уличная"],
            ["Эмаль зеленая"],
        ],
    )

    assert load_queries_from_file(path) == [
        BatchQuery(row_number=1, query="Метла уличная"),
        BatchQuery(row_number=2, query="Эмаль зеленая"),
    ]


def test_load_queries_requires_existing_column(tmp_path: Path):
    path = tmp_path / "queries.csv"
    path.write_text("id;name\n1;Лопата\n", encoding="utf-8")

    with pytest.raises(BatchFileError, match="Колонка не найдена"):
        load_queries_from_file(path, column_name="query")


def test_load_queries_reports_broken_xlsx(tmp_path: Path):
    path = tmp_path / "broken.xlsx"
    path.write_text("это не xlsx", encoding="utf-8")

    with pytest.raises(BatchFileError, match="Не удалось прочитать XLSX"):
        load_queries_from_file(path)


def test_write_results_csv_creates_results_file(tmp_path: Path):
    input_path = tmp_path / "queries.csv"
    output_dir = tmp_path / "results"

    output_path = write_results_csv(
        [
            {
                "row": "2",
                "status": "found",
                "query": "Лопата",
                "url_1": "https://example.test",
                "url_2": "",
                "url_3": "",
                "error": "",
            }
        ],
        input_path=input_path,
        output_dir=output_dir,
    )

    assert output_path.parent == output_dir
    assert output_path.name.startswith("queries_result_")
    assert "Лопата" in output_path.read_text(encoding="utf-8-sig")


def test_write_results_csv_can_use_original_upload_name(tmp_path: Path):
    input_path = tmp_path / "abc123_queries.csv"
    output_dir = tmp_path / "results"

    output_path = write_results_csv(
        [],
        input_path=input_path,
        output_dir=output_dir,
        result_stem="список запросов 1",
    )

    assert output_path.name.startswith("список запросов 1_result_")
    assert "abc123" not in output_path.name


def test_build_result_path_avoids_existing_file(monkeypatch, tmp_path: Path):
    real_datetime = table_files.datetime

    class FixedDateTime:
        @staticmethod
        def now():
            return real_datetime(2026, 6, 19, 12, 2, 6)

    input_path = tmp_path / "queries.csv"
    existing_path = tmp_path / "queries_result_20260619_120206.csv"
    existing_path.touch()

    monkeypatch.setattr(table_files, "datetime", FixedDateTime)

    assert build_result_path(input_path=input_path, output_dir=tmp_path) == (
        tmp_path / "queries_result_20260619_120206_2.csv"
    )


def write_minimal_xlsx(path: Path, rows: list[list[str]]) -> None:
    """Создаёт маленький XLSX с inline-строками для тестов парсера."""

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            column = column_name(column_index)
            cells.append(
                f'<c r="{column}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            (
                '<workbook xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships">'
                '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
                "</sheets></workbook>"
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main">'
                f"<sheetData>{''.join(sheet_rows)}</sheetData>"
                "</worksheet>"
            ),
        )


def column_name(index: int) -> str:
    """Преобразует номер колонки с единицы в название Excel-колонки."""

    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = f"{chr(65 + remainder)}{result}"
    return result
