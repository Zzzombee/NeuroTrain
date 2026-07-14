from __future__ import annotations

import re
from pathlib import Path


SORTED_PL2_RE = re.compile(r"^sorted_(?P<file_index>\d+)_", flags=re.IGNORECASE)
TEST_FILE_ID_RE = re.compile(r"^test(?P<file_index>\d+)$", flags=re.IGNORECASE)
SORTED_STEM_RE = re.compile(r"^sorted_(?P<file_index>\d+)_", flags=re.IGNORECASE)


def format_file_index(file_index: str | int, config: dict | None = None) -> str:
    zero_pad = int((config or {}).get("stim_schedule", {}).get("file_id", {}).get("zero_pad", 2))
    return str(file_index).zfill(zero_pad)


def file_index_from_pl2_file(pl2_file: str | Path | None) -> str | None:
    if pl2_file is None:
        return None
    name = Path(str(pl2_file)).name
    match = SORTED_PL2_RE.match(name)
    if not match:
        return None
    return match.group("file_index")


def file_index_from_file_id(file_id: str | None) -> str | None:
    if file_id is None:
        return None
    text = str(file_id).strip()
    if not text:
        return None
    test_match = TEST_FILE_ID_RE.match(text)
    if test_match:
        return test_match.group("file_index")
    sorted_match = SORTED_STEM_RE.match(text)
    if sorted_match:
        return sorted_match.group("file_index")
    if text.isdigit():
        return text
    return None


def canonicalize_file_id_from_pl2_file(pl2_file: str | Path | None, config: dict | None = None) -> str | None:
    file_index = file_index_from_pl2_file(pl2_file)
    if file_index is None:
        return None
    return format_file_index(file_index, config)


def canonicalize_file_id(file_id: str | None, pl2_file: str | Path | None = None, config: dict | None = None) -> str:
    file_index = file_index_from_pl2_file(pl2_file) or file_index_from_file_id(file_id)
    if file_index is None:
        return "" if file_id is None else str(file_id)
    return format_file_index(file_index, config)


def legacy_file_id_candidates(file_id: str, pl2_file: str | Path | None = None, config: dict | None = None) -> list[str]:
    canonical = canonicalize_file_id(file_id, pl2_file, config)
    candidates = [canonical]
    file_index = file_index_from_pl2_file(pl2_file) or file_index_from_file_id(file_id)
    if file_index is not None:
        padded = format_file_index(file_index, config)
        candidates.append(f"test{padded}")
        if pl2_file:
            candidates.append(Path(str(pl2_file)).stem)
    if file_id:
        candidates.append(str(file_id))
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def infer_has_light_from_identifiers(file_id: str | None = None, pl2_file: str | None = None) -> bool | None:
    haystack = " ".join(str(value).lower() for value in [file_id, pl2_file] if value)
    if "nolight" in haystack or "no_light" in haystack:
        return False
    return None
