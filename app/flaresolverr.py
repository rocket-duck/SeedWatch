import httpx
from urllib.parse import urlencode


class FlareSolverrError(RuntimeError):
    pass


class FlareSolverrClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def _command(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(f"{self.base_url}/v1", json=payload)
            except httpx.HTTPError as exc:
                raise FlareSolverrError(f"FlareSolverr недоступен: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise FlareSolverrError(f"FlareSolverr вернул не-JSON (HTTP {response.status_code})") from exc
        if data.get("status") != "ok":
            raise FlareSolverrError(f"FlareSolverr: {data.get('message') or 'неизвестная ошибка'}")
        return data

    async def _session(self) -> str:
        if not self.session:
            data = await self._command({"cmd": "sessions.create"})
            self.session = data["solution"]["session"]
        return self.session

    async def _run(self, payload: dict) -> dict:
        payload["session"] = await self._session()
        try:
            return await self._command(payload)
        except FlareSolverrError as exc:
            if "session" not in str(exc).lower() or not self.session:
                raise
            self.session = None
            payload["session"] = await self._session()
            return await self._command(payload)

    async def get(self, url: str) -> str:
        data = await self._run({"cmd": "request.get", "url": url, "maxTimeout": 90000})
        return data["solution"]["response"]

    async def post(self, url: str, fields: dict[str, str]) -> str:
        data = await self._run(
            {
                "cmd": "request.post",
                "url": url,
                "postData": urlencode(fields),
                "maxTimeout": 90000,
            }
        )
        return data["solution"]["response"]
