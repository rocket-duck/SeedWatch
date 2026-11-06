from pathlib import Path

import pytest

import app.anistar as anistar_module
import app.main as main
from app.anistar import parse_series_page, select_torrents

FIXTURES = Path(__file__).parent / "fixtures"
JADE_PATH = "/8883-jade.html"

TORRENT_BLOCK = """<div id="torrent_{tid}_info" class="torrent">
\t<div class="title">
\t\t<a  href="/engine/gettorrent.php?id={tid}">
\t\t<div class="info_c">&nbsp;</div>
\t\t<div class="download">&nbsp;</div>
\t\t<div class="info_d">
\t\t\t<div class="info_d1">{label}</div>
\t\t</div>
\t\t</a>
\t</div>
\t<div class="cont">
\t\t<div class="bord_a1">
\t\t\t<div class="li_distribute_a1">
\t\t\t\t<b>Раздают: </b> <div class="li_distribute">{seeds}</div>
\t\t\t</div>
\t\t</div>
\t\t<div class="bord_a1">
\t\t<b>Размер: </b> {size}
\t\t</div>
\t\t<div class="bord_a1">
\t\t<b>Дата: </b> {date}
\t\t</div>
\t</div>
</div>"""

EMPTY_PAGE = (
    "<html><head><title>Пусто</title></head>"
    "<body><h1 itemprop=\"name\">Пустой сериал</h1></body></html>"
)


@pytest.fixture
def anistar(services, monkeypatch):
    monkeypatch.setattr(anistar_module, "ANISTAR_HOST_SUFFIXES", ("127.0.0.1",))
    services.anistar_pages[f"{services.base_url}{JADE_PATH}"] = jade_page_bytes()
    return services


def jade_html() -> str:
    return (FIXTURES / "anistar_jade.html").read_bytes().decode("cp1251")


def jade_page_bytes(*blocks: str) -> bytes:
    html = jade_html()
    if blocks:
        marker = '<div class="list_torrent"'
        html = html.replace(marker, "".join(blocks) + marker, 1)
    return html.encode("cp1251")


def episode_block(tid: int, episode: int, seeds: int = 5) -> str:
    return TORRENT_BLOCK.format(
        tid=tid, label=f"Серия {episode} (800 Mb)", seeds=seeds, size="800 Mb", date="05-11-2026"
    )


def seed_torrent(services, tid: int) -> None:
    services.anistar_torrents[tid] = b"d8:announce://anistar/" + f"id={tid}".encode()


def seed_jade_torrents(services) -> dict:
    page = parse_series_page(jade_html())
    for torrent in page["torrents"]:
        seed_torrent(services, torrent["id"])
    return page


def add_jade(client, services, series_id=None):
    payload = {"url": f"{services.base_url}{JADE_PATH}", "interval_days": 1}
    if series_id:
        payload["sonarr_series_id"] = series_id
    response = client.post("/api/releases", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def history_messages(client, release_id):
    history = client.get(f"/api/releases/{release_id}/history").json()
    return [entry["message"] for entry in history]


def test_sonarr_series_detail_reports_downloaded_count(client, anistar):
    response = client.get("/api/sonarr/series/10")

    assert response.status_code == 200, response.text
    series = response.json()
    assert series["id"] == 10
    assert series["seriesType"] == "anime"
    assert series["downloaded_count"] == 173


def test_sonarr_series_detail_empty_series(client, anistar):
    response = client.get("/api/sonarr/series/40")

    assert response.status_code == 200, response.text
    assert response.json()["downloaded_count"] == 0


def test_anistar_duplicate_add_reports_conflict(client, anistar, monkeypatch):
    import sqlite3

    add_jade(client, anistar)
    monkeypatch.setattr(main.store, "get_by_url", lambda url: None)

    def boom(data):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: releases.url")

    monkeypatch.setattr(main.store, "create_release", boom)

    response = client.post("/api/releases", json={
        "url": f"{anistar.base_url}{JADE_PATH}",
        "interval_days": 1,
    })

    assert response.status_code == 400
    assert "уже добавлена" in response.json()["detail"]


def test_add_baseline_no_sonarr(client, anistar):
    release = add_jade(client, anistar)

    assert release["source"] == "anistar"
    assert release["title"] == "Убийца богов / Нефритовая династия / Zhu Xian / Jade Dynasty"
    assert release["episodes"]
    assert release["known_torrents"]
    assert all(record["processed"] for record in release["known_torrents"])
    assert anistar.added_torrents == []


def test_add_catch_up_empty_sonarr(client, anistar):
    page = seed_jade_torrents(anistar)
    release = add_jade(client, anistar, series_id=40)

    expected = select_torrents(page["torrents"], [], catch_up=True, downloaded=None)
    assert len(anistar.added_torrents) == len(expected["selected"])
    episode_14 = next(item for item in expected["selected"] if 14 in item["episodes"])
    assert episode_14["torrent"]["id"] == 31051
    assert episode_14["torrent"]["comment"] == "Многоголосая озвучка"
    messages = history_messages(client, release["id"])
    assert any(
        "Серия 14: отправлен торрент 31051 (многоголосая озвучка, сиды 2)" in message
        for message in messages
    )


def test_add_baseline_partial_sonarr(client, anistar):
    release = add_jade(client, anistar, series_id=10)

    assert anistar.added_torrents == []
    assert all(record["processed"] for record in release["known_torrents"])


def test_check_sends_new_episode(client, anistar):
    release = add_jade(client, anistar)
    anistar.anistar_pages[f"{anistar.base_url}{JADE_PATH}"] = jade_page_bytes(
        episode_block(99999, 92)
    )
    seed_torrent(anistar, 99999)

    response = client.post(f"/api/releases/{release['id']}/check")

    assert response.status_code == 200, response.text
    assert response.json()["last_status"] == "grabbed"
    assert len(anistar.added_torrents) == 1
    assert b"id=99999" in anistar.added_torrents[0]
    messages = history_messages(client, release["id"])
    assert any("Серия 92" in message and "99999" in message for message in messages)


def test_check_skips_sonarr_downloaded(client, anistar):
    anistar.custom_episodes[50] = [
        {"seasonNumber": 1, "episodeNumber": 92, "absoluteEpisodeNumber": 92, "hasFile": True}
    ]
    release = add_jade(client, anistar, series_id=50)
    anistar.anistar_pages[f"{anistar.base_url}{JADE_PATH}"] = jade_page_bytes(
        episode_block(99999, 92)
    )

    response = client.post(f"/api/releases/{release['id']}/check")

    assert response.status_code == 200, response.text
    assert response.json()["last_status"] == "ok"
    assert anistar.added_torrents == []
    assert anistar.gettorrent_requests == []
    messages = history_messages(client, release["id"])
    assert any("Серия 92 уже скачана в Sonarr" in message for message in messages)


def test_check_threshold(client, anistar):
    release = add_jade(client, anistar)
    blocks = "".join(episode_block(99001 + index, 100 + index) for index in range(20))
    anistar.anistar_pages[f"{anistar.base_url}{JADE_PATH}"] = jade_page_bytes(blocks)

    response = client.post(f"/api/releases/{release['id']}/check")

    assert response.status_code == 200, response.text
    assert response.json()["last_status"] == "error"
    assert "подозрительно много" in response.json()["last_error"].lower()
    assert anistar.added_torrents == []
    assert anistar.gettorrent_requests == []
    messages = history_messages(client, release["id"])
    assert any("подозрительно много" in message.lower() for message in messages)


def test_check_zero_torrents(client, anistar):
    release = add_jade(client, anistar)
    anistar.anistar_pages[f"{anistar.base_url}{JADE_PATH}"] = EMPTY_PAGE.encode("cp1251")

    response = client.post(f"/api/releases/{release['id']}/check")

    assert response.status_code == 200, response.text
    assert response.json()["last_status"] == "error"
    assert "торренты не найдены" in response.json()["last_error"]


def test_check_torrent_download_failure(client, anistar):
    release = add_jade(client, anistar)
    anistar.anistar_pages[f"{anistar.base_url}{JADE_PATH}"] = jade_page_bytes(
        episode_block(99999, 92)
    )

    response = client.post(f"/api/releases/{release['id']}/check")
    assert response.json()["last_status"] == "error"
    assert anistar.gettorrent_requests == [99999]

    record = next(
        item for item in response.json()["known_torrents"] if item["id"] == 99999
    )
    assert record["processed"] is False

    again = client.post(f"/api/releases/{release['id']}/check")
    assert again.json()["last_status"] == "error"
    assert anistar.gettorrent_requests == [99999, 99999]
    assert anistar.added_torrents == []


def test_check_requires_qbit(client, anistar):
    release = add_jade(client, anistar)
    response = client.put("/api/settings", json={"qbit_url": "http://"})
    assert response.status_code == 200, response.text
    anistar.anistar_pages[f"{anistar.base_url}{JADE_PATH}"] = jade_page_bytes(
        episode_block(99999, 92)
    )
    seed_torrent(anistar, 99999)

    response = client.post(f"/api/releases/{release['id']}/check")

    assert response.status_code == 200, response.text
    assert response.json()["last_status"] == "error"
    assert "QBIT_URL" in response.json()["last_error"]
    assert anistar.added_torrents == []
    assert anistar.gettorrent_requests == []
