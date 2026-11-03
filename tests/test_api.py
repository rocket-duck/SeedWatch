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
    assert "tv-sonarr" in history[0]["message"]


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
