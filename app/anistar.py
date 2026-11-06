"""Парсинг HTML-страниц сериалов с anistar.org (windows-1251) и выбор торрентов."""

import html as html_module
import re
from urllib.parse import urlparse

ANISTAR_HOST_SUFFIXES: tuple[str, ...] = ("anistar.org", "astar.bz")

_SIGNATURE_RE = re.compile(
    r"^сери[ия]\s+(\d+)(?![\d.])\s*(?:[-–—]\s*(\d+))?\s*(.*?)\s*\(([^)]+)\)\s*$",
    re.IGNORECASE,
)
_TORRENT_BLOCK_RE = re.compile(r'id="torrent_(\d+)_info"')
_LABEL_RE = re.compile(r'<div class="info_d1">(.*?)</div>', re.DOTALL)
_SEEDS_RE = re.compile(r'class="li_distribute">\s*(\d+)')
_DATE_RE = re.compile(r"<b>Дата:\s*</b>\s*([\d-]+)")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SERIES_ID_RE = re.compile(r"/(\d+)(?:-[^/]*)?\.html")
_MAX_PACK_SPAN = 500


def is_anistar_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in ANISTAR_HOST_SUFFIXES
    )


def series_id_from_url(url: str) -> str | None:
    match = _SERIES_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def decode_page(content: bytes) -> str:
    return content.decode("cp1251", errors="replace")


def parse_series_page(html: str) -> dict:
    return {
        "title": _parse_title(html),
        "torrents": [_parse_torrent(tid, block) for tid, block in _torrent_blocks(html)],
    }


def _torrent_blocks(html: str) -> list[tuple[int, str]]:
    parts = _TORRENT_BLOCK_RE.split(html)
    # split: [before, id1, block1, id2, block2, ...]
    return [
        (int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)
    ]


def _parse_torrent(torrent_id: int, block: str) -> dict:
    label_match = _LABEL_RE.search(block)
    label = _clean_text(label_match.group(1)) if label_match else ""
    episodes, comment, size = _parse_label(label)
    seeds_match = _SEEDS_RE.search(block)
    date_match = _DATE_RE.search(block)
    return {
        "id": torrent_id,
        "episodes": episodes,
        "comment": comment,
        "label": label,
        "size": size,
        "seeds": int(seeds_match.group(1)) if seeds_match else None,
        "date": date_match.group(1) if date_match else None,
    }


def _parse_title(html: str) -> str:
    match = _H1_RE.search(html)
    if match:
        return _clean_text(match.group(1))
    match = _TITLE_RE.search(html)
    if match:
        title = _clean_text(match.group(1))
        return re.split(r"\s+смотреть онлайн", title, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return ""


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(_TAG_RE.sub("", text))).strip()


def _parse_label(label: str) -> tuple[list[int], str, str | None]:
    match = _SIGNATURE_RE.match(label)
    if not match:
        return [], "", None
    start, end, comment, size = match.groups()
    first = int(start)
    if end is None:
        return [first], comment.strip(), size.strip()
    last = int(end)
    if last >= first and last - first <= _MAX_PACK_SPAN:
        return list(range(first, last + 1)), comment.strip(), size.strip()
    return [], comment.strip(), size.strip()


def voice_tier(comment: str) -> int:
    lowered = comment.lower()
    if "многоголос" in lowered:
        return 3
    if "&" in comment:
        return 2
    if "одноголос" in lowered:
        return 1
    return 0


def select_torrents(
    torrents: list[dict],
    known: list[dict],
    catch_up: bool,
    downloaded: set[int] | None,
) -> dict:
    known_by_id = {record["id"]: record for record in known}
    downloaded = downloaded or set()

    candidates = []
    for torrent in torrents:
        if not torrent["episodes"]:
            continue  # не-серийный контент не качаем, только фиксируем в processed
        record = known_by_id.get(torrent["id"])
        if catch_up or record is None or not record.get("processed"):
            candidates.append(torrent)

    needs: set[int] = set()
    for torrent in candidates:
        needs.update(torrent["episodes"])
    needs -= downloaded

    ordered = sorted(
        candidates,
        key=lambda t: (
            -voice_tier(t["comment"]),
            len(t["episodes"]) > 1,  # одиночные раньше паков
            -(t["seeds"] or 0),
            -_date_sort_value(t["date"]),
        ),
    )

    covered: set[int] = set()
    selected = []
    grabbed_ids: set[int] = set()
    for torrent in ordered:
        fresh = [ep for ep in torrent["episodes"] if ep in needs and ep not in covered]
        if not fresh:
            continue
        selected.append({"torrent": torrent, "episodes": fresh})
        grabbed_ids.add(torrent["id"])
        covered.update(fresh)

    processed = [
        {
            "id": torrent["id"],
            "episodes": torrent["episodes"],
            "comment": torrent["comment"],
            "grabbed": torrent["id"] in grabbed_ids,
        }
        for torrent in torrents
    ]
    return {
        "selected": selected,
        "processed": processed,
        "new_seen": sum(1 for torrent in torrents if torrent["id"] not in known_by_id),
    }


def _date_sort_value(date: str | None) -> int:
    if not date:
        return 0
    try:
        day, month, year = (int(part) for part in date.split("-"))
    except ValueError:
        return 0
    return year * 10000 + month * 100 + day
