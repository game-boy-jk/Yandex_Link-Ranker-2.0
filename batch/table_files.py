import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


DEFAULT_RESULTS_DIR = Path("results")
DEFAULT_QUERY_COLUMNS = (
    "query",
    "запрос",
    "наименование",
    "название",
    "товар",
    "name",
    "product",
)
RESULT_HEADERS = ("row", "status", "query", "url_1", "url_2", "url_3", "error")
SUPPORTED_INPUT_SUFFIXES = {".csv", ".xlsx"}
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")

XML_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass(frozen=True, slots=True)
class BatchQuery:
    """Один товарный запрос, загруженный из табличного файла."""

    row_number: int
    query: str


@dataclass(frozen=True, slots=True)
class TableData:
    """Нормализованные строки таблицы из CSV или XLSX."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


class BatchFileError(ValueError):
    """Ошибка чтения или проверки входной таблицы."""


def load_queries_from_file(
    path: Path,
    *,
    column_name: str | None = None,
) -> list[BatchQuery]:
    """Загружает товарные запросы из CSV или XLSX файла."""

    table = load_table(path)
    column_index, data_rows, first_row_number = prepare_query_rows(
        table,
        column_name=column_name,
    )
    queries: list[BatchQuery] = []

    for offset, row in enumerate(data_rows, start=first_row_number):
        query = get_cell(row, column_index).strip()
        if query:
            queries.append(BatchQuery(row_number=offset, query=query))

    if not queries:
        raise BatchFileError("В выбранной колонке нет запросов для обработки")

    return queries


def prepare_query_rows(
    table: TableData,
    *,
    column_name: str | None,
) -> tuple[int, tuple[tuple[str, ...], ...], int]:
    """Находит колонку запросов и возвращает строки для обработки."""

    try:
        column_index = resolve_query_column(table.headers, column_name)
    except BatchFileError:
        if column_name or not is_headerless_single_column_table(table):
            raise

        return 0, (table.headers, *table.rows), 1

    return column_index, table.rows, 2


def load_table(path: Path) -> TableData:
    """Читает поддерживаемый табличный файл в заголовки и строки."""

    if not path.exists():
        raise BatchFileError(f"Файл не найден: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv_table(path)
    if suffix == ".xlsx":
        return load_xlsx_table(path)

    allowed = ", ".join(sorted(SUPPORTED_INPUT_SUFFIXES))
    raise BatchFileError(f"Неподдерживаемый формат файла. Нужен: {allowed}")


def load_csv_table(path: Path) -> TableData:
    """Читает CSV с определением разделителя и кодировки."""

    text = read_text_with_fallback(path)
    sample = text[:4096]

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows = [
        tuple(clean_cell(cell) for cell in row)
        for row in csv.reader(text.splitlines(), dialect)
        if any(cell.strip() for cell in row)
    ]
    return normalize_table_rows(rows)


def load_xlsx_table(path: Path) -> TableData:
    """Читает первый лист XLSX файла."""

    try:
        with ZipFile(path) as archive:
            sheet_path = find_first_sheet_path(archive)
            shared_strings = read_shared_strings(archive)
            rows = read_sheet_rows(archive, sheet_path, shared_strings)
    except (BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise BatchFileError(f"Не удалось прочитать XLSX: {path}") from exc

    return normalize_table_rows(rows)


def normalize_table_rows(rows: Iterable[tuple[str, ...]]) -> TableData:
    """Разделяет таблицу на заголовки и данные, убирая пустые хвосты строк."""

    normalized = [trim_trailing_empty(row) for row in rows]
    normalized = [row for row in normalized if any(row)]

    if not normalized:
        raise BatchFileError("Файл пустой")

    headers = normalized[0]
    if not any(headers):
        raise BatchFileError("В первой строке не найдены заголовки")

    return TableData(headers=headers, rows=tuple(normalized[1:]))


def resolve_query_column(
    headers: tuple[str, ...],
    column_name: str | None,
) -> int:
    """Находит колонку запроса по имени пользователя или типовым заголовкам."""

    normalized_headers = [normalize_header(header) for header in headers]

    if column_name:
        normalized_name = normalize_header(column_name)
        for index, header in enumerate(normalized_headers):
            if header == normalized_name:
                return index
        raise BatchFileError(f"Колонка не найдена: {column_name}")

    for candidate in DEFAULT_QUERY_COLUMNS:
        normalized_candidate = normalize_header(candidate)
        for index, header in enumerate(normalized_headers):
            if header == normalized_candidate:
                return index

    available = ", ".join(header for header in headers if header)
    raise BatchFileError(
        "Не нашёл колонку с запросом. Укажите её через --column. "
        f"Доступные колонки: {available}"
    )


def is_headerless_single_column_table(table: TableData) -> bool:
    """Проверяет, похожа ли таблица на одноколоночный список без заголовка."""

    rows = (table.headers, *table.rows)
    return all(len(row) <= 1 for row in rows)


def write_results_csv(
    rows: Iterable[dict[str, str]],
    *,
    input_path: Path,
    output_dir: Path = DEFAULT_RESULTS_DIR,
    result_stem: str | None = None,
) -> Path:
    """Записывает результаты пакетной обработки в CSV с датой в имени."""

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = build_result_path(
        input_path=input_path,
        output_dir=output_dir,
        result_stem=result_stem,
    )

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=RESULT_HEADERS,
            delimiter=";",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def build_result_path(
    *,
    input_path: Path,
    output_dir: Path,
    result_stem: str | None = None,
) -> Path:
    """Строит свободный путь для файла результата."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    source_stem = clean_result_stem(result_stem or input_path.stem)
    file_stem = f"{source_stem}_result_{timestamp}"
    output_path = output_dir / f"{file_stem}.csv"

    if not output_path.exists():
        return output_path

    for number in range(2, 10_000):
        numbered_path = output_dir / f"{file_stem}_{number}.csv"
        if not numbered_path.exists():
            return numbered_path

    raise BatchFileError("Не удалось подобрать свободное имя файла результата")


def clean_result_stem(value: str) -> str:
    """Возвращает безопасную основу имени файла результата."""

    safe_value = Path(value).stem.strip()
    return safe_value or "result"


def read_text_with_fallback(path: Path) -> str:
    """Читает текст в распространённых CSV-кодировках."""

    last_error: UnicodeDecodeError | None = None
    for encoding in CSV_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc

    raise BatchFileError(f"Не удалось определить кодировку CSV: {path}") from last_error


def find_first_sheet_path(archive: ZipFile) -> str:
    """Возвращает путь к первому листу через связи XLSX-книги."""

    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

    first_sheet = workbook.find(f".//{{{XML_MAIN_NS}}}sheet")
    if first_sheet is None:
        raise BatchFileError("В XLSX нет листов")

    relationship_id = first_sheet.attrib.get(f"{{{XML_REL_NS}}}id")
    if not relationship_id:
        raise BatchFileError("В XLSX нет ссылки на первый лист")

    for relation in rels:
        if relation.attrib.get("Id") != relationship_id:
            continue

        target = relation.attrib.get("Target", "")
        if not target:
            break

        return normalize_xlsx_path(target)

    raise BatchFileError("Не удалось найти первый лист XLSX")


def normalize_xlsx_path(target: str) -> str:
    """Приводит путь из связей XLSX-книги к пути внутри ZIP-архива."""

    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def read_shared_strings(archive: ZipFile) -> list[str]:
    """Читает общую таблицу строк XLSX."""

    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []

    for item in root.findall(f"{{{XML_MAIN_NS}}}si"):
        values.append("".join(item.itertext()))

    return values


def read_sheet_rows(
    archive: ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[tuple[str, ...]]:
    """Читает строки листа из XML внутри XLSX."""

    root = ElementTree.fromstring(archive.read(sheet_path))
    result: list[tuple[str, ...]] = []

    for row in root.findall(f".//{{{XML_MAIN_NS}}}row"):
        values: list[str] = []

        for cell in row.findall(f"{{{XML_MAIN_NS}}}c"):
            column_index = column_index_from_ref(cell.attrib.get("r", ""))
            while len(values) < column_index:
                values.append("")
            values.append(read_xlsx_cell(cell, shared_strings))

        result.append(trim_trailing_empty(tuple(values)))

    return result


def read_xlsx_cell(
    cell: ElementTree.Element,
    shared_strings: list[str],
) -> str:
    """Извлекает значение одной XLSX-ячейки как текст."""

    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        inline_string = cell.find(f"{{{XML_MAIN_NS}}}is")
        if inline_string is None:
            return ""
        return clean_cell("".join(inline_string.itertext()))

    value = cell.find(f"{{{XML_MAIN_NS}}}v")
    if value is None or value.text is None:
        return ""

    raw_value = value.text
    if cell_type == "s":
        try:
            return clean_cell(shared_strings[int(raw_value)])
        except (IndexError, ValueError) as exc:
            raise BatchFileError("В XLSX повреждена таблица строк") from exc

    return clean_cell(raw_value)


def column_index_from_ref(cell_ref: str) -> int:
    """Преобразует ссылку XLSX-ячейки в индекс колонки с нуля."""

    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        return 0

    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - ord("A") + 1

    return index - 1


def get_cell(row: tuple[str, ...], index: int) -> str:
    """Возвращает значение ячейки из строки, которая может быть короче."""

    if index >= len(row):
        return ""
    return row[index]


def normalize_header(value: str) -> str:
    """Нормализует заголовок для сравнения."""

    return clean_cell(value).lower().replace("ё", "е")


def clean_cell(value: str) -> str:
    """Очищает значение ячейки и схлопывает пробельные символы."""

    return re.sub(r"\s+", " ", value).strip()


def trim_trailing_empty(row: tuple[str, ...]) -> tuple[str, ...]:
    """Удаляет пустые ячейки в конце строки."""

    values = list(row)
    while values and not values[-1]:
        values.pop()
    return tuple(values)
