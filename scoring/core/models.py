from dataclasses import dataclass
from typing import TypeAlias


RawSearchItemValue: TypeAlias = str | list[str]
RawSearchItem: TypeAlias = dict[str, RawSearchItemValue]


@dataclass(frozen=True, slots=True)
class SearchItem:
    url: str
    title: str = ""
    passages: tuple[str, ...] = ()
    offers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankedLink:
    url: str
    score: int
    title: str
    reason: str
    source_pos: int
