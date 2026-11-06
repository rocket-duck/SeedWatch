# AniStar Grabber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SeedWatch отслеживает страницы сериалов с anistar.org напрямую (без Prowlarr) и отправляет новые серии (и при пустой библиотеке Sonarr — все) в qBittorrent.

**Architecture:** Новый модуль `app/anistar.py` с чистыми функциями парсера и выбора торрентов; ветвление по `source` в `Monitor.add_release`/`check_release`; новые колонки `source`/`known_torrents` в SQLite; новые методы клиентов (`QbitClient.add_torrent`, `SonarrClient.downloaded_episodes(force_absolute=)`); точечные правки WebUI.

**Tech Stack:** Python 3.11+, FastAPI, httpx, pydantic v2, SQLite, pytest (TestClient + ThreadingHTTPServer mock), ванильный JS.

**Spec:** `docs/superpowers/specs/2026-11-06-anistar-grabber-design.md` — план реализует его дословно; при расхождении спека важнее.

## Global Constraints

- Страницы anistar в кодировке **windows-1251**; декодирование bytes → str только через `decode_page` (`errors="replace"`).
- User-Agent существующего `Monitor.http` (`Mozilla/5.0 (compatible; ReleaseMonitor/1.0)`) проходит фильтр сайта — менять не нужно.
- **Без сети в тестах**: все обращения к anistar — к мок-серверу из `tests/conftest.py`; тесты ходят на `services.base_url`, суффиксы хостов подменяются через `monkeypatch`.
- Имена хостов: `ANISTAR_HOST_SUFFIXES = ("anistar.org", "astar.bz")`, сравнение `host == suffix or host.endswith("." + suffix)`, порт отбрасывается, `www.` снимается.
- Тиры озвучки: «многолос» → 3, `&` → 2, «одноголос» → 1, иначе 0 (регистронезависимо).
- Порог новизны: > 15 ранее невиданных id в проверке → `error`, если это не режим догона.
- Сообщения пользователю и истории — на русском, в духе существующих.
- Стиль кода — как в окружающих файлах (`int | None`, без `__future__`), комментарии только где неочевидно.
- Каждая задача заканчивается коммитом; весь сьют `.venv/bin/pytest -q` зелёный перед коммитом.

## Review Focus

1. **Подпись «Серии 1-67» не должна стать «серия 1»** — наивный regex даёт ложный номер; пак обязан распарситься как диапазон (тест Task 1).
2. **Дробная серия «Серия 240.5»** — не целый номер, не грабится, но учитывается в `known_torrents` (тест Task 1).
3. **Частичный сбой догона**: упавший `.torrent` остаётся не-`processed`, успешные не отправляются повторно при следующей проверке (тест Task 6).
4. **Sonarr с типом `standard`**: сравнение по `absoluteEpisodeNumber` независимо от `seriesType` (тест Task 4).
5. **Дубль серии в одной проверке при непустой библиотеке Sonarr**: скачанная — пропускается, обе копии помечаются `processed`, повторной отправки нет (тест Task 6).

---

### Task 1: Парсер AniStar (ядро)

**Files:**
- Create: `app/anistar.py`
- Create: `tests/fixtures/anistar_jade.html` (копия `/tmp/anistar_page.html`)
- Create: `tests/fixtures/anistar_douluo.html` (`/tmp/anistar_6927-boevoy-kontinent-douluo-dalu.html`)
- Create: `tests/fixtures/anistar_demon.html` (`/tmp/anistar2.html`)
- Create: `tests/fixtures/anistar_perfect.html` (`/tmp/anistar_perfect.html`)
- Test: `tests/test_anistar.py`

Фикстуры — реальные страницы (публичные, обезличенные), сохранённые в байтах cp1251.

**Interfaces:**
- Produces:
  - `ANISTAR_HOST_SUFFIXES: tuple[str, ...]` (модульная константа, читается в рантайме — тесты monkeypatch'ат)
  - `is_anistar_url(url: str) -> bool`
  - `series_id_from_url(url: str) -> str | None` — `/8883-slug.html` → `"8883"`
  - `decode_page(content: bytes) -> str`
  - `parse_series_page(html: str) -> {"title": str, "torrents": list[dict]}`; элемент torrent: `{"id": int, "episodes": list[int], "comment": str, "label": str, "size": str | None, "seeds": int | None, "date": str | None}` — `episodes` пуст для не-серийного контента, `[N]` для одиночной, `[N..M]` для пака

- [ ] **Step 1: Скопировать фикстуры**

```bash
cp /tmp/anistar_page.html tests/fixtures/anistar_jade.html
cp /tmp/anistar_6927-boevoy-kontinent-douluo-dalu.html tests/fixtures/anistar_douluo.html
cp /tmp/anistar2.html tests/fixtures/anistar_demon.html
cp /tmp/anistar_perfect.html tests/fixtures/anistar_perfect.html
```

- [ ] **Step 2: Написать failing-тесты `tests/test_anistar.py`**

Тест читает фикстуру байтами, декодирует через `decode_page`, парсит через `parse_series_page`. Пиновые значения:

```python
def test_title_from_h1_jade():  # «Убийца богов / Нефритовая династия / Zhu Xian / Jade Dynasty»
def test_single_episode_with_comment():  # id=31051: episodes == [14], comment == "Многоголосая озвучка", size == "835.81 Mb"
def test_single_episode_plain():  # id=31048: episodes == [14], comment == ""
def test_pack_range():  # id=15886 douluo: «Серии 1-67 (35.08 Gb)» → episodes == list(range(1, 68))
def test_pack_with_comment():  # id=29876 jade: «Серии 1-3 Многоголосая озвучка» → episodes == [1, 2, 3], comment == "Многоголосая озвучка"
def test_decimal_episode_is_non_series():  # id=32072 douluo: «Серия 240.5» → episodes == []
def test_specials_are_non_series():  # demon id=45017 «Спешлы 1-3» → []; 7788 «Фильмы 1-4» не входит в jade — пин на demon
def test_seeds_and_date():  # id=47069 jade: seeds == 26, date == "29-10-2026"
def test_is_anistar_url():  # anistar.org / v2.astar.bz / www.anistar.org → True; rutracker.org, anistar.org.evil.ru → False
def test_series_id_from_url():  # «https://anistar.org/8883-x.html» → «8883»; без id → None
```

- [ ] **Step 3: Запустить, убедиться что падает (ImportError)**

Run: `.venv/bin/pytest tests/test_anistar.py -q`
Expected: FAIL, `ModuleNotFoundError: app.anistar`

- [ ] **Step 4: Реализовать `app/anistar.py` (ядро)**

Подпись разбирается одним regex по трём форматам (порядок важен):
`^сери[ия]\s+(\d+)(?![\d.])\s*(?:[-–—]\s*(\d+))?\s*(.*?)\s*\(([^)]+)\)\s*$` (IGNORECASE). Логика: нет второй группы → `[N]`; есть → `[N..M]` (если `M >= N` и `M - N <= 500`, иначе `[]`); lookahead `(?![\d.])` отсекает «240.5». Не матчится → `episodes=[]`. Блоки: `re.split` по `id="torrent_(\d+)_info"`; внутри блока подпись из `<div class="info_d1">`, сиды из `class="li_distribute">\s*(\d+)`, дата из `<b>Дата:</b>\s*([\d-]+)`. Заголовок: первый `<h1>`, очищенный от тегов; fallback — `<title>` с отрезанным суффиксом «смотреть онлайн…». Русские слова «Серия/Спешлы/Фильмы» в regex дать в обоих регистрах через IGNORECASE.

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/pytest tests/test_anistar.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/anistar.py tests/test_anistar.py tests/fixtures/
git commit -m "feat: парсер страниц сериалов AniStar"
```

---

### Task 2: Выбор торрентов (тиры, дедупликация, паки)

**Files:**
- Modify: `app/anistar.py`
- Test: `tests/test_anistar.py`

**Interfaces:**
- Consumes: формат torrent из Task 1; `known` — записи из колонки `known_torrents` (`{"id", "episodes", "comment", "processed", "grabbed"}`)
- Produces:
  - `voice_tier(comment: str) -> int`
  - `select_torrents(torrents: list[dict], known: list[dict], catch_up: bool, downloaded: set[int] | None) -> {"selected": list[{"torrent": dict, "episodes": list[int]}], "processed": list[{"id", "episodes", "comment", "grabbed"}], "new_seen": int}`

Алгоритм (из спеки): кандидаты = торренты с `processed=false` + (если `catch_up`) все прочие; не-серийные → сразу в `processed` (`grabbed=False`); если `downloaded` непусто — из потребностей вычитаются скачанные серии; жадный проход по сортировке `(tier desc, одиночные раньше паков, seeds desc, date desc)`: берём, если покрывает ≥1 непокрытую нужную серию; остальные → `processed` без отправки; `new_seen` = число id вне `known`.

- [ ] **Step 1: Failing-тесты** (в `tests/test_anistar.py`)

```python
def test_voice_tiers():  # «Многоголосая озвучка»→3, «[Xelenum & Sati Akura]»→2, «Одноголосая»→1, «[Xelenum]»/""→0
def test_same_episode_prefers_multi_voice():  # дубли серии 14 jade → selected один, torrent id == 31051
def test_tie_break_by_seeds():  # два кандидата тира 0 на одну серию → побеждает больший seeds
def test_processed_ids_not_candidates():  # catch_up=False, всё processed → selected пуст, new_seen == 0
def test_catch_up_selects_all():  # catch_up=True → selected покрывает все серии страницы (jade: по одному на серию)
def test_downloaded_subtracted_and_pack_covers_rest():  # downloaded={1..60}, кандидаты: пак 1-67 + одиночные 61,62 → selected: 61,62 одиночными; пак не берётся (покрывает 63-67? нет — см. ниже)
```

Уточнение последнего теста (синтетические торренты, не фикстура): пак `[1..67]` при `downloaded={1..60}`: после вычитания нужные = 61..67 (7 серий); одиночные на 61 и 62 покрывают своё; пак покрывает 63–67 (≥1) → пак БЕРЁТСЯ, итог selected = {61, 62, пак}. Отдельный кейс: пак `[1..61]`, `downloaded={1..60}`, одиночная 61 → пак покрывает ничего не оставшееся → не берётся, одиночная берётся.

- [ ] **Step 2: Запустить — FAIL** (`voice_tier` not defined)

- [ ] **Step 3: Реализовать `voice_tier` и `select_torrents` в `app/anistar.py`**

- [ ] **Step 4: Запустить — PASS**

Run: `.venv/bin/pytest tests/test_anistar.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/anistar.py tests/test_anistar.py
git commit -m "feat: выбор торрентов AniStar (тиры озвучки, дедупликация, паки)"
```

---

### Task 3: Схема данных (`source`, `known_torrents`)

**Files:**
- Modify: `app/store.py` (SCHEMA, `init`, `_release`, `create_release`, `update_release`)
- Test: `tests/test_anistar.py` (класс хранения)

**Interfaces:**
- Produces: `Store` прозрачно возвращает `source: str` и `known_torrents: list[dict]` из `_release`; `create_release`/`update_release` принимают оба поля; дефолты `source='prowlarr'`, `known_torrents=[]`.

- [ ] **Step 1: Failing-тест**

```python
def test_store_anistar_roundtrip(tmp_path):  # Store(tmp_path/"t.db"); init; create_release({... source="anistar", known_torrents=[{"id": 1, "episodes": [5], "comment": "", "processed": True, "grabbed": True}], ...обязательные поля как в monitor.add_release...}); get_release → поля на месте; update_release(known_torrents=[...]) → сменилось
def test_store_defaults(tmp_path):  # create_release без source/known_torrents → source == "prowlarr", known_torrents == []
```

- [ ] **Step 2: Запустить — FAIL** (поля отсутствуют / отброшены `update_release`)

- [ ] **Step 3: Реализовать в `app/store.py`**

SCHEMA: колонки `source TEXT NOT NULL DEFAULT 'prowlarr'` (проверить написание дефолта — с двумя «r») и `known_torrents TEXT NOT NULL DEFAULT '[]'`. В `init` — миграции через `ALTER TABLE` по образцу существующих `sonarr_*`. `_release`: `json.loads` для `known_torrents`. `update_release`: добавить `"source"`, `"known_torrents"` в `allowed`, JSON-сериализация как у `episodes`. `create_release`: добавить колонки в INSERT.

- [ ] **Step 4: Запустить — PASS** (весь сьют: `.venv/bin/pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_anistar.py
git commit -m "feat: колонки source и known_torrents в хранилище"
```

---

### Task 4: `SonarrClient.downloaded_episodes(force_absolute=)`

**Files:**
- Modify: `app/sonarr.py:79-99`
- Modify: `tests/conftest.py` (маршрут `/api/v3/episode` поддерживает `mock.custom_episodes`)
- Test: `tests/test_anistar.py`

**Interfaces:**
- Produces: `downloaded_episodes(series_id: int, series_type: str, season: int | None = None, force_absolute: bool = False) -> set[int]`; при `force_absolute=True` номер = `absoluteEpisodeNumber or episodeNumber` независимо от `series_type`.

- [ ] **Step 1: Расширить conftest**: `MockServices.custom_episodes: dict[int, list[dict]] = {}`; в обработчике `/api/v3/episode` — `if series_id in mock.custom_episodes: return self.send_json(mock.custom_episodes[series_id])` до существующих веток.

- [ ] **Step 2: Failing-тест**

```python
@pytest.mark.asyncio  # если в проекте нет asyncio-режима — вызывать через asyncio.run внутри теста (как сделано в test_qbit.py — свериться)
async/services-стиль: services-фикстура, клиент = SonarrClient(services.base_url, "sonarr_secret", 5)
mock.custom_episodes[60] = [{"seasonNumber": 2, "episodeNumber": 5, "absoluteEpisodeNumber": 91, "hasFile": True}]
def test_force_absolute_uses_absolute_for_standard():  # series_type="standard": force_absolute=True → {91}; force_absolute=False → {5}
```

(Проверить в `tests/test_qbit.py` стиль вызова асинхронных клиентов и повторить его.)

- [ ] **Step 3: Запустить — FAIL**

- [ ] **Step 4: Реализовать**: при `force_absolute=True` и `season is None`: `number = episode.get("absoluteEpisodeNumber") or episode.get("episodeNumber")` вне зависимости от `series_type`.

- [ ] **Step 5: Запустить — PASS** (весь сьют)

- [ ] **Step 6: Commit**

```bash
git add app/sonarr.py tests/conftest.py tests/test_anistar.py
git commit -m "feat: force_absolute в downloaded_episodes Sonarr"
```

---

### Task 5: `QbitClient.add_torrent` + мок-маршруты AniStar

**Files:**
- Modify: `app/qbit.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_anistar.py`

**Interfaces:**
- Produces:
  - `QbitClient.add_torrent(content: bytes, filename: str, category: str | None = None) -> None`
  - В `MockServices`: `anistar_pages: dict[str, bytes]` (полный URL → страница), `anistar_torrents: dict[int, bytes]` (id → .torrent), `added_torrents: list[bytes]` (сырые multipart-тела)
  - Мок-маршруты: GET `/<digits>-*.html` → страница (`text/html; charset=windows-1251`); GET `/engine/gettorrent.php?id=N` → torrent (`application/x-bittorrent`); POST `/api/v2/torrents/add` → запись тела в `mock.added_torrents`

- [ ] **Step 1: Расширить conftest** по спецификации выше (путь `/<digits>-*.html` — regex по `parsed.path`).

- [ ] **Step 2: Failing-тест**

```python
def test_add_torrent_posts_multipart(services):  # QbitClient(base_url=services.base_url, ..., auth_method="api_key", api_key=services.api_key); await add_torrent(b"d8:announce9:test", "anistar_1.torrent", "tv-sonarr"); тело в services.added_torrents[-1] содержит b"d8:announce9:test" и b"tv-sonarr"
```

- [ ] **Step 3: Запустить — FAIL**

- [ ] **Step 4: Реализовать `add_torrent`**: `POST /torrents/add`, `files={"torrents": (filename, content)}`, `data={"category": category}` если задана; `_login` перед запросом; `_check`.

- [ ] **Step 5: Запустить — PASS**

- [ ] **Step 6: Commit**

```bash
git add app/qbit.py tests/conftest.py tests/test_anistar.py
git commit -m "feat: добавление торрента в qBittorrent по байтам файла"
```

---

### Task 6: Интеграция Monitor (add/check/граб)

**Files:**
- Modify: `app/monitor.py` (ветки в `add_release`, `check_release`; хелперы)
- Test: `tests/test_anistar_api.py`

**Interfaces:**
- Consumes: всё из Tasks 1–5.
- Produces (приватные методы `Monitor`):
  - `_fetch_anistar_html(url: str) -> str` — FlareSolverr если настроен, иначе `self.http`; `decode_page`
  - `_add_anistar_release(url, interval_minutes, sonarr_series_id) -> dict`
  - `_check_anistar_release(release: dict, initial: bool) -> dict | None`
  - `_grab_anistar_torrents(release_id: int, selected: list[dict], category: str | None) -> tuple[list[int], list[str]]` (grabbed ids, failure messages)
- Диспетчеризация: в начале `add_release` — `if is_anistar_url(url): return await self._add_anistar_release(...)`; в начале `check_release` после чтения `current` — `if current["source"] == "anistar": return await self._check_anistar_release(current, initial)`.

Поток (`_add_anistar_release`): fetch → parse → (если задан sonarr) `downloaded = downloaded_episodes(..., force_absolute=True)`; `catch_up = bool(sonarr_series_id) and not downloaded`; создать запись: `known_torrents` = все id с `processed=not catch_up` (догон → все `processed=False`, иначе baseline все `processed=True`), `episodes` = все распознанные номера, `title` = заголовок страницы; если `catch_up` — сразу селекция (`downloaded` пусто) и `_grab_anistar_torrents`; иначе все id → `processed=True`. Пустая страница (0 торрентов) — валидна: запись с пустым `known_torrents`.

Поток (`_check_anistar_release`): parse (0 торрентов → `MonitorError("Страница разобрана, но торренты не найдены — возможно, изменилась вёрстка")`); sonarr → `downloaded`, `catch_up`; селекция через `select_torrents`; если `new_seen > 15 and not catch_up` → `MonitorError`; нет selected → статус `ok`; есть selected и qbit не настроен → `MonitorError("QBIT_URL не задан")`; категория `sonarr.download_category()` (неблокирующе); `_grab_anistar_torrents`: для каждого — `GET {scheme}://{host}/engine/gettorrent.php?id=...` (хост из `release["url"]`), проверка префикса `b"d8:announce"`, `add_torrent`; обновление `known_torrents`/`episodes`/статуса/истории. История: по каждому отправленному — «Серия N: отправлен торрент <id> (<многоголосая озвучка|одноголосая|без комментария, сиды X>)»; по пропущенным скачанным — «Серия N уже скачана в Sonarr»; при неудаче — сообщение об ошибке, id остаётся не-`processed`. Статус: `grabbed`, если ≥1 отправлен; `error`, если ни один.

- [ ] **Step 1: Тест-скелет с monkeypatch хостов**

```python
@pytest.fixture
def anistar(services, monkeypatch):
    monkeypatch.setattr(anistar_module, "ANISTAR_HOST_SUFFIXES", ("127.0.0.1",))
    services.anistar_pages[f"{services.base_url}/8883-jade.html"] = (FIXTURES / "anistar_jade.html").read_bytes()
    return services
```

(`anistar_module` — импорт `app.anistar as anistar_module`; URL хоста мока совпадает с патченным суффиксом.)

- [ ] **Step 2: Failing-тесты (по одному, за ними реализация)**

```python
def test_add_baseline_no_sonar():  # POST /api/releases {url: jade, interval_days: 1} → 201; запись source == "anistar"; все id processed=True; services.added_torrents пуст; episodes непуст
def test_add_catch_up_empty_sonarr():  # sonarr_series_id = 40 (в conftest series[40] = {"id": 40, "title": "Fresh", "seriesType": "anime"}, downloaded пуст) → добавлено; services.added_torrents непуст; число добавленных == числу selected из select_torrents(parse(jade), [], catch_up=True, downloaded=None); серия 14 представлена торрентом 31051 (многоголосая)
def test_add_baseline_partial_sonarr():  # sonarr_series_id = 10 (скачано 1..173) → added_torrents пуст; все processed=True
def test_check_sends_new_episode():  # после baseline в anistar_pages появляется id 99999 «Серия 92 (800 Mb)» → POST /check → added_torrents содержит b"id=99999"-тело; история has "Серия 92"
def test_check_skips_sonarr_downloaded():  # серия 92 помечена скачанной в Sonarr (custom_episodes[50] с absoluteEpisodeNumber=92, hasFile=True; series[50] добавить в conftest) → добавления нет; история «уже скачана»
def test_check_threshold():  # страница заменена: 20 новых id → статус error, «подозрительно много»; added_torrents не вырос
def test_check_zero_torrents():  # страница заменена на пустую (без блоков) → статус error
def test_check_torrent_download_failure():  # anistar_torrents без нужного id → мок отдаёт 404/пусто → id не processed; статус error; повторный /check → повторная попытка (число GET gettorrent к id выросло)
def test_check_requires_qbit():  # qbit_url сброшен через PUT /api/settings {qbit_url: "http://"} → /check с новой серией → error про настройку qBittorrent
```

Для подсчёта GET gettorrent: `services.anistar_torrents` / список запросов — добавить `mock.gettorrent_requests: list[int]` в conftest (Task 5) либо считать по `added_torrents`. Выбрать проще: `mock.gettorrent_requests`.

- [ ] **Step 3: Запустить новые тесты — FAIL**

- [ ] **Step 4: Реализовать диспетчеризацию и методы в `app/monitor.py`** по спецификации выше. Существующий prowlarr-поток не трогать.

- [ ] **Step 5: Запустить — PASS** (весь сьют)

- [ ] **Step 6: Commit**

```bash
git add app/monitor.py tests/conftest.py tests/test_anistar_api.py
git commit -m "feat: мониторинг страниц AniStar (добавление, проверка, отправка в qbit)"
```

---

### Task 7: WebUI

**Files:**
- Modify: `app/static/index.html` (id на label'ы полей поискового запроса: `search-query-field` в add-форме, `edit-search-query-field` в edit-диалоге)
- Modify: `app/static/app.js`
- Verify: вручную

- [ ] **Step 1: `index.html`** — добавить `id="search-query-field"` и `id="edit-search-query-field"` на соответствующие `<label class="field">`.

- [ ] **Step 2: `app.js`**
  - `function isAnistarUrl(url)`: hostname матчит `/(^|\.)anistar\.org$/` или `/(^|\.)astar\.bz$/`.
  - На `input` у `form.elements.url`: скрывать/показывать `#search-query-field`.
  - В submit-обработчике: текст `formMessage` «Добавляем сериал с AniStar…» для anistar-URL, иначе текущий.
  - `renderRelease`: если `release.source === 'anistar'` — значок (`element('span', 'badge source', 'AniStar')`) рядом со статусом.
  - `openEditDialog`: скрывать/показывать `#edit-search-query-field` по `release.source`.

- [ ] **Step 3: Проверка**: `.venv/bin/pytest -q` зелёный; ручной чек-лист: поле скрывается при вводе anistar-ссылки, возвращается при rutracker; значок виден на карточке anistar-раздачи.

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: UI поддержки источника AniStar"
```

---

### Task 8: README и финальная проверка

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Обновить README**: в «Возможности» — поддержка AniStar (страницы сериалов напрямую, без Prowlarr; режим догона при пустой библиотеке Sonarr; выбор озвучки по приоритету многоголосая > несколько дикторов > одноголосая); в «Как это работает» — ветку AniStar; в «Добавление раздачи» — что ссылка на страницу сериала AniStar распознаётся автоматически, поисковый запрос не нужен.

- [ ] **Step 2: `.venv/bin/pytest -q` — весь сьют зелёный.**

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: AniStar в README"
```

---

## Self-Review

**Spec coverage:** распознавание по хосту (T1), парсер трёх форматов + дробные (T1), тиры и приоритеты (T2), схема `source`/`known_torrents` (T3), `force_absolute` (T4), отправка в qbit (T5), догон/baseline/проверка/порог/ошибки (T6), UI (T7), README (T8). Покрыто всё; FlareSolverr-фолбэк заимствован из существующего `fetch_topic_title` через общий хелпер — в T6.

**Step scan:** тесты пинуют конкретные значения из спеки/фикстур; реализация — сигнатуры + один неочевидный алгоритм (жадный выбор), который спека не определяет полностью.

**Type consistency:** `select_torrents` используется в T2-тестах и T6 с одной сигнатурой; `known_torrents`-записи одной формы в T2/T3/T6; `add_torrent(content, filename, category)` — T5 производит, T6 потребляет.

**Review Focus:** пять пунктов распределены по T1 (1, 2), T4 (4), T6 (3, 5) — тесты добавлены в соответствующие задачи.

**Proportion:** план ≈ спека по объёму; тела кода только там, где спека оставляет выбор (regex подписи, жадный алгоритм).
