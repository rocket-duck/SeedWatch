import re
from urllib.parse import parse_qs, urlparse


EPISODE_PATTERNS = (
    re.compile(r"\bS\d{1,2}E(\d{1,4})(?:\s*[-–—]\s*(?:S\d{1,2}E|E)?(\d{1,4}))?", re.IGNORECASE),
    re.compile(r"\bE(?:p(?:isode)?)?\.?\s*(\d{1,4})(?:\s*[-–—]\s*(?:E(?:p(?:isode)?)?\.?\s*)?(\d{1,4}))?", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*(?=сери[ия])", re.IGNORECASE),
    re.compile(r"(?<![\d-])(\d{1,4})\s*сери[ия]", re.IGNORECASE),
    re.compile(r"\[(\d{1,4})\s*[-–—]\s*(\d{1,4})\]"),
)


def extract_topic_id(url: str) -> str | None:
    try:
        topic_ids = parse_qs(urlparse(url).query).get("t", [])
    except ValueError:
        return None
    return topic_ids[0] if topic_ids and topic_ids[0].isdigit() else None


def find_topic_release(results: list[dict], topic_id: str) -> dict | None:
    for release in results:
        links = (
            release.get("infoUrl"),
            release.get("guid"),
            release.get("downloadUrl"),
            release.get("comment"),
        )
        if any(link and extract_topic_id(str(link)) == topic_id for link in links):
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


def episode_range(episodes: list[int]) -> str:
    if not episodes:
        return "Серии не распознаны"
    if len(episodes) == 1:
        return f"Серия {episodes[0]}"
    return f"Серии {episodes[0]}–{episodes[-1]} ({len(episodes)})"
