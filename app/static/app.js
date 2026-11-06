const releasesNode = document.querySelector('#releases');
const emptyNode = document.querySelector('#empty');
const form = document.querySelector('#add-form');
const formMessage = document.querySelector('#form-message');
const historyDialog = document.querySelector('#history-dialog');
const historyContent = document.querySelector('#history-content');
const sonarrSeriesSelect = document.querySelector('#sonarr-series');
const settingsDialog = document.querySelector('#settings-dialog');
const settingsForm = document.querySelector('#settings-form');
const settingsMessage = document.querySelector('#settings-message');
const editDialog = document.querySelector('#edit-dialog');
const editForm = document.querySelector('#edit-form');
const editMessage = document.querySelector('#edit-message');
const editSeriesSelect = document.querySelector('#edit-sonarr-series');

let releasesCache = [];

const statusNames = {
  monitoring: 'Отслеживается',
  ok: 'Без изменений',
  grabbed: 'Отправлено',
  changed: 'Без новых серий',
  error: 'Ошибка',
};

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout ?? 15000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(options.timeout
        ? 'Превышено время ожидания ответа. Проверьте список раздач — операция могла завершиться на сервере.'
        : 'Превышено время ожидания ответа');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function formatDate(value) {
  if (!value) return 'Ещё не было';
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'short', timeStyle: 'short',
  }).format(new Date(value));
}

function formatEpisodes(episodes) {
  if (!episodes.length) return 'Не распознаны';
  if (episodes.length === 1) return `Серия ${episodes[0]}`;
  return `${episodes[0]}–${episodes.at(-1)} · ${episodes.length} шт.`;
}

function formatInterval(minutes) {
  if (minutes % 1440 === 0) return `${minutes / 1440} дн.`;
  return `${minutes} мин.`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function metaItem(label, value) {
  const wrapper = element('div');
  wrapper.append(
    element('span', 'meta-label', label),
    element('div', 'meta-value', value),
  );
  return wrapper;
}

function actionButton(label, action, id, style = 'secondary') {
  const button = element('button', style, label);
  button.type = 'button';
  button.dataset.action = action;
  button.dataset.id = id;
  return button;
}

function isAnistarUrl(url) {
  try {
    const host = new URL(url).hostname;
    return /(^|\.)anistar\.org$/.test(host) || /(^|\.)astar\.bz$/.test(host);
  } catch {
    return false;
  }
}

function renderRelease(release) {
  const card = element('article', `release${release.enabled ? '' : ' paused'}`);
  const header = element('div', 'release-header');
  const title = element('a', 'release-title', release.title);
  title.href = release.url;
  title.target = '_blank';
  title.rel = 'noreferrer';
  const status = release.enabled ? release.last_status : 'paused';
  header.append(title, element('span', `badge ${status}`, release.enabled ? (statusNames[status] || status) : 'На паузе'));
  if (release.source === 'anistar') {
    header.append(element('span', 'badge source', 'AniStar'));
  }

  const meta = element('div', 'release-meta');
  meta.append(
    metaItem('Распознанные серии', formatEpisodes(release.episodes)),
    metaItem('Период проверки', formatInterval(release.interval_minutes)),
    metaItem('Последняя проверка', formatDate(release.last_checked_at)),
    metaItem('Следующая проверка', release.enabled ? formatDate(release.next_check_at) : 'Остановлена'),
  );
  if (release.sonarr_series_title) {
    meta.append(metaItem('Сериал в Sonarr', release.sonarr_series_title));
  }
  card.append(header, meta);
  if (release.last_error) card.append(element('p', 'release-error', release.last_error));

  const actions = element('div', 'release-actions');
  actions.append(
    actionButton('Проверить сейчас', 'check', release.id),
    actionButton('Изменить', 'edit', release.id),
    actionButton(release.enabled ? 'Поставить на паузу' : 'Возобновить', 'toggle', release.id),
    actionButton('История', 'history', release.id),
    actionButton('Удалить', 'delete', release.id, 'danger'),
  );
  card.append(actions);
  return card;
}

async function loadReleases() {
  try {
    const releases = await api('/api/releases');
    releasesCache = releases;
    releasesNode.replaceChildren(...releases.map(renderRelease));
    emptyNode.hidden = releases.length > 0;
  } catch (error) {
    emptyNode.hidden = false;
    emptyNode.textContent = `Не удалось загрузить список: ${error.message}`;
  }
}

function openEditDialog(release) {
  editMessage.textContent = '';
  editMessage.className = 'form-message';
  document.querySelector('#edit-release-title').textContent = release.title;
  editForm.elements.interval_days.value = Math.round(release.interval_minutes / 1440) || 1;
  editForm.elements.search_query.value = release.search_query || '';
  document.querySelector('#edit-search-query-field').hidden = release.source === 'anistar';
  const options = [new Option('Без интеграции Sonarr', '')];
  for (const option of sonarrSeriesSelect.options) {
    if (option.value) options.push(new Option(option.text, option.value));
  }
  editSeriesSelect.replaceChildren(...options);
  editSeriesSelect.value = release.sonarr_series_id || '';
  editForm.dataset.id = release.id;
  editDialog.showModal();
}

async function checkConnection(name, label) {
  const node = document.querySelector(`#${name}-status`);
  try {
    const status = await api(`/api/${name}/status`);
    node.textContent = `${label} ${status.version || ''} подключён`;
    node.className = 'connection online';
    node.title = '';
  } catch (error) {
    node.textContent = `${label} недоступен`;
    node.title = error.message;
    node.className = 'connection offline';
  }
}

async function checkConnections() {
  await Promise.all([
    checkConnection('prowlarr', 'Prowlarr'),
    checkConnection('sonarr', 'Sonarr'),
    checkConnection('qbit', 'qBittorrent'),
  ]);
}

async function loadSonarrSeries() {
  try {
    const series = await api('/api/sonarr/series');
    const options = [new Option('Без интеграции Sonarr', '')];
    for (const item of series) {
      options.push(new Option(`${item.title}${item.year ? ` (${item.year})` : ''}`, item.id));
    }
    sonarrSeriesSelect.replaceChildren(...options);
  } catch (error) {
    sonarrSeriesSelect.options[0].text = 'Sonarr недоступен — без привязки';
    sonarrSeriesSelect.title = error.message;
  }
}

const searchQueryField = document.querySelector('#search-query-field');
form.elements.url.addEventListener('input', () => {
  searchQueryField.hidden = isAnistarUrl(form.elements.url.value);
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  const url = data.get('url');
  const sonarrSeriesId = data.get('sonarr_series_id') ? Number(data.get('sonarr_series_id')) : null;
  try {
    if (isAnistarUrl(url) && sonarrSeriesId) {
      const series = await api(`/api/sonarr/series/${sonarrSeriesId}`);
      const prompt = series.downloaded_count
        ? 'Будут докачаны серии со страницы, которых нет в Sonarr. Продолжить?'
        : 'Сериал пуст — будут скачаны все серии со страницы. Продолжить?';
      if (!window.confirm(prompt)) return;
    }
    button.disabled = true;
    formMessage.className = 'form-message';
    formMessage.textContent = isAnistarUrl(url)
      ? 'Добавляем сериал с AniStar…'
      : 'Ищем раздачу через Prowlarr…';
    await api('/api/releases', {
      method: 'POST',
      timeout: 300000,
      body: JSON.stringify({
        url: url,
        interval_days: Number(data.get('interval_days')),
        search_query: data.get('search_query') || null,
        sonarr_series_id: sonarrSeriesId,
      }),
    });
    form.reset();
    form.querySelector('[name="interval_days"]').value = 1;
    searchQueryField.hidden = false;
    formMessage.className = 'form-message success';
    formMessage.textContent = 'Раздача добавлена и проверена.';
    await loadReleases();
  } catch (error) {
    formMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

releasesNode.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const id = Number(button.dataset.id);
  const action = button.dataset.action;
  if (action === 'history') {
    historyContent.textContent = 'Загрузка…';
    historyDialog.showModal();
    try {
      const history = await api(`/api/releases/${id}/history`);
      if (!history.length) {
        historyContent.textContent = 'Изменений и отправок пока не было.';
        return;
      }
      historyContent.replaceChildren(...history.map((item) => {
        const wrapper = element('div', 'history-item');
        wrapper.append(
          element('small', '', `${formatDate(item.created_at)} · ${statusNames[item.status] || item.status}`),
          element('p', '', item.message),
          element('div', 'history-title', item.title),
        );
        return wrapper;
      }));
    } catch (error) {
      historyContent.textContent = error.message;
    }
    return;
  }
  if (action === 'delete' && !window.confirm('Удалить раздачу из мониторинга?')) return;
  if (action === 'edit') {
    const release = releasesCache.find((item) => item.id === id);
    if (release) openEditDialog(release);
    return;
  }
  button.disabled = true;
  try {
    if (action === 'check') await api(`/api/releases/${id}/check`, { method: 'POST', timeout: 300000 });
    if (action === 'delete') await api(`/api/releases/${id}`, { method: 'DELETE' });
    if (action === 'toggle') {
      const card = button.closest('.release');
      const paused = card.classList.contains('paused');
      await api(`/api/releases/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: paused }),
      });
    }
    await loadReleases();
  } catch (error) {
    window.alert(error.message);
    button.disabled = false;
  }
});

function toggleQbitAuthFields() {
  const method = document.querySelector('#qbit-auth-method').value;
  document.querySelector('#qbit-login-fields').hidden = method !== 'login';
  document.querySelector('#qbit-api-key-field').hidden = method !== 'api_key';
}

async function loadConnectionSettings() {
  const settings = await api('/api/settings');
  for (const name of ['prowlarr_url', 'sonarr_url', 'qbit_url', 'flaresolverr_url', 'qbit_auth_method', 'qbit_username', 'rutracker_username', 'kinozal_username']) {
    settingsForm.elements[name].value = settings[name] || (name.endsWith('_url') ? 'http://' : '');
  }
  for (const name of ['prowlarr_api_key', 'sonarr_api_key', 'qbit_password', 'qbit_api_key', 'rutracker_password', 'kinozal_password']) {
    const input = settingsForm.elements[name];
    input.value = '';
    input.placeholder = settings[`${name}_set`] ? '************' : '';
  }
  toggleQbitAuthFields();
}

document.querySelector('#open-settings').addEventListener('click', async () => {
  settingsMessage.textContent = '';
  settingsDialog.showModal();
  try {
    await loadConnectionSettings();
  } catch (error) {
    settingsMessage.textContent = error.message;
  }
});
document.querySelector('#close-settings').addEventListener('click', () => settingsDialog.close());
document.querySelector('#qbit-auth-method').addEventListener('change', toggleQbitAuthFields);
settingsDialog.addEventListener('click', (event) => {
  if (event.target === settingsDialog) settingsDialog.close();
});
settingsForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = settingsForm.querySelector('button[type="submit"]');
  button.disabled = true;
  settingsMessage.className = 'form-message';
  try {
    const values = Object.fromEntries(new FormData(settingsForm).entries());
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(values) });
    settingsMessage.className = 'form-message success';
    settingsMessage.textContent = 'Настройки сохранены и применены.';
    await Promise.all([checkConnections(), loadSonarrSeries(), loadConnectionSettings()]);
  } catch (error) {
    settingsMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#refresh').addEventListener('click', loadReleases);
document.querySelector('#close-history').addEventListener('click', () => historyDialog.close());
document.querySelector('#close-edit').addEventListener('click', () => editDialog.close());
editDialog.addEventListener('click', (event) => {
  if (event.target === editDialog) editDialog.close();
});
editForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = editForm.querySelector('button[type="submit"]');
  button.disabled = true;
  editMessage.className = 'form-message';
  try {
    const data = new FormData(editForm);
    await api(`/api/releases/${editForm.dataset.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        interval_days: Number(data.get('interval_days')),
        search_query: data.get('search_query') || null,
        sonarr_series_id: data.get('sonarr_series_id') ? Number(data.get('sonarr_series_id')) : null,
      }),
    });
    editDialog.close();
    await loadReleases();
  } catch (error) {
    editMessage.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
historyDialog.addEventListener('click', (event) => {
  if (event.target === historyDialog) historyDialog.close();
});

loadReleases();
checkConnections();
loadSonarrSeries();
setInterval(loadReleases, 15000);
setInterval(checkConnections, 60000);
