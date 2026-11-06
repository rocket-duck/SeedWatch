import httpx


class SonarrError(RuntimeError):
    pass


class SonarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v3",
            headers={"X-Api-Key": self.api_key},
            timeout=self.timeout,
        )

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.is_error:
            raise SonarrError(f"Sonarr вернул HTTP {response.status_code}: {response.text.strip()[:500]}")

    async def status(self) -> dict:
        if not self.configured:
            raise SonarrError("Не заданы URL или API key Sonarr")
        try:
            async with self._client() as client:
                response = await client.get("/system/status")
                self._check(response)
                return response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"Sonarr недоступен: {exc}") from exc

    async def list_series(self) -> list[dict]:
        if not self.configured:
            raise SonarrError("Не заданы SONARR_URL или SONARR_API_KEY")
        try:
            async with self._client() as client:
                response = await client.get("/series")
                self._check(response)
                return sorted(response.json(), key=lambda series: series["title"].casefold())
        except httpx.HTTPError as exc:
            raise SonarrError(f"Sonarr недоступен: {exc}") from exc

    async def get_series(self, series_id: int) -> dict:
        if not self.configured:
            raise SonarrError("Не заданы SONARR_URL или SONARR_API_KEY")
        try:
            async with self._client() as client:
                response = await client.get(f"/series/{series_id}")
                self._check(response)
                return response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"Sonarr недоступен: {exc}") from exc

    async def download_category(self) -> str | None:
        try:
            async with self._client() as client:
                response = await client.get("/downloadclient")
                self._check(response)
                clients = response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"Sonarr недоступен: {exc}") from exc
        for client_config in clients:
            if "qbittorrent" not in str(client_config.get("implementation", "")).lower():
                continue
            for field in client_config.get("fields", []):
                if field.get("name") == "tvCategory" and field.get("value"):
                    return str(field["value"]).strip()
        return None

    async def downloaded_episodes(
        self,
        series_id: int,
        series_type: str,
        season: int | None = None,
        force_absolute: bool = False,
    ) -> set[int]:
        try:
            async with self._client() as client:
                response = await client.get("/episode", params={"seriesId": series_id})
                self._check(response)
                episodes = response.json()
        except httpx.HTTPError as exc:
            raise SonarrError(f"Sonarr недоступен: {exc}") from exc
        downloaded = set()
        for episode in episodes:
            if not episode.get("hasFile"):
                continue
            if season is not None:
                if episode.get("seasonNumber") != season:
                    continue
                number = episode.get("episodeNumber")
            elif force_absolute:
                number = episode.get("absoluteEpisodeNumber") or episode.get("episodeNumber")
            else:
                number = episode.get("absoluteEpisodeNumber") if series_type == "anime" else None
                number = number or episode.get("episodeNumber")
            if number is not None:
                downloaded.add(number)
        return downloaded
