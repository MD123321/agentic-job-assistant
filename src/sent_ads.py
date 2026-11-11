"""Persist successfully emailed ads in the shared TOML job log."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Iterable, Protocol


class SentJobLike(Protocol):
    """Fields stored for each emailed job ad."""

    title: str
    url: str
    source: str


def load_sent_ads(path: Path) -> list[dict]:
    """Load sent ads from the shared log, treating a missing file as empty."""
    if not path.exists():
        return []
    try:
        with path.open("rb") as log_file:
            data = tomllib.load(log_file)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(f"The job log is not valid TOML: {path}") from error
    entries = data.get("sent_ads", [])
    if not isinstance(entries, list):
        raise RuntimeError(f"The job log must contain a sent_ads list: {path}")
    return entries


def record_sent_ads(
    path: Path,
    matches: Iterable[SentJobLike],
    sent_at: str,
) -> int:
    """Append each URL once after a successful email and return the count."""
    entries = load_sent_ads(path)
    known_urls = {
        entry.get("url")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("url"), str)
    }
    added = 0
    for match in matches:
        if match.url in known_urls:
            continue
        entries.append(
            {
                "title": match.title,
                "url": match.url,
                "source": match.source,
                "sent_at": sent_at,
            }
        )
        known_urls.add(match.url)
        added += 1

    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        existing_text = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
        new_entries = entries[-added:]
        blocks = []
        for entry in new_entries:
            blocks.append(
                "\n".join(
                    [
                        "[[sent_ads]]",
                        f'title = {json.dumps(entry["title"], ensure_ascii=False)}',
                        f'url = {json.dumps(entry["url"], ensure_ascii=False)}',
                        f'source = {json.dumps(entry["source"], ensure_ascii=False)}',
                        f'sent_at = {json.dumps(entry["sent_at"], ensure_ascii=False)}',
                    ]
                )
            )
        prefix = existing_text or "# Felles logg for jobbsøknader og utsendte jobbannonser."
        temporary_path.write_text(prefix + "\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")
        temporary_path.replace(path)
    return added
