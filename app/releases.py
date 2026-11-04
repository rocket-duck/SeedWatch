import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse


EPISODE_PATTERNS = (
    re.compile(r"\bS\d{1,2}E(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", re.IGNORECASE),
    re.compile(r"\bE(?:p(?:isode)?)?\.?\s*(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*(?=сери[ия])", re.IGNORECASE),
    re.compile(r"(?<![\d-])(\d{1,4})\s*сери[ия]", re.IGNORECASE),
    re.compile(r"\[(\d{1,4})\s*[-–—]\s*(\d{1,4})\]"),
)


SEASON_PATTERNS = (
    re.compile(r"\bS(\d{1,2})\s*E\d", re.IGNORECASE),
    re.compile(r"\bSeason\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,2})\s*сезон", re.IGNORECASE),
)


def extract_topic_id(url: str) -> str | None:
    try:
        topic_ids = parse_qs(urlparse(url).query).get("t", [])
    except ValueError:
        return None
    return topic_ids[0] if topic_ids and topic_ids[0].isdigit() else None


def release_identity(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        topic_id = extract_topic_id(url)
        if topic_id:
            return topic_id
        host = parsed.netloc.lower().removeprefix("www.")
        if parsed.scheme == "http" and host.endswith(":80"):
            host = host[:-3]
        if parsed.scheme == "https" and host.endswith(":443"):
            host = host[:-4]
        path = parsed.path.rstrip("/") or "/"
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        return f"{host}{path}{f'?{query}' if query else ''}"
    except ValueError:
        return None


def find_matching_release(results: list[dict], identity: str) -> dict | None:
    for release in results:
        links = (
            release.get("infoUrl"),
            release.get("guid"),
            release.get("downloadUrl"),
            release.get("comment"),
        )
        if any(link and release_identity(str(link)) == identity for link in links):
            return release
    return None


def parse_episodes(title: str) -> list[int]:
    episodes: set[int] = set()
    for pattern in EPISODE_PATTERNS:
        for match in pattern.finditer(title):
            start = int(match.group(1))
            end = int(match.group(2)) if match.lastindex and match.lastindex >= 2 and match.group(2) else start
            if end < start or end - start > 2000:
                continue
            episodes.update(range(start, end + 1))
    return sorted(episodes)


def parse_season(title: str) -> int | None:
    seasons = set()
    for pattern in SEASON_PATTERNS:
        for match in pattern.finditer(title):
            seasons.add(int(match.group(1)))
    return seasons.pop() if len(seasons) == 1 else None


def episode_range(episodes: list[int]) -> str:
    if not episodes:
        return "Серии не распознаны"
    if len(episodes) == 1:
        return f"Серия {episodes[0]}"
    return f"Серии {episodes[0]}–{episodes[-1]} ({len(episodes)})"
