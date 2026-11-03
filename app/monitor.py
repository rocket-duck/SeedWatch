import asyncio
import re
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from .config import Settings
from .flaresolverr import FlareSolverrClient, FlareSolverrError
from .prowlarr import ProwlarrClient, ProwlarrError
from .qbit import QbitClient, QbitError
from .releases import find_matching_release, parse_episodes, parse_season, release_identity
from .sonarr import SonarrClient, SonarrError
from .store import Store


class MonitorError(RuntimeError):
    pass


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self.parts).strip()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def compact_episodes(episodes: list[int]) -> str:
    if not episodes:
        return ""
    if len(episodes) == 1:
        return str(episodes[0])
    if len(episodes) <= 12:
        return ", ".join(map(str, episodes))
    return f"{episodes[0]}–{episodes[-1]} ({len(episodes)} серий)"


class Monitor:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.prowlarr = ProwlarrClient(
            settings.prowlarr_url,
            settings.prowlarr_api_key,
            settings.request_timeout,
            settings.prowlarr_download_client_id,
        )
        self.sonarr = SonarrClient(settings.sonarr_url, settings.sonarr_api_key, settings.request_timeout)
        self.qbit = QbitClient(
            settings.qbit_url,
            settings.qbit_username,
            settings.qbit_password,
            settings.request_timeout,
            settings.qbit_metadata_timeout,
            settings.qbit_auth_method,
            settings.qbit_api_key,
        )
        self.flaresolverr = FlareSolverrClient(settings.flaresolverr_url)
        self.tracker_credentials = {
            "rutracker": (settings.rutracker_username, settings.rutracker_password),
            "kinozal": (settings.kinozal_username, settings.kinozal_password),
        }
        self.http: httpx.AsyncClient | None = None
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.locks = defaultdict(asyncio.Lock)

    def configure(self, values: dict[str, str]) -> None:
        self.prowlarr.base_url = values.get("prowlarr_url", self.prowlarr.base_url).rstrip("/")
        self.prowlarr.api_key = values.get("prowlarr_api_key", self.prowlarr.api_key)
        self.sonarr.base_url = values.get("sonarr_url", self.sonarr.base_url).rstrip("/")
        self.sonarr.api_key = values.get("sonarr_api_key", self.sonarr.api_key)
        self.qbit.base_url = values.get("qbit_url", self.qbit.base_url).rstrip("/")
        self.qbit.auth_method = values.get("qbit_auth_method", self.qbit.auth_method)
        self.qbit.username = values.get("qbit_username", self.qbit.username)
        self.qbit.password = values.get("qbit_password", self.qbit.password)
        self.qbit.api_key = values.get("qbit_api_key", self.qbit.api_key)
        flaresolverr_url = values.get("flaresolverr_url", self.flaresolverr.base_url).rstrip("/")
        if flaresolverr_url != self.flaresolverr.base_url:
            self.flaresolverr.base_url = flaresolverr_url
            self.flaresolverr.session = None
        for tracker in ("rutracker", "kinozal"):
            username, password = self.tracker_credentials[tracker]
            self.tracker_credentials[tracker] = (
                values.get(f"{tracker}_username", username),
                values.get(f"{tracker}_password", password),
            )

    def public_settings(self) -> dict:
        return {
            "prowlarr_url": self.prowlarr.base_url,
            "prowlarr_api_key_set": bool(self.prowlarr.api_key),
            "sonarr_url": self.sonarr.base_url,
            "sonarr_api_key_set": bool(self.sonarr.api_key),
            "qbit_url": self.qbit.base_url,
            "qbit_auth_method": self.qbit.auth_method,
            "qbit_username": self.qbit.username,
            "qbit_password_set": bool(self.qbit.password),
            "qbit_api_key_set": bool(self.qbit.api_key),
            "flaresolverr_url": self.flaresolverr.base_url,
            "rutracker_username": self.tracker_credentials["rutracker"][0],
            "rutracker_password_set": bool(self.tracker_credentials["rutracker"][1]),
            "kinozal_username": self.tracker_credentials["kinozal"][0],
            "kinozal_password_set": bool(self.tracker_credentials["kinozal"][1]),
        }

    async def start(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=self.settings.request_timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ReleaseMonitor/1.0)"},
        )
        self.task = asyncio.create_task(self._run(), name="release-monitor")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.http:
            await self.http.aclose()

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                due = self.store.due_releases(timestamp())
                if due:
                    await asyncio.gather(
                        *(self.check_release(release["id"]) for release in due),
                        return_exceptions=True,
                    )
            except Exception:
                pass
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=10)

    async def fetch_topic_title(self, url: str) -> str:
        if self.flaresolverr.configured:
            html = await self._fetch_via_flaresolverr(url)
        else:
            try:
                response = await self.http.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise MonitorError(
                    f"Не удалось прочитать страницу раздачи: {exc}. Укажите поисковый запрос вручную."
                ) from exc
            html = response.text
        title = self._extract_title(html)
        if not title:
            raise MonitorError("На странице раздачи не найден заголовок. Укажите поисковый запрос вручную.")
        return title

    @staticmethod
    def _extract_title(html: str) -> str | None:
        parser = TitleParser()
        parser.feed(html)
        title = re.sub(r"\s*::\s*RuTracker\.org.*$", "", parser.title, flags=re.IGNORECASE).strip()
        if not title or "just a moment" in title.lower():
            return None
        return title

    @staticmethod
    def _tracker_for(url: str) -> str | None:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if "kinozal" in host:
            return "kinozal"
        if "rutracker" in host:
            return "rutracker"
        return None

    async def _fetch_via_flaresolverr(self, url: str) -> str:
        tracker = self._tracker_for(url)
        try:
            html = await self.flaresolverr.get(url)
        except FlareSolverrError as exc:
            raise MonitorError(f"Не удалось прочитать страницу раздачи через FlareSolverr: {exc}") from exc
        if tracker and self._is_login_page(tracker, html):
            username, password = self.tracker_credentials.get(tracker, ("", ""))
            if not username or not password:
                raise MonitorError(f"Трекер {tracker} требует входа. Задайте логин и пароль в настройках.")
            await self._login_tracker(tracker, url, username, password)
            try:
                html = await self.flaresolverr.get(url)
            except FlareSolverrError as exc:
                raise MonitorError(f"Не удалось прочитать страницу раздачи через FlareSolverr: {exc}") from exc
            if self._is_login_page(tracker, html):
                raise MonitorError(f"Трекер {tracker} не принял логин/пароль. Проверьте учётные данные в настройках.")
        return html

    @staticmethod
    def _is_login_page(tracker: str, html: str) -> bool:
        if tracker == "kinozal":
            return "takelogin.php" in html
        return 'name="login_username"' in html or "name='login_username'" in html

    async def _login_tracker(self, tracker: str, url: str, username: str, password: str) -> None:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        try:
            if tracker == "kinozal":
                login_html = await self.flaresolverr.get(f"{base}/login.php")
                fields = self._hidden_fields(login_html, "takelogin.php")
                fields["username"] = username
                fields["password"] = password
                await self.flaresolverr.post(f"{base}/takelogin.php", fields)
            else:
                await self.flaresolverr.post(
                    f"{base}/forum/login.php",
                    {
                        "redirect": "/forum/index.php",
                        "login_username": username,
                        "login_password": password,
                        "login": "Вход",
                    },
                )
        except FlareSolverrError as exc:
            raise MonitorError(f"Не удалось войти на трекер {tracker}: {exc}") from exc

    @staticmethod
    def _hidden_fields(html: str, form_marker: str) -> dict[str, str]:
        match = re.search(
            rf"<form[^>]*{re.escape(form_marker)}[^>]*>(.*?)</form>", html, re.IGNORECASE | re.DOTALL
        )
        if not match:
            raise MonitorError(f"На странице входа не найдена форма {form_marker}")
        fields = {}
        for tag in re.findall(r"<input[^>]*>", match.group(1), re.IGNORECASE):
            if 'type="hidden"' not in tag.lower():
                continue
            name = re.search(r"name=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
            value = re.search(r"value=[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
            if name:
                fields[name.group(1)] = value.group(1) if value else ""
        return fields

    async def add_release(
        self,
        url: str,
        interval_minutes: int,
        search_query: str | None,
        sonarr_series_id: int | None,
    ) -> dict:
        identity = release_identity(url)
        if not identity:
            raise MonitorError("Нужна корректная HTTP(S)-ссылка на страницу раздачи")
        if self.store.get_by_url(url):
            raise MonitorError("Эта раздача уже добавлена")

        series = await self.sonarr.get_series(sonarr_series_id) if sonarr_series_id else None
        topic_title = None
        query = search_query
        if not query:
            try:
                topic_title = await self.fetch_topic_title(url)
                query = topic_title
            except MonitorError:
                if not series:
                    raise
                query = series["title"]
                search_query = query

        results = await self.prowlarr.search(query)
        release = find_matching_release(results, identity)
        if not release:
            raise MonitorError(
                "Prowlarr не нашёл указанную раздачу. Добавьте короткий поисковый запрос "
                "и проверьте, что её индексер включён в Prowlarr."
            )

        title = (release.get("title") or "").strip()
        if not title:
            raise MonitorError("Prowlarr вернул раздачу без названия")
        now = utc_now()
        return self.store.create_release(
            {
                "url": url,
                "topic_id": identity,
                "search_query": search_query,
                "topic_title": topic_title,
                "title": title,
                "episodes": parse_episodes(title),
                "interval_minutes": interval_minutes,
                "enabled": True,
                "last_status": "monitoring",
                "last_error": None,
                "last_checked_at": timestamp(now),
                "last_grabbed_at": None,
                "next_check_at": timestamp(now + timedelta(minutes=interval_minutes)),
                "created_at": timestamp(now),
                "updated_at": timestamp(now),
                "sonarr_series_id": series["id"] if series else None,
                "sonarr_series_title": series["title"] if series else None,
                "sonarr_series_type": series.get("seriesType", "standard") if series else None,
            }
        )

    async def check_release(self, release_id: int, initial: bool = False) -> dict | None:
        async with self.locks[release_id]:
            current = self.store.get_release(release_id)
            if not current:
                return None
            next_check = timestamp(utc_now() + timedelta(minutes=current["interval_minutes"]))
            self.store.update_release(release_id, next_check_at=next_check, updated_at=timestamp())
            try:
                query = current["search_query"]
                topic_title = None
                if not query:
                    try:
                        topic_title = await self.fetch_topic_title(current["url"])
                        query = topic_title
                    except MonitorError:
                        if not current["sonarr_series_title"]:
                            raise
                        query = current["sonarr_series_title"]
                        self.store.update_release(release_id, search_query=query)
                if topic_title is None:
                    try:
                        topic_title = await self.fetch_topic_title(current["url"])
                    except MonitorError:
                        topic_title = None
                results = await self.prowlarr.search(query)
                release = find_matching_release(results, current["topic_id"])
                if not release:
                    raise MonitorError("Раздача не найдена в результатах Prowlarr")
                indexer_title = (release.get("title") or "").strip()
                if not indexer_title:
                    raise MonitorError("Prowlarr вернул раздачу без названия")
                title = indexer_title
                if topic_title and parse_episodes(topic_title):
                    title = topic_title

                now = timestamp()
                if title == current["title"] and not initial:
                    return self.store.update_release(
                        release_id,
                        topic_title=topic_title or current["topic_title"],
                        last_status="ok",
                        last_error=None,
                        last_checked_at=now,
                        updated_at=now,
                    )

                old_episodes = set(current["episodes"])
                new_episodes_list = parse_episodes(title)
                previous_episodes = set() if initial else old_episodes
                added = sorted(set(new_episodes_list) - previous_episodes)
                if added:
                    missing = added
                    if current["sonarr_series_id"]:
                        downloaded = await self.sonarr.downloaded_episodes(
                            current["sonarr_series_id"],
                            current["sonarr_series_type"],
                            parse_season(title),
                        )
                        missing = sorted(set(new_episodes_list) - downloaded)
                    if not missing:
                        message = "Раздача обновилась, но все её серии уже скачаны в Sonarr"
                        self.store.update_release(
                            release_id,
                            title=title,
                            topic_title=topic_title or current["topic_title"],
                            episodes=new_episodes_list,
                            last_status="changed",
                            last_error=None,
                            last_checked_at=now,
                            updated_at=now,
                        )
                        self.store.add_history(release_id, now, "changed", title, message)
                        return self.store.get_release(release_id)

                    previous_hashes = None
                    delta_error = None
                    category = None
                    if current["sonarr_series_id"] and self.qbit.configured:
                        try:
                            previous_hashes = await self.qbit.snapshot()
                            category = await self.sonarr.download_category()
                        except (QbitError, SonarrError) as exc:
                            delta_error = str(exc)
                    await self.prowlarr.grab(release)
                    message = f"Недостающие серии {compact_episodes(missing)} отправлены в Prowlarr"
                    if previous_hashes is not None:
                        try:
                            delta_message = await self.qbit.apply_delta(previous_hashes, release, missing, category)
                        except QbitError as exc:
                            delta_message = f"{exc}; торрент оставлен полным"
                        message = f"{message}. {delta_message}"
                    elif current["sonarr_series_id"]:
                        reason = delta_error or "QBIT_URL не задан"
                        message = f"{message}. {reason}; торрент отправлен полностью"
                    self.store.update_release(
                        release_id,
                        title=title,
                        topic_title=topic_title or current["topic_title"],
                        episodes=new_episodes_list,
                        last_status="grabbed",
                        last_error=None,
                        last_checked_at=now,
                        last_grabbed_at=now,
                        updated_at=now,
                    )
                    self.store.add_history(release_id, now, "grabbed", title, message)
                else:
                    warning = None
                    if old_episodes and not new_episodes_list:
                        warning = "Название изменилось, но серии в новом названии не распознаны"
                    message = warning or "Название изменилось, новых серий нет"
                    self.store.update_release(
                        release_id,
                        title=title,
                        topic_title=topic_title or current["topic_title"],
                        episodes=new_episodes_list,
                        last_status="changed",
                        last_error=warning,
                        last_checked_at=now,
                        updated_at=now,
                    )
                    self.store.add_history(release_id, now, "changed", title, message)
                return self.store.get_release(release_id)
            except Exception as exc:
                now = timestamp()
                message = str(exc)
                self.store.update_release(
                    release_id,
                    last_status="error",
                    last_error=message,
                    last_checked_at=now,
                    updated_at=now,
                )
                if current["last_status"] != "error" or current["last_error"] != message:
                    self.store.add_history(release_id, now, "error", current["title"], message)
                return self.store.get_release(release_id)
