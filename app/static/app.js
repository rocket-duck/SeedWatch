const releasesNode = document.querySelector('#releases');
const emptyNode = document.querySelector('#empty');
const form = document.querySelector('#add-form');
const formMessage = document.querySelector('#form-message');
const historyDialog = document.querySelector('#history-dialog');
const historyContent = document.querySelector('#history-content');

const statusNames = {
  monitoring: 'Отслеживается',
  ok: 'Без изменений',
  grabbed: 'Отправлено',
  changed: 'Без новых серий',
  error: 'Ошибка',
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
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

function renderRelease(release) {
  const card = element('article', `release${release.enabled ? '' : ' paused'}`);
  const header = element('div', 'release-header');
  const title = element('a', 'release-title', release.title);
  title.href = release.url;
  title.target = '_blank';
  title.rel = 'noreferrer';
  const status = release.enabled ? release.last_status : 'paused';
  header.append(title, element('span', `badge ${status}`, release.enabled ? (statusNames[status] || status) : 'На паузе'));

  const meta = element('div', 'release-meta');
  meta.append(
    metaItem('Распознанные серии', formatEpisodes(release.episodes)),
    metaItem('Период проверки', formatInterval(release.interval_minutes)),
    metaItem('Последняя проверка', formatDate(release.last_checked_at)),
    metaItem('Следующая проверка', release.enabled ? formatDate(release.next_check_at) : 'Остановлена'),
  );
  card.append(header, meta);
  if (release.last_error) card.append(element('p', 'release-error', release.last_error));

  const actions = element('div', 'release-actions');
  actions.append(
    actionButton('Проверить сейчас', 'check', release.id),
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
    releasesNode.replaceChildren(...releases.map(renderRelease));
    emptyNode.hidden = releases.length > 0;
  } catch (error) {
    emptyNode.hidden = false;
    emptyNode.textContent = `Не удалось загрузить список: ${error.message}`;
  }
}

async function checkProwlarr() {
  const node = document.querySelector('#prowlarr-status');
  try {
    const status = await api('/api/prowlarr/status');
    node.textContent = `Prowlarr ${status.version || ''} подключён`;
    node.className = 'connection online';
  } catch (error) {
    node.textContent = 'Prowlarr недоступен';
    node.title = error.message;
    node.className = 'connection offline';
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  button.disabled = true;
  formMessage.className = 'form-message';
  formMessage.textContent = 'Ищем раздачу через Prowlarr…';
  try {
    await api('/api/releases', {
      method: 'POST',
      body: JSON.stringify({
        url: data.get('url'),
        interval_days: Number(data.get('interval_days')),
        search_query: data.get('search_query') || null,
      }),
    });
    form.reset();
    form.querySelector('[name="interval_days"]').value = 1;
    formMessage.className = 'form-message success';
    formMessage.textContent = 'Раздача добавлена. Текущие серии сохранены как отправная точка.';
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
  button.disabled = true;
  try {
    if (action === 'check') await api(`/api/releases/${id}/check`, { method: 'POST' });
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

document.querySelector('#refresh').addEventListener('click', loadReleases);
document.querySelector('#close-history').addEventListener('click', () => historyDialog.close());
historyDialog.addEventListener('click', (event) => {
  if (event.target === historyDialog) historyDialog.close();
});

loadReleases();
checkProwlarr();
setInterval(loadReleases, 15000);
