import asyncio
import re
import time
from pathlib import PurePosixPath

import httpx

from .releases import parse_episodes


VIDEO_EXTENSIONS = {".avi", ".m2ts", ".mkv", ".mp4", ".ts"}


class QbitError(RuntimeError):
    pass


class QbitClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float, metadata_timeout: float):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.metadata_timeout = metadata_timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"{self.base_url}/api/v2", timeout=self.timeout)

    async def _login(self, client: httpx.AsyncClient) -> None:
        if not self.username:
            return
        response = await client.post(
            "/auth/login",
            data={"username": self.username, "password": self.password},
        )
        if response.is_error or response.text.strip() != "Ok.":
            raise QbitError("qBittorrent отклонил авторизацию")

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.is_error:
            raise QbitError(f"qBittorrent вернул HTTP {response.status_code}: {response.text.strip()[:500]}")

    async def snapshot(self) -> set[str]:
        try:
            async with self._client() as client:
                await self._login(client)
                response = await client.get("/torrents/info")
                self._check(response)
                return {torrent["hash"].lower() for torrent in response.json()}
        except httpx.HTTPError as exc:
            raise QbitError(f"qBittorrent недоступен: {exc}") from exc

    async def apply_delta(self, previous_hashes: set[str], release: dict, missing_episodes: list[int]) -> str:
        try:
            async with self._client() as client:
                await self._login(client)
                torrent_hash = await self._find_new_torrent(client, previous_hashes, release)
                files = await self._wait_for_files(client, torrent_hash)
                return await self._skip_downloaded(client, torrent_hash, files, set(missing_episodes))
        except httpx.HTTPError as exc:
            raise QbitError(f"qBittorrent недоступен: {exc}") from exc

    async def _find_new_torrent(self, client: httpx.AsyncClient, previous_hashes: set[str], release: dict) -> str:
        deadline = time.monotonic() + self.metadata_timeout
        expected_hash = (release.get("infoHash") or "").lower()
        while time.monotonic() < deadline:
            response = await client.get("/torrents/info")
            self._check(response)
            torrents = response.json()
            if expected_hash and any(torrent["hash"].lower() == expected_hash for torrent in torrents):
                return expected_hash
            new_torrents = [torrent for torrent in torrents if torrent["hash"].lower() not in previous_hashes]
            if len(new_torrents) == 1:
                return new_torrents[0]["hash"].lower()
            if len(new_torrents) > 1:
                raise QbitError("В qBittorrent появилось несколько новых торрентов, дельта не применена")
            await asyncio.sleep(1)
        raise QbitError("Новый торрент не появился в qBittorrent, дельта не применена")

    async def _wait_for_files(self, client: httpx.AsyncClient, torrent_hash: str) -> list[dict]:
        deadline = time.monotonic() + self.metadata_timeout
        while time.monotonic() < deadline:
            response = await client.get("/torrents/files", params={"hash": torrent_hash})
            self._check(response)
            files = response.json()
            if files:
                return files
            await asyncio.sleep(1)
        raise QbitError("qBittorrent не получил метаданные торрента, дельта не применена")

    async def _skip_downloaded(
        self,
        client: httpx.AsyncClient,
        torrent_hash: str,
        files: list[dict],
        missing_episodes: set[int],
    ) -> str:
        matched = set()
        skip_ids = []
        for file in files:
            path = PurePosixPath(file["name"])
            episodes = self._file_episodes(path.name)
            if not episodes:
                if path.suffix.lower() in VIDEO_EXTENSIONS:
                    return f"Не удалось распознать серии в файле {path.name}; торрент оставлен полным"
                continue
            wanted = episodes & missing_episodes
            if wanted:
                matched.update(wanted)
            else:
                if "index" not in file:
                    return "qBittorrent не вернул индексы файлов; торрент оставлен полным"
                skip_ids.append(str(file["index"]))
        if matched != missing_episodes:
            missing = ", ".join(map(str, sorted(missing_episodes - matched)))
            return f"Не найдены файлы для серий {missing}; торрент оставлен полным"
        if skip_ids:
            response = await client.post(
                "/torrents/filePrio",
                data={"hash": torrent_hash, "id": "|".join(skip_ids), "priority": 0},
            )
            self._check(response)
        return f"В qBittorrent отключены уже скачанные серии; файлов пропущено: {len(skip_ids)}"

    @staticmethod
    def _file_episodes(filename: str) -> set[int]:
        episodes = set(parse_episodes(filename))
        if episodes:
            return episodes
        match = re.match(r"^(\d{1,4})(?:\D|$)", filename)
        if not match:
            match = re.search(r"\[(\d{1,4})\]", filename)
        return {int(match.group(1))} if match else set()
