import argparse
import sys
from pathlib import Path

from batch.search_runner import process_queries, result_to_csv_row
from batch.table_files import BatchFileError, DEFAULT_RESULTS_DIR
from batch.table_files import load_queries_from_file, write_results_csv
from core.quotas import DEFERRED_REQUEST_SECOND_QUOTA
from ranking.link_ranker import find_product_links, find_product_links_strict
from search.yandex_search import (
    YANDEX_API_KEY_ENV,
    YANDEX_FOLDER_ID_ENV,
    YandexSearchConfigError,
    get_yandex_config,
    search_yandex,
)


CONFIG_MISSING_MESSAGE = (
    "API_KEY и FOLDER_ID ушли за кофе.\nВерните их в .env, и я снова начну считать."
)
PASSAGES_FLAG = "--passages"
RAW_RESULTS_LIMIT = 10
BATCH_DEFAULT_WORKERS = 1
PROGRESS_BAR_WIDTH = 30


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args is None:
        return 2

    query = " ".join(args.query).strip()
    if not query and not args.input_file:
        build_parser().print_usage(sys.stderr)
        return 2

    if query and args.input_file:
        print("Передайте или один запрос, или --file, но не оба сразу", file=sys.stderr)
        return 2

    if args.passages and args.input_file:
        print(f"{PASSAGES_FLAG} работает только для одного запроса", file=sys.stderr)
        return 2

    try:
        get_yandex_config()
    except YandexSearchConfigError:
        print(CONFIG_MISSING_MESSAGE, file=sys.stderr)
        print(
            f"Нужные переменные: {YANDEX_API_KEY_ENV} и {YANDEX_FOLDER_ID_ENV}",
            file=sys.stderr,
        )
        return 2

    if args.input_file:
        return process_file(
            input_path=args.input_file,
            column_name=args.column,
            output_dir=args.output_dir,
            workers=args.workers,
        )

    if args.passages:
        return print_yandex_passages(query)

    urls = find_product_links(query)

    if not urls:
        print("Ничего не нашлось", file=sys.stderr)
        return 1

    for url in urls:
        print(url)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ищет релевантные товарные ссылки через Yandex Search API.",
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Название товара для одиночного поиска.",
    )
    parser.add_argument(
        "--file",
        "-f",
        dest="input_file",
        type=Path,
        help="CSV или XLSX с колонкой запросов.",
    )
    parser.add_argument(
        "--column",
        "-c",
        help="Название колонки с запросами. Если не указано, колонка ищется сама.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help=f"Папка для результатов. По умолчанию: {DEFAULT_RESULTS_DIR}.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=BATCH_DEFAULT_WORKERS,
        help=(
            "Количество параллельных запросов для batch-режима. "
            f"Сверху ограничивается лимитом API: {DEFERRED_REQUEST_SECOND_QUOTA}."
        ),
    )
    parser.add_argument(
        PASSAGES_FLAG,
        action="store_true",
        help="Показать сырые passages Яндекса для одного запроса.",
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace | None:
    try:
        return build_parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code:
            return None
        raise


def process_file(
    *,
    input_path: Path,
    column_name: str | None,
    output_dir: Path,
    workers: int,
) -> int:
    try:
        queries = load_queries_from_file(input_path, column_name=column_name)
        progress_bar = ProgressBar()
        results = process_queries(
            queries,
            workers=workers,
            search=find_product_links_strict,
            progress=progress_bar.update,
        )
        output_path = write_results_csv(
            (result_to_csv_row(result) for result in results),
            input_path=input_path,
            output_dir=output_dir,
        )
    except (BatchFileError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(output_path)
    return 0


class ProgressBar:
    """Рисует прогресс пакетной обработки в консоли."""

    def __init__(self, width: int = PROGRESS_BAR_WIDTH) -> None:
        self.width = width

    def update(self, done: int, total: int) -> None:
        """Обновляет строку прогресса."""

        safe_total = max(total, 1)
        percent = done / safe_total
        filled = round(self.width * percent)
        bar = "#" * filled + "-" * (self.width - filled)

        print(
            f"\rОбработано: [{bar}] {done}/{total} "
            f"({percent:.0%}), осталось: {total - done}",
            end="",
            file=sys.stderr,
            flush=True,
        )

        if done >= total:
            print(file=sys.stderr)


def print_yandex_passages(query: str) -> int:
    items = search_yandex(query, limit=RAW_RESULTS_LIMIT)
    if not items:
        print("Ничего не нашлось", file=sys.stderr)
        return 1

    for index, item in enumerate(items, start=1):
        print(f"{index}. {item.get('url', '')}")

        title = item.get("title")
        if title:
            print(f"   title: {title}")

        passages = item.get("passages") or []
        if not passages:
            print("   passages: -")
            continue

        for passage in passages:
            print(f"   passage: {passage}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
