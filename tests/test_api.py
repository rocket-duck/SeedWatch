import app.main as main


def add_release(client, services, index=0, series_id=None, search_query="Show"):
    item = services.release(index)
    response = client.post("/api/releases", json={
        "url": item["url"],
        "interval_days": 3,
        "search_query": search_query,
        "sonarr_series_id": series_id,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_settings_are_persisted_and_secrets_stay_masked(client, configure):
    public = configure()
    assert public["prowlarr_api_key_set"] is True
    assert public["sonarr_api_key_set"] is True
    assert public["qbit_api_key_set"] is True
    assert "prowlarr_api_key" not in public
    assert "qbit_api_key" not in public
    assert main.store.get_settings()["qbit_api_key"] == "qbt_test_key"

    response = client.put("/api/settings", json={
        "prowlarr_api_key": "",
        "sonarr_api_key": "",
        "qbit_api_key": "",
    })
    assert response.status_code == 200
    assert main.store.get_settings()["prowlarr_api_key"] == "prowlarr_secret"
    assert main.store.get_settings()["sonarr_api_key"] == "sonarr_secret"
    assert main.store.get_settings()["qbit_api_key"] == "qbt_test_key"


def test_settings_page_and_series_list(client, configure):
    configure()
    page = client.get("/").text
    assert 'id="settings-dialog"' in page
    assert "API key (qBittorrent 5.2+)" in page
    series = client.get("/api/sonarr/series")
    assert series.status_code == 200
    assert len(series.json()) == 3


def test_immediate_sonarr_download_applies_delta_with_bearer(client, configure, services):
    configure()
    release = add_release(client, services, series_id=10)
    assert release["last_status"] == "grabbed"
    assert release["sonarr_series_title"] == "Show"
    assert len(services.grabs) == 1
    assert len(services.priorities) == 1
    assert services.priorities[0]["id"] == ["0"]
    assert services.priorities[0]["priority"] == ["0"]
    assert services.qbit_auth
    assert all(value == "Bearer qbt_test_key" for value in services.qbit_auth)


def test_login_password_auth_is_supported(client, configure, services):
    configure("login")
    release = add_release(client, services, series_id=10)
    assert release["last_status"] == "grabbed"
    assert services.logins
    assert services.logins[0]["username"] == ["admin"]
    assert services.logins[0]["password"] == ["secret"]


def test_immediate_download_without_sonarr(client, configure, services):
    release = add_release(client, services, index=1)
    assert release["last_status"] == "grabbed"
    assert len(services.grabs) == 1
    assert services.priorities == []


def test_unrecognized_video_falls_back_to_full_torrent(client, configure, services):
    configure()
    services.release(1)["files"] = [{"index": 0, "name": "Other 02.mkv"}]
    release = add_release(client, services, index=1, series_id=20)
    assert release["last_status"] == "grabbed"
    assert services.priorities == []
    history = client.get(f"/api/releases/{release['id']}/history").json()
    assert "торрент оставлен полным" in history[0]["message"]


def test_no_download_when_sonarr_has_every_episode(client, configure, services):
    configure()
    services.downloaded[10] = set(range(1, 175))
    release = add_release(client, services, series_id=10)
    assert release["last_status"] == "changed"
    assert services.grabs == []


def test_polling_downloads_new_missing_episode(client, configure, services):
    configure()
    services.downloaded[10] = set(range(1, 175))
    release = add_release(client, services, series_id=10)
    assert services.grabs == []

    services.release(0)["title"] = "Show S01E01-E175"
    services.release(0)["files"] = [
        {"index": 0, "name": "Show.S01E174.mkv"},
        {"index": 1, "name": "Show.S01E175.mkv"},
    ]
    checked = client.post(f"/api/releases/{release['id']}/check").json()
    assert checked["last_status"] == "grabbed"
    assert len(services.grabs) == 1
    assert services.priorities[0]["id"] == ["0"]

    repeated = client.post(f"/api/releases/{release['id']}/check").json()
    assert repeated["last_status"] == "ok"
    assert len(services.grabs) == 1


def test_new_season_episodes_are_matched_against_their_own_season(client, configure, services):
    configure()
    services.releases.append({
        "url": "https://kinozal.guru/details.php?id=789",
        "title": "Demon - S3E1-24 - 2026 MVO WEBRip 1080p - RUSSIAN",
        "indexerId": 4,
        "hash": "demon3",
        "files": [{"index": 0, "name": "Demon.S03E24.mkv"}],
    })
    release = add_release(client, services, index=3, series_id=30)
    assert release["last_status"] == "grabbed"
    assert len(services.grabs) == 1

    services.release(3)["title"] = "Demon - S3E1-31 - 2026 MVO WEBRip 1080p - RUSSIAN"
    services.release(3)["files"] = [
        {"index": i, "name": f"Demon.S03E{number}.mkv"}
        for i, number in enumerate(range(24, 32))
    ]
    checked = client.post(f"/api/releases/{release['id']}/check").json()
    assert checked["last_status"] == "grabbed"
    assert len(services.grabs) == 2


def test_grab_sets_sonarr_category_in_qbit(client, configure, services):
    configure()
    release = add_release(client, services, series_id=10)
    assert release["last_status"] == "grabbed"
    assert services.categories == [{"hash": "abc123", "category": "tv-sonarr"}]
    history = client.get(f"/api/releases/{release['id']}/history").json()
    assert "категория" not in history[0]["message"]


def test_update_release_interval_query_and_sonarr(client, configure, services):
    configure()
    release = add_release(client, services, series_id=10)
    url = f"/api/releases/{release['id']}"

    response = client.patch(url, json={
        "interval_days": 7,
        "search_query": "New Query",
        "sonarr_series_id": 20,
    })
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["interval_minutes"] == 7 * 1440
    assert updated["search_query"] == "New Query"
    assert updated["sonarr_series_id"] == 20
    assert updated["sonarr_series_title"] == "Other"
    assert updated["sonarr_series_type"] == "standard"

    response = client.patch(url, json={"search_query": None, "sonarr_series_id": None})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["search_query"] is None
    assert updated["sonarr_series_id"] is None
    assert updated["sonarr_series_title"] is None
    assert updated["sonarr_series_type"] is None


def test_edit_dialog_markup(client):
    page = client.get("/").text
    assert 'id="edit-dialog"' in page
    script = client.get("/static/app.js").text
    assert "edit-dialog" in script


def test_pages_and_static_assets_disable_caching(client):
    for path in ("/", "/static/app.js", "/static/styles.css", "/static/index.html"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path


def test_poll_parses_episodes_from_topic_page_title(client, configure, services):
    configure()
    url = f"{services.base_url}/details.php?id=555"
    services.releases.append({
        "url": url,
        "title": "Tunshi Xingkong (Swallowed Star) 2026 MVO WEBRip 1080p - RUSSIAN",
        "indexerId": 5,
        "hash": "sw555",
        "files": [
            {"index": i, "name": f"Swallowed.Star.S05E{number}.mkv"}
            for i, number in enumerate(range(33, 40))
        ],
    })
    services.page_titles[url] = (
        "Пожиратель звёзд (5 сезон: 1-39 серии из 52) / Tunshi Xingkong (Swallowed Star) / 2026 WEBRip (1080p)"
    )
    release = add_release(client, services, index=3, series_id=30, search_query="Swallowed Star")
    assert release["last_status"] == "grabbed"
    assert release["episodes"] == list(range(1, 40))
    assert "Пожиратель звёзд" in release["title"]
    assert len(services.grabs) == 1

    services.page_titles[url] = (
        "Пожиратель звёзд (5 сезон: 1-45 серии из 52) / Tunshi Xingkong (Swallowed Star) / 2026 WEBRip (1080p)"
    )
    services.release(3)["files"] = [
        {"index": i, "name": f"Swallowed.Star.S05E{number}.mkv"}
        for i, number in enumerate(range(40, 46))
    ]
    checked = client.post(f"/api/releases/{release['id']}/check").json()
    assert checked["last_status"] == "grabbed"
    assert checked["episodes"] == list(range(1, 46))
    assert len(services.grabs) == 2


KINOZAL_LOGIN_HTML = (
    '<html><head><title>Вход</title></head><body>'
    '<form action="/takelogin.php"><input type="hidden" name="touser" value="1">'
    '<input type="hidden" name="wact" value="takerecover">'
    '<input name="username"><input type="password" name="password"></form></body></html>'
)
RUTRACKER_LOGIN_HTML = (
    '<html><head><title>rutracker.org</title></head><body>'
    '<form action="login.php"><input name="login_username">'
    '<input name="login_password"></form></body></html>'
)


def add_kinozal_swallowed_star(services, topic_id):
    url = f"https://kinozal.guru/details.php?id={topic_id}"
    services.releases.append({
        "url": url,
        "title": "Tunshi Xingkong (Swallowed Star) 2026 MVO WEBRip 1080p - RUSSIAN",
        "indexerId": 5,
        "hash": f"sw{topic_id}",
        "files": [
            {"index": i, "name": f"Swallowed.Star.S05E{number}.mkv"}
            for i, number in enumerate(range(33, 40))
        ],
    })
    services.fs_private_pages[url] = (
        "Пожиратель звёзд (5 сезон: 1-39 серии из 52) / Tunshi Xingkong (Swallowed Star) / 2026 WEBRip (1080p)"
    )
    services.fs_pages_raw["https://kinozal.guru/login.php"] = KINOZAL_LOGIN_HTML
    return url


def test_kinozal_fetch_logs_in_via_flaresolverr(client, configure, services):
    url = add_kinozal_swallowed_star(services, 777)
    configure(
        flaresolverr_url=services.base_url,
        kinozal_username="ku",
        kinozal_password="kp",
    )
    release = add_release(client, services, index=3, series_id=30, search_query="Swallowed Star")
    assert release["last_status"] == "grabbed"
    assert release["episodes"] == list(range(1, 40))
    assert len(services.grabs) == 1


def test_rutracker_fetch_logs_in_via_flaresolverr(client, configure, services):
    url = "https://rutracker.org/forum/viewtopic.php?t=999"
    services.releases.append({
        "url": url,
        "title": "Static Name WEBRip 1080p - RUSSIAN",
        "indexerId": 6,
        "hash": "rt999",
        "files": [
            {"index": i, "name": f"Show.S05E{number}.mkv"}
            for i, number in enumerate(range(33, 40))
        ],
    })
    services.page_titles[url] = (
        "Шоу (5 сезон: 1-39 серии из 52) / Static Name / 2026 WEBRip (1080p) :: RuTracker.org"
    )
    services.fs_pages_raw[url] = RUTRACKER_LOGIN_HTML
    configure(
        flaresolverr_url=services.base_url,
        rutracker_username="rt",
        rutracker_password="rp",
    )
    release = add_release(client, services, index=3, series_id=30, search_query="Static")
    assert release["last_status"] == "grabbed"
    assert release["episodes"] == list(range(1, 40))
    assert len(services.grabs) == 1


def test_kinozal_wrong_password_falls_back_to_indexer_title(client, configure, services):
    add_kinozal_swallowed_star(services, 778)
    configure(
        flaresolverr_url=services.base_url,
        kinozal_username="ku",
        kinozal_password="wrong",
    )
    release = add_release(client, services, index=3, series_id=30, search_query="Swallowed Star")
    assert release["last_status"] == "changed"
    assert services.grabs == []


def test_tracker_settings_are_persisted_and_masked(client, configure):
    public = configure(
        flaresolverr_url="http://localhost:8191",
        rutracker_username="rt_user",
        rutracker_password="rt_pass",
        kinozal_username="kz_user",
        kinozal_password="kz_pass",
    )
    assert public["flaresolverr_url"] == "http://localhost:8191"
    assert public["rutracker_username"] == "rt_user"
    assert public["rutracker_password_set"] is True
    assert public["kinozal_password_set"] is True
    assert "rutracker_password" not in public
    assert "kinozal_password" not in public
    assert main.store.get_settings()["kinozal_password"] == "kz_pass"

    response = client.put("/api/settings", json={"rutracker_password": "", "kinozal_password": ""})
    assert response.status_code == 200
    assert main.store.get_settings()["rutracker_password"] == "rt_pass"
    assert main.store.get_settings()["kinozal_password"] == "kz_pass"


def test_duplicate_add_reports_conflict(client, configure, services, monkeypatch):
    import sqlite3

    configure()
    add_release(client, services, series_id=10)
    monkeypatch.setattr(main.store, "get_by_url", lambda url: None)

    def boom(data):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: releases.url")

    monkeypatch.setattr(main.store, "create_release", boom)
    item = services.release(0)
    response = client.post("/api/releases", json={
        "url": item["url"],
        "interval_days": 3,
        "search_query": "Show",
        "sonarr_series_id": 10,
    })
    assert response.status_code == 400
    assert "уже добавлена" in response.json()["detail"]


def test_delta_skips_files_with_absolute_episode_markers(client, configure, services):
    configure()
    services.releases.append({
        "url": "https://kinozal.guru/details.php?id=888",
        "title": "Tunshi Xingkong (Swallowed Star) 2026 MVO WEBRip 1080p - RUSSIAN",
        "indexerId": 5,
        "hash": "sw888",
        "files": [
            {"index": 0, "name": "Tunshi.Xingkong.Ona.S05e33-E241 - 1080.mp4"},
            {"index": 1, "name": "Tunshi.Xingkong.Ona.S05e34-E242 - 1080.mp4"},
            {"index": 2, "name": "Tunshi.Xingkong.Ona.S05e01-E209 - 1080.mp4"},
            {"index": 3, "name": "Tunshi.Xingkong.Ona.S05e11-film - 1080.mp4"},
        ],
    })
    services.fs_private_pages["https://kinozal.guru/details.php?id=888"] = (
        "Пожиратель звёзд (5 сезон: 1-34 серии из 52) / Tunshi Xingkong / 2026 WEBRip (1080p)"
    )
    services.fs_pages_raw["https://kinozal.guru/login.php"] = KINOZAL_LOGIN_HTML
    configure(flaresolverr_url=services.base_url, kinozal_username="ku", kinozal_password="kp")
    release = add_release(client, services, index=3, series_id=30, search_query="Swallowed Star")
    assert release["last_status"] == "grabbed"
    assert services.priorities[0]["id"] == ["2|3"]
    history = client.get(f"/api/releases/{release['id']}/history").json()
    assert "файлов пропущено: 2" in history[0]["message"]


def test_delta_skips_unrecognized_files_when_episodes_match(client, configure, services):
    configure()
    services.releases.append({
        "url": "https://kinozal.guru/details.php?id=901",
        "title": "Zhe Tian 2026 WEBDL 1080p - RUSSIAN",
        "indexerId": 7,
        "hash": "zt901",
        "files": [
            {"index": 0, "name": "Zhe Tian (33) серия.mkv"},
            {"index": 1, "name": "Zhe Tian (34) серия.mkv"},
            {"index": 2, "name": "Shrouding the Heavens (156) фильм Битва.mp4"},
            {"index": 3, "name": "Zhe Tian (32) серия.mkv"},
        ],
    })
    services.fs_private_pages["https://kinozal.guru/details.php?id=901"] = (
        "Окутывая небеса (5 сезон: 1-34 серии из 52 + Фильм) / Zhe Tian / 2023-2026 WEB-DL (1080p)"
    )
    services.fs_pages_raw["https://kinozal.guru/login.php"] = KINOZAL_LOGIN_HTML
    configure(flaresolverr_url=services.base_url, kinozal_username="ku", kinozal_password="kp")
    release = add_release(client, services, index=3, series_id=30, search_query="Zhe Tian")
    assert release["last_status"] == "grabbed"
    assert services.priorities[0]["id"] == ["2|3"]
    history = client.get(f"/api/releases/{release['id']}/history").json()
    assert "файлов пропущено: 2" in history[0]["message"]
    assert "Не найдены файлы" not in history[0]["message"]


def test_delta_applies_partial_skip_with_warning(client, configure, services):
    configure()
    services.releases.append({
        "url": "https://kinozal.guru/details.php?id=902",
        "title": "Zhe Tian 2026 WEBDL 1080p - RUSSIAN",
        "indexerId": 7,
        "hash": "zt902",
        "files": [
            {"index": 0, "name": "Zhe Tian (33) серия.mkv"},
            {"index": 1, "name": "Shrouding the Heavens (156) фильм Битва.mp4"},
        ],
    })
    services.fs_private_pages["https://kinozal.guru/details.php?id=902"] = (
        "Окутывая небеса (5 сезон: 1-34 серии из 52 + Фильм) / Zhe Tian / 2023-2026 WEB-DL (1080p)"
    )
    services.fs_pages_raw["https://kinozal.guru/login.php"] = KINOZAL_LOGIN_HTML
    configure(flaresolverr_url=services.base_url, kinozal_username="ku", kinozal_password="kp")
    release = add_release(client, services, index=3, series_id=30, search_query="Zhe Tian")
    assert release["last_status"] == "grabbed"
    assert services.priorities[0]["id"] == ["1"]
    history = client.get(f"/api/releases/{release['id']}/history").json()
    assert "Не найдены файлы для серий 34" in history[0]["message"]


def test_aniliberty_url_is_supported(client, configure, services):
    release = add_release(client, services, index=2)
    assert release["last_status"] == "grabbed"
    assert services.grabs[0]["guid"] == services.release(2)["url"]


def test_service_statuses_and_toolbar(client, configure):
    configure()
    for service, version in (("prowlarr", "test"), ("sonarr", "test-sonarr"), ("qbit", "5.2.0-test")):
        response = client.get(f"/api/{service}/status")
        assert response.status_code == 200, response.text
        assert response.json()["version"] == version

    page = client.get("/").text
    for service in ("prowlarr", "sonarr", "qbit"):
        assert f'id="{service}-status"' in page
    script = client.get("/static/app.js").text
    assert "'************'" in script


def test_connection_checks_retry_and_time_out(client):
    script = client.get("/static/app.js").text
    assert "AbortController" in script
    assert "setInterval(checkConnections" in script


def test_long_operations_use_extended_timeouts(client):
    script = client.get("/static/app.js").text
    assert "options.timeout" in script
    assert "300000" in script


def test_urls_without_scheme_are_normalized(client, services):
    address = services.base_url.removeprefix("http://")
    response = client.put("/api/settings", json={
        "prowlarr_url": address,
        "sonarr_url": address,
        "qbit_url": address,
    })
    assert response.status_code == 200, response.text
    for name in ("prowlarr_url", "sonarr_url", "qbit_url"):
        assert response.json()[name] == services.base_url


def test_forbidden_topic_uses_sonarr_title_for_prowlarr(client, configure, services):
    configure()
    release = add_release(client, services, series_id=10, search_query=None)
    assert release["last_status"] == "grabbed"
    assert release["search_query"] == "Show"
    assert services.searches == ["Show", "Show"]


def test_forbidden_topic_without_sonarr_asks_for_query(client, configure, services):
    configure()
    item = services.release(0)
    response = client.post("/api/releases", json={
        "url": item["url"],
        "interval_days": 3,
        "search_query": None,
        "sonarr_series_id": None,
    })
    assert response.status_code == 400
    assert "Укажите поисковый запрос вручную" in response.json()["detail"]
    assert services.searches == []
