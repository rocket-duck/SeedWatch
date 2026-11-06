from pathlib import Path

import pytest

import app.anistar
from app.anistar import decode_page, is_anistar_url, parse_series_page, series_id_from_url

FIXTURES = Path(__file__).parent / "fixtures"


def torrent_by_id(page, torrent_id):
    return next(t for t in page["torrents"] if t["id"] == torrent_id)


@pytest.fixture(scope="module")
def jade():
    return parse_series_page(decode_page((FIXTURES / "anistar_jade.html").read_bytes()))


@pytest.fixture(scope="module")
def douluo():
    return parse_series_page(decode_page((FIXTURES / "anistar_douluo.html").read_bytes()))


@pytest.fixture(scope="module")
def demon():
    return parse_series_page(decode_page((FIXTURES / "anistar_demon.html").read_bytes()))


@pytest.fixture(scope="module")
def perfect():
    return parse_series_page(decode_page((FIXTURES / "anistar_perfect.html").read_bytes()))


def test_title_from_h1_jade(jade):
    assert jade["title"] == "Убийца богов / Нефритовая династия / Zhu Xian / Jade Dynasty"


def test_single_episode_with_comment(jade):
    torrent = torrent_by_id(jade, 31051)
    assert torrent["episodes"] == [14]
    assert torrent["comment"] == "Многоголосая озвучка"
    assert torrent["size"] == "835.81 Mb"


def test_single_episode_plain(jade):
    torrent = torrent_by_id(jade, 31048)
    assert torrent["episodes"] == [14]
    assert torrent["comment"] == ""


def test_pack_range(douluo):
    torrent = torrent_by_id(douluo, 15886)
    assert torrent["episodes"] == list(range(1, 68))


def test_pack_with_comment(jade):
    torrent = torrent_by_id(jade, 29876)
    assert torrent["episodes"] == [1, 2, 3]
    assert torrent["comment"] == "Многоголосая озвучка"


def test_decimal_episode_is_non_series(douluo):
    torrent = torrent_by_id(douluo, 32072)
    assert torrent["episodes"] == []


def test_specials_are_non_series(demon):
    torrent = torrent_by_id(demon, 45017)
    assert torrent["episodes"] == []


def test_seeds_and_date(jade):
    torrent = torrent_by_id(jade, 47069)
    assert torrent["seeds"] == 26
    assert torrent["date"] == "29-10-2026"


def test_perfect_page_parses(perfect):
    assert perfect["title"] == "Безупречный мир / Perfect World / Wanmei Shijie"
    assert len(perfect["torrents"]) == 287


def test_is_anistar_url():
    assert is_anistar_url("https://anistar.org/8883-x.html")
    assert is_anistar_url("https://v2.astar.bz/8883-x.html")
    assert is_anistar_url("https://www.anistar.org/8883-x.html")
    assert not is_anistar_url("https://rutracker.org/forum/viewtopic.php?t=1")
    assert not is_anistar_url("https://anistar.org.evil.ru/8883-x.html")


def test_is_anistar_url_uses_runtime_suffixes(monkeypatch):
    monkeypatch.setattr(app.anistar, "ANISTAR_HOST_SUFFIXES", ("example.com",))
    assert is_anistar_url("https://example.com/1-a.html")
    assert not is_anistar_url("https://anistar.org/8883-x.html")


def test_series_id_from_url():
    assert series_id_from_url("https://anistar.org/8883-x.html") == "8883"
    assert series_id_from_url("https://v2.astar.bz/8883-slug.html") == "8883"
    assert series_id_from_url("https://anistar.org/") is None
    assert series_id_from_url("https://anistar.org/news.html") is None
