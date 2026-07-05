import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Создаёт временную папку теста внутри проекта."""

    path = Path(".test_tmp") / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)

    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
