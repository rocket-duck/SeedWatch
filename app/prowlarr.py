import httpx


class ProwlarrError(RuntimeError):
    pass


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float, download_client_id: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.download_client_id = download_client_id

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1",
            headers={"X-Api-Key": self.api_key},
            timeout=self.timeout,
        )

    @staticmethod
    async def _response(response: httpx.Response) -> httpx.Response:
        if response.is_error:
            detail = response.text.strip()[:500]
            raise ProwlarrError(f"Prowlarr вернул HTTP {response.status_code}: {detail}")
        return response

    async def search(self, query: str) -> list[dict]:
        if not self.api_key:
            raise ProwlarrError("Не задан PROWLARR_API_KEY")
        try:
            async with self._client() as client:
                response = await client.get(
                    "/search",
                    params={"query": query, "type": "search", "limit": 100},
                )
                await self._response(response)
                return response.json()
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr недоступен: {exc}") from exc

    async def grab(self, release: dict) -> None:
        payload = {"guid": release["guid"], "indexerId": release["indexerId"]}
        if self.download_client_id is not None:
            payload["downloadClientId"] = self.download_client_id
        try:
            async with self._client() as client:
                response = await client.post("/search", json=payload)
                await self._response(response)
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr недоступен: {exc}") from exc

    async def status(self) -> dict:
        try:
            async with self._client() as client:
                response = await client.get("/system/status")
                await self._response(response)
                return response.json()
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr недоступен: {exc}") from exc
