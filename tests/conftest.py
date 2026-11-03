import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
import app.main as main
from app.monitor import Monitor
from app.store import Store


class MockServices:
    def __init__(self):
        self.releases = [
            {
                "url": "https://rutracker.org/forum/viewtopic.php?t=123",
                "title": "Show S01E01-E174",
                "indexerId": 1,
                "hash": "abc123",
                "files": [
                    {"index": 0, "name": "Show.S01E173.mkv"},
                    {"index": 1, "name": "Show.S01E174.mkv"},
                ],
            },
            {
                "url": "https://kinozal.tv/details.php?id=456",
                "title": "Other E01-E02",
                "indexerId": 2,
                "hash": "def456",
                "files": [
                    {"index": 0, "name": "Other.E01.mkv"},
                    {"index": 1, "name": "Other.E02.mkv"},
                ],
            },
            {
                "url": "https://aniliberty.top/anime/releases/release/show/",
                "title": "Ani E01-E24",
                "indexerId": 3,
                "hash": "ghi789",
                "files": [{"index": 0, "name": "Ani.E24.mkv"}],
            },
        ]
        self.series = {
            10: {"id": 10, "title": "Show", "year": 2023, "seriesType": "anime"},
            20: {"id": 20, "title": "Other", "year": 2024, "seriesType": "standard"},
            30: {"id": 30, "title": "Demon", "year": 2026, "seriesType": "standard"},
        }
        self.downloaded = {10: set(range(1, 174)), 20: {1}}
        self.season_files = {
            30: {1: set(range(1, 27)), 2: set(range(1, 37)), 3: set(range(1, 24))},
        }
        self.torrents = []
        self.grabs = []
        self.searches = []
        self.priorities = []
        self.categories = []
        self.logins = []
        self.qbit_auth = []
        self.auth_method = "api_key"
        self.api_key = "qbt_test_key"
        self.base_url = ""

    def release(self, index=0):
        return self.releases[index]


@pytest.fixture
def services():
    mock = MockServices()
    mock_server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(mock))
    mock.base_url = f"http://127.0.0.1:{mock_server.server_port}"
    thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
    thread.start()
    yield mock
    mock_server.shutdown()
    mock_server.server_close()
    thread.join(timeout=2)


def handler_for(mock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            return self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith(("/forum/", "/details.php")):
                return self.send_json({"detail": "forbidden"}, 403)
            if parsed.path == "/api/v1/system/status":
                return self.send_json({"version": "test"})
            if parsed.path == "/api/v1/search":
                mock.searches.append(parse_qs(parsed.query)["query"][0])
                return self.send_json([
                    {
                        "title": item["title"],
                        "guid": item["url"],
                        "infoUrl": item["url"],
                        "indexerId": item["indexerId"],
                    }
                    for item in mock.releases
                ])
            if parsed.path == "/api/v3/system/status":
                return self.send_json({"version": "test-sonarr"})
            if parsed.path == "/api/v3/series":
                return self.send_json(list(mock.series.values()))
            if parsed.path.startswith("/api/v3/series/"):
                series_id = int(parsed.path.rsplit("/", 1)[1])
                if series_id in mock.series:
                    return self.send_json(mock.series[series_id])
                return self.send_json({"detail": "not found"}, 404)
            if parsed.path == "/api/v3/downloadclient":
                return self.send_json([
                    {
                        "name": "qBittorrent",
                        "implementation": "QBittorrent",
                        "fields": [{"name": "tvCategory", "value": "tv-sonarr"}],
                    }
                ])
            if parsed.path == "/api/v3/episode":
                series_id = int(parse_qs(parsed.query)["seriesId"][0])
                if series_id in mock.season_files:
                    return self.send_json([
                        {
                            "seasonNumber": season,
                            "episodeNumber": number,
                            "absoluteEpisodeNumber": None,
                            "hasFile": number in files,
                        }
                        for season, files in mock.season_files[series_id].items()
                        for number in range(1, max(files) + 2)
                    ])
                return self.send_json([
                    {
                        "seasonNumber": 1,
                        "episodeNumber": number,
                        "absoluteEpisodeNumber": number,
                        "hasFile": number in mock.downloaded.get(series_id, set()),
                    }
                    for number in range(1, 201)
                ])
            if parsed.path.startswith("/api/v2/"):
                if not self.qbit_authorized():
                    return self.send_json({"detail": "forbidden"}, 403)
                if parsed.path == "/api/v2/app/version":
                    body = b"5.2.0-test"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path == "/api/v2/torrents/info":
                    return self.send_json(mock.torrents)
                if parsed.path == "/api/v2/torrents/files":
                    torrent_hash = parse_qs(parsed.query)["hash"][0]
                    item = next((item for item in mock.releases if item["hash"] == torrent_hash), None)
                    return self.send_json(item["files"] if item else [])
            return self.send_json({"detail": "not found"}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            data = self.body()
            if parsed.path == "/api/v2/auth/login":
                mock.logins.append(parse_qs(data))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Ok.")
                return
            if parsed.path == "/api/v1/search":
                payload = json.loads(data)
                mock.grabs.append(payload)
                item = next(item for item in mock.releases if item["url"] == payload["guid"])
                mock.torrents.append({"hash": item["hash"], "added_on": len(mock.torrents) + 1})
                return self.send_json({})
            if parsed.path == "/api/v2/torrents/filePrio":
                if not self.qbit_authorized():
                    return self.send_json({"detail": "forbidden"}, 403)
                mock.priorities.append(parse_qs(data))
                return self.send_json({})
            if parsed.path == "/api/v2/torrents/setCategory":
                if not self.qbit_authorized():
                    return self.send_json({"detail": "forbidden"}, 403)
                params = parse_qs(data)
                mock.categories.append({"hash": params["hashes"][0], "category": params["category"][0]})
                return self.send_json({})
            return self.send_json({"detail": "not found"}, 404)

        def qbit_authorized(self):
            authorization = self.headers.get("Authorization")
            mock.qbit_auth.append(authorization)
            return mock.auth_method != "api_key" or authorization == f"Bearer {mock.api_key}"

    return Handler


@pytest.fixture
def client(services, tmp_path):
    store = Store(tmp_path / "monitor.db")
    settings = Settings(
        prowlarr_url=services.base_url,
        prowlarr_api_key="prowlarr_secret",
        prowlarr_download_client_id=None,
        sonarr_url=services.base_url,
        sonarr_api_key="sonarr_secret",
        qbit_url=services.base_url,
        qbit_auth_method=services.auth_method,
        qbit_username="admin",
        qbit_password="secret",
        qbit_api_key=services.api_key,
        qbit_metadata_timeout=2,
        database_path=tmp_path / "monitor.db",
        request_timeout=5,
    )
    main.store = store
    main.monitor = Monitor(settings, store)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def configure(client, services):
    def apply(auth_method="api_key"):
        services.auth_method = auth_method
        response = client.put("/api/settings", json={
            "prowlarr_url": services.base_url,
            "prowlarr_api_key": "prowlarr_secret",
            "sonarr_url": services.base_url,
            "sonarr_api_key": "sonarr_secret",
            "qbit_url": services.base_url,
            "qbit_auth_method": auth_method,
            "qbit_username": "admin",
            "qbit_password": "secret",
            "qbit_api_key": services.api_key,
        })
        assert response.status_code == 200, response.text
        return response.json()

    return apply
