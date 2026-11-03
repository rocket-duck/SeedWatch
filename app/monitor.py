import asyncio
import re
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx

from .config import Settings
from .prowlarr import ProwlarrClient, ProwlarrError
from .releases import extract_topic_id, find_topic_release, parse_episodes
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
        self.http: httpx.AsyncClient | None = None
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.locks = defaultdict(asyncio.Lock)

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
        try:
            response = await self.http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MonitorError(
                f"Не удалось прочитать страницу раздачи: {exc}. Укажите поисковый запрос вручную."
            ) from exc
        parser = TitleParser()
        parser.feed(response.text)
        title = re.sub(r"\s*::\s*RuTracker\.org.*$", "", parser.title, flags=re.IGNORECASE).strip()
        if not title:
            raise MonitorError("На странице раздачи не найден заголовок. Укажите поисковый запрос вручную.")
        return title

    async def add_release(self, url: str, interval_minutes: int, search_query: str | None) -> dict:
        topic_id = extract_topic_id(url)
        if not topic_id:
            raise MonitorError("Нужна ссылка вида https://rutracker.org/forum/viewtopic.php?t=123456")
        if self.store.get_by_url(url):
            raise MonitorError("Эта раздача уже добавлена")

        topic_title = None
        query = search_query
        if not query:
            topic_title = await self.fetch_topic_title(url)
            query = topic_title

        results = await self.prowlarr.search(query)
        release = find_topic_release(results, topic_id)
        if not release:
            raise MonitorError(
                "Prowlarr не нашёл указанную тему. Добавьте короткий поисковый запрос, "
                "например Douluo Dalu II, и проверьте индексер RuTracker."
            )

        title = (release.get("title") or "").strip()
        if not title:
            raise MonitorError("Prowlarr вернул раздачу без названия")
        now = utc_now()
        return self.store.create_release(
            {
                "url": url,
                "topic_id": topic_id,
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
            }
        )

    async def check_release(self, release_id: int) -> dict | None:
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
                    topic_title = await self.fetch_topic_title(current["url"])
                    query = topic_title
                results = await self.prowlarr.search(query)
                release = find_topic_release(results, current["topic_id"])
                if not release:
                    raise MonitorError("Раздача не найдена в результатах Prowlarr")
                title = (release.get("title") or "").strip()
                if not title:
                    raise MonitorError("Prowlarr вернул раздачу без названия")

                now = timestamp()
                if title == current["title"]:
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
                added = sorted(set(new_episodes_list) - old_episodes)
                if added:
                    await self.prowlarr.grab(release)
                    message = f"Новые серии {compact_episodes(added)} отправлены в Prowlarr"
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
