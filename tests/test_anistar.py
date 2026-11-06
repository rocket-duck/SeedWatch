from pathlib import Path

import pytest

import app.anistar
from app.anistar import (
    decode_page,
    is_anistar_url,
    parse_series_page,
    select_torrents,
    series_id_from_url,
    voice_tier,
)
from app.store import Store

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


def make_torrent(tid, episodes, comment="", seeds=None, date=None):
    return {
        "id": tid,
        "episodes": episodes,
        "comment": comment,
        "label": "",
        "size": None,
        "seeds": seeds,
        "date": date,
    }


def known_record(torrent, processed=False, grabbed=False):
    return {
        "id": torrent["id"],
        "episodes": torrent["episodes"],
        "comment": torrent["comment"],
        "processed": processed,
        "grabbed": grabbed,
    }


def test_voice_tiers():
    assert voice_tier("Многоголосая озвучка") == 3
    assert voice_tier("[Xelenum & Sati Akura]") == 2
    assert voice_tier("Одноголосая озвучка") == 1
    assert voice_tier("[Xelenum]") == 0
    assert voice_tier("") == 0


def test_same_episode_prefers_multi_voice(jade):
    known = [known_record(t) for t in jade["torrents"]]
    result = select_torrents(jade["torrents"], known, catch_up=False, downloaded=None)
    ep14 = [s for s in result["selected"] if 14 in s["episodes"]]
    assert len(ep14) == 1
    assert ep14[0]["torrent"]["id"] == 31051


def test_tie_break_by_seeds():
    torrents = [
        make_torrent(1, [5], seeds=5, date="01-01-2026"),
        make_torrent(2, [5], seeds=12, date="02-01-2026"),
    ]
    result = select_torrents(torrents, [], catch_up=False, downloaded=None)
    assert [s["torrent"]["id"] for s in result["selected"]] == [2]


def test_processed_ids_not_candidates():
    torrents = [make_torrent(1, [1], seeds=5), make_torrent(2, [2], seeds=7)]
    known = [known_record(t, processed=True, grabbed=True) for t in torrents]
    result = select_torrents(torrents, known, catch_up=False, downloaded=None)
    assert result["selected"] == []
    assert result["new_seen"] == 0


def test_catch_up_selects_all(jade):
    result = select_torrents(jade["torrents"], [], catch_up=True, downloaded=None)
    covered = [ep for s in result["selected"] for ep in s["episodes"]]
    assert sorted(covered) == list(range(1, 92))
    assert len(covered) == len(set(covered))
    assert result["new_seen"] == len(jade["torrents"])


def test_downloaded_subtracted_and_pack_covers_rest():
    downloaded = set(range(1, 61))
    pack = make_torrent(100, list(range(1, 68)), seeds=9, date="01-01-2026")
    single_61 = make_torrent(101, [61], seeds=1)
    single_62 = make_torrent(102, [62], seeds=1)
    result = select_torrents([pack, single_61, single_62], [], catch_up=False, downloaded=downloaded)
    assert [s["torrent"]["id"] for s in result["selected"]] == [101, 102, 100]
    pack_sel = next(s for s in result["selected"] if s["torrent"]["id"] == 100)
    assert pack_sel["episodes"] == [63, 64, 65, 66, 67]

    pack_small = make_torrent(200, list(range(1, 62)), seeds=9)
    single = make_torrent(201, [61], seeds=1)
    result2 = select_torrents([pack_small, single], [], catch_up=False, downloaded=downloaded)
    assert [s["torrent"]["id"] for s in result2["selected"]] == [201]
    pack_processed = next(p for p in result2["processed"] if p["id"] == 200)
    assert pack_processed["grabbed"] is False
    single_processed = next(p for p in result2["processed"] if p["id"] == 201)
    assert single_processed["grabbed"] is True


def release_payload(**overrides):
    data = {
        "url": "https://anistar.org/8883-x.html",
        "topic_id": "8883",
        "search_query": None,
        "topic_title": None,
        "title": "Тестовая раздача [01 из 12]",
        "episodes": [1],
        "interval_minutes": 60,
        "enabled": True,
        "last_status": "monitoring",
        "last_error": None,
        "last_checked_at": "2026-11-06T00:00:00",
        "last_grabbed_at": None,
        "next_check_at": "2026-11-06T01:00:00",
        "created_at": "2026-11-06T00:00:00",
        "updated_at": "2026-11-06T00:00:00",
        "sonarr_series_id": None,
        "sonarr_series_title": None,
        "sonarr_series_type": None,
    }
    data.update(overrides)
    return data


def test_store_anistar_roundtrip(tmp_path):
    store = Store(tmp_path / "t.db")
    store.init()
    known = [{"id": 1, "episodes": [5], "comment": "", "processed": True, "grabbed": True}]
    release = store.create_release(release_payload(source="anistar", known_torrents=known))
    stored = store.get_release(release["id"])
    assert stored["source"] == "anistar"
    assert stored["known_torrents"] == known

    new_known = [{"id": 2, "episodes": [6], "comment": "Многоголосая озвучка", "processed": False, "grabbed": True}]
    updated = store.update_release(release["id"], known_torrents=new_known)
    assert updated["known_torrents"] == new_known
    assert updated["source"] == "anistar"


def test_store_defaults(tmp_path):
    store = Store(tmp_path / "t.db")
    store.init()
    release = store.create_release(release_payload())
    stored = store.get_release(release["id"])
    assert stored["source"] == "prowlarr"
    assert stored["known_torrents"] == []
