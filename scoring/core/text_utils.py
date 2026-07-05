import re


SPACES_RE = re.compile(r"\s+")


def clean_text(value: str) -> str:
    return SPACES_RE.sub(" ", value).strip()
