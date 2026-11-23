(() => {
  const presets = [
    { name: 'Контрагенты', method: 'GET', path: '/counterparties?page=1&limit=20', body: '', hint: 'Список контрагентов' },
    { name: 'Контрагент', method: 'GET', path: '/counterparty/000000001', body: '', hint: 'Карточка контрагента по коду или GUID' },
    { name: 'Номенклатура', method: 'GET', path: '/nomenclatures?page=1&limit=20', body: '', hint: 'Список товаров и услуг' },
    { name: 'Заказы', method: 'GET', path: '/orders?page=1&limit=20', body: '', hint: 'Список заказов' },
    { name: 'Создать заказ', method: 'POST', path: '/order', body: { date: new Date().toISOString().slice(0, 10), customer: { id: 'ВСТАВЬТЕ_ID_КЛИЕНТА' }, items: [{ product_id: 'ВСТАВЬТЕ_ID_ТОВАРА', quantity: 1, price: 1000 }], comment: 'Тест из формы API ТеМа' }, hint: 'Создание черновика заказа' },
    { name: 'Счета', method: 'GET', path: '/invoices?page=1&limit=20', body: '', hint: 'Список счетов на оплату' },
    { name: 'Создать счёт', method: 'POST', path: '/invoice', body: { order_id: 'ВСТАВЬТЕ_ID_ЗАКАЗА', date: new Date().toISOString().slice(0, 10), comment: 'Тест из формы API ТеМа' }, hint: 'Создание черновика счёта по заказу' },
    { name: 'Акты', method: 'GET', path: '/acts?page=1&limit=20', body: '', hint: 'Список актов' },
    { name: 'Создать акт', method: 'POST', path: '/act', body: { invoice_id: 'ВСТАВЬТЕ_ID_СЧЁТА', date: new Date().toISOString().slice(0, 10), comment: 'Тест из формы API ТеМа' }, hint: 'Создание черновика акта по счёту' },
    { name: 'PDF · КП', method: 'GET', path: '/order/ВСТАВЬТЕ_ID_ЗАКАЗА/kp/pdf', body: '', hint: 'Печатная форма КП по сохранённому заказу' },
    { name: 'PDF · спецификация', method: 'GET', path: '/order/ВСТАВЬТЕ_ID_ЗАКАЗА/specification/pdf', body: '', hint: 'Спецификация с позициями и итогами заказа' },
    { name: 'PDF · счёт', method: 'GET', path: '/invoice/ВСТАВЬТЕ_ID_СЧЁТА/pdf', body: '', hint: 'Печатная форма счёта' },
    { name: 'PDF · акт', method: 'GET', path: '/act/ВСТАВЬТЕ_ID_АКТА/pdf', body: '', hint: 'Печатная форма акта' },
    { name: 'Создать контрагента', method: 'POST', path: '/counterparty', body: { name: 'Тест из формы API ТеМа', type: 'ЮридическоеЛицо' }, hint: 'Создание тестового контрагента' },
  ];
  const $ = (id) => document.getElementById(id);
  const ui = { token: $('token'), method: $('method'), path: $('path'), body: $('body'), send: $('send'), presets: $('presets'), empty: $('responseEmpty'), content: $('responseContent'), badge: $('statusBadge'), time: $('responseTime'), json: $('responseJson'), pdf: $('pdfResult'), pdfOpen: $('pdfOpen'), pdfDownload: $('pdfDownload'), healthDot: $('healthDot'), healthText: $('healthText') };
  let activePreset = 0;
  let pdfUrl = null;
  const pretty = (value) => JSON.stringify(value, null, 2);
  const apiUrl = () => {
    const path = ui.path.value.trim();
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Путь должен начинаться с / и быть относительным.');
    return '/api/v1' + path;
  };
  const setHealth = (ok, text) => { ui.healthDot.className = 'dot ' + (ok ? 'ok' : 'bad'); ui.healthText.textContent = text; };
  const renderPresets = () => {
    ui.presets.replaceChildren();
    presets.forEach((preset, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'preset' + (index === activePreset ? ' active' : ''); button.textContent = preset.name; button.title = preset.hint;
      button.addEventListener('click', () => { activePreset = index; ui.method.value = preset.method; ui.path.value = preset.path; ui.body.value = preset.body ? pretty(preset.body) : ''; renderPresets(); });
      ui.presets.append(button);
    });
  };
  const checkHealth = async () => {
    setHealth(false, 'Проверяем соединение…');
    try { const r = await fetch('/health', { cache: 'no-store' }); const data = await r.json(); if (!r.ok) throw new Error(); setHealth(true, data.status === 'ok' ? 'API доступен' : 'API отвечает'); }
    catch { setHealth(false, 'API недоступен · запустите интеграцию'); }
  };
  const showResult = (status, duration, value, isError = false) => {
    ui.empty.hidden = true; ui.content.hidden = false; ui.pdf.hidden = true; ui.json.hidden = false;
    ui.badge.className = 'status-badge ' + (isError ? 'error' : status < 300 ? 'success' : 'error'); ui.badge.textContent = String(status) + (status >= 200 && status < 300 ? ' · Успешно' : ' · Ошибка');
    ui.time.textContent = duration + ' мс'; ui.json.textContent = pretty(value);
  };
  const send = async () => {
    const method = ui.method.value;
    const token = ui.token.value.trim();
    if (!token) { ui.token.focus(); showResult('—', 0, { error: { message: 'Введите Bearer-токен из локальных настроек API.' } }, true); return; }
    let url;
    let body;
    try { url = apiUrl(); if (method !== 'GET') body = JSON.parse(ui.body.value || '{}'); }
    catch (error) { showResult('—', 0, { error: { message: error.message } }, true); return; }
    const headers = { Authorization: 'Bearer ' + token, Accept: 'application/json, application/pdf' };
    if (method !== 'GET') { headers['Content-Type'] = 'application/json'; if (method === 'POST') headers['Idempotency-Key'] = 'tema-test-' + crypto.randomUUID(); }
    ui.send.disabled = true; ui.send.firstElementChild.textContent = 'Отправляем…';
    const started = performance.now();
    try {
      const response = await fetch(url, { method, headers, body: method === 'GET' ? undefined : JSON.stringify(body), cache: 'no-store' });
      const elapsed = Math.round(performance.now() - started);
      const type = (response.headers.get('content-type') || '').toLowerCase();
      if (type.includes('application/pdf')) {
        const blob = await response.blob(); if (pdfUrl) URL.revokeObjectURL(pdfUrl); pdfUrl = URL.createObjectURL(blob);
        ui.empty.hidden = true; ui.content.hidden = false; ui.badge.className = 'status-badge success'; ui.badge.textContent = response.status + ' · PDF'; ui.time.textContent = elapsed + ' мс'; ui.json.hidden = true; ui.pdf.hidden = false;
        ui.pdfOpen.href = pdfUrl; ui.pdfDownload.href = pdfUrl; ui.pdfDownload.download = (response.headers.get('content-disposition')?.match(/filename\*?=(?:UTF-8''|\")?([^;\"]+)/i)?.[1] || 'document.pdf').trim();
      } else {
        const text = await response.text(); let value; try { value = text ? JSON.parse(text) : {}; } catch { value = { response: text }; }
        showResult(response.status, elapsed, value);
      }
    } catch (error) { showResult('—', Math.round(performance.now() - started), { error: { message: 'Не удалось подключиться к API.', details: error.message } }, true); }
    finally { ui.send.disabled = false; ui.send.firstElementChild.textContent = 'Отправить запрос'; }
  };
  ui.send.addEventListener('click', send);
  ui.body.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') send(); });
  $('formatJson').addEventListener('click', () => { try { ui.body.value = pretty(JSON.parse(ui.body.value)); } catch { ui.body.focus(); } });
  $('toggleToken').addEventListener('click', (event) => { const hidden = ui.token.type === 'password'; ui.token.type = hidden ? 'text' : 'password'; event.currentTarget.textContent = hidden ? 'Скрыть' : 'Показать'; });
  $('healthRefresh').addEventListener('click', checkHealth);
  $('copyResponse').addEventListener('click', async (event) => { try { await navigator.clipboard.writeText(ui.json.textContent); event.currentTarget.textContent = 'Скопировано'; setTimeout(() => { event.currentTarget.textContent = 'Скопировать JSON'; }, 1300); } catch { event.currentTarget.textContent = 'Не удалось скопировать'; } });
  ui.method.addEventListener('change', () => { if (ui.method.value === 'GET') ui.body.value = ''; });
  renderPresets();
  checkHealth();
})();
