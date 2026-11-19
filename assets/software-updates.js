/* Independent official-updates reader. No news/read/affinity storage access. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root.SoftwareUpdatesReader = api;
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', function () { api.mount(root); }, { once: true });
    else api.mount(root);
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const BASE = '/Daily-briefing/';
  function validDate(value) {
    return /^\d{4}-\d{2}-\d{2}$/.test(value || '') &&
      Number.isFinite(Date.parse(value + 'T00:00:00Z')) &&
      new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
  }
  function resolveRequest(href, issue, currentEdition) {
    const u = new URL(href, 'https://example.org');
    const dated = u.pathname.match(/\/briefings\/(\d{4}-\d{2}-\d{2})-(morning|midday|evening)(?:\.html)?\/?$/);
    const edition = u.searchParams.get('edition') || (dated && dated[2]) || currentEdition || (issue && issue.edition) || 'morning';
    if (edition !== 'morning' && edition !== 'evening') return null;
    const date = u.searchParams.has('date') ? u.searchParams.get('date') : dated && dated[1];
    if (date !== null && date !== undefined && !validDate(date)) return { invalid: true, edition, date };
    return { edition, date: date || null, url: BASE + 'data/software-updates/' + (date ? date + '-' : '') + edition + '.json' };
  }
  function safeOfficialURL(value) {
    if (typeof value !== 'string' || !/^https:\/\//i.test(value) || /[\s<>"'`\\]/.test(value)) return null;
    try {
      const u = new URL(value);
      const host = u.hostname.toLowerCase().replace(/\.$/, '');
      if (u.protocol !== 'https:' || u.username || u.password || !host.includes('.') || host.includes(':') || /(?:^|\.)(localhost|local|internal|test|invalid)$/.test(host)) return null;
      if (/^[\d.]+$/.test(host)) {
        const p = host.split('.').map(Number);
        if (p.length !== 4 || p[0] === 0 || p[0] === 10 || p[0] === 127 || p[0] >= 224 ||
            (p[0] === 169 && p[1] === 254) || (p[0] === 172 && p[1] >= 16 && p[1] <= 31) ||
            (p[0] === 192 && (p[1] === 168 || p[1] === 0)) || (p[0] === 100 && p[1] >= 64 && p[1] <= 127) ||
            (p[0] === 198 && (p[1] === 18 || p[1] === 19))) return null;
      }
      return value;
    } catch (_) { return null; }
  }
  async function loadRequest(request, fetcher) {
    if (!request || request.invalid) return { state: 'invalid' };
    try {
      const response = await fetcher(request.url, { cache: 'no-cache', credentials: 'omit' });
      if (response.status === 404) return { state: 'missing' };
      if (!response.ok) return { state: 'unavailable' };
      let data;
      try { data = await response.json(); } catch (_) { return { state: 'invalid' }; }
      if (!data || data.schema_version !== 1 || data.kind !== 'official-software-updates' ||
          !validDate(data.date) || data.edition !== request.edition || (request.date && data.date !== request.date) ||
          !Array.isArray(data.items) || data.items.some(item => !item || typeof item !== 'object') ||
          data.selection_applied !== false || data.news_minimum_applies !== false) return { state: 'invalid' };
      return { state: 'ready', data };
    } catch (_) { return { state: 'unavailable' }; }
  }
  function displayDate(value) {
    if (validDate(value)) return value.replace(/-/g, '/') + '（官方僅提供日期）';
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
        !validDate(value.slice(0, 10)) || !Number.isFinite(Date.parse(value))) return '官方未提供可辨識的日期';
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value));
    const p = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return p.year + '/' + p.month + '/' + p.day + ' ' + p.hour + ':' + p.minute + '（台灣時間）';
  }
  function sourceHealth(data) {
    const raw = data.source_health;
    const entries = (Array.isArray(raw) ? raw : raw && typeof raw === 'object' ? Object.entries(raw).map(([id, value]) => typeof value === 'string' ? { provider_id: id, status: value } : { provider_id: id, ...value }) : []).map(value => value && typeof value === 'object' ? value : { status: 'unknown' });
    const good = entries.filter(e => e.ok === true || ['ok', 'healthy', 'success'].includes(e.status)).length;
    return { entries, good, bad: entries.length - good };
  }
  function renderSection(document, result) {
    function el(tag, text, cls, parent) {
      const node = document.createElement(tag);
      if (text !== null && text !== undefined) node.textContent = String(text);
      if (cls) node.className = cls;
      if (parent) parent.appendChild(node);
      return node;
    }
    const section = el('section', null, 'su-section');
    section.id = 'software-updates-section';
    section.setAttribute('aria-label', '軟體與模型更新');
    el('h2', '軟體與模型更新', 'su-heading', section);
    const messages = { loading: '正在讀取官方更新資料…', missing: '此報期的官方更新資料尚未提供。', unavailable: '官方更新資料暫時無法讀取；無法判定是否有更新。', invalid: '官方更新資料格式或報期不符，未顯示其他日期的資料。' };
    if (result.state !== 'ready') { el('p', messages[result.state] || messages.unavailable, 'su-status', section); return section; }
    const data = result.data;
    const readable = item => item.status === 'verified-summary' && !!item.summary_zh;
    const summaries = data.items.filter(readable);
    const links = data.items.filter(item => !readable(item));
    el('p', data.date.replace(/-/g, '/') + (data.edition === 'evening' ? '科技晚報' : '科技早報'), 'su-meta', section);
    el('p', '本期新增收錄' + data.items.length + '則，' + summaries.length + '則有中文摘要、' + links.length + '則提供官方連結', 'su-count', section);
    el('p', '新增收錄不是剛發布，也不代表尚未上市；請以每則公告日期與開放條件為準。', 'su-note', section);
    if (data.publication && data.publication.mode === 'initial-user-requested-rollout') {
      el('p', '欄位啟用補收（非正常排程出刊）｜實際公開時間：' + displayDate(data.publication.published_at), 'su-note', section);
    }
    const health = sourceHealth(data);
    let healthText;
    if (!health.entries.length) healthText = '來源查核狀態未提供；無法判定是否有更新。';
    else if (!health.good) healthText = '來源目前無法查核；無法判定是否有更新。';
    else if (health.bad) healthText = '部分來源無法查核；本期僅列已取得的官方更新，並非完整清單。';
    else if (!data.items.length) healthText = '已成功檢查的官方來源未發現尚未收錄的新公告。';
    else healthText = '所列來源查核正常。';
    const names = { google: 'Google', anthropic: 'Anthropic', deepmind: 'Google DeepMind', openai: 'OpenAI', xai: 'Grok／xAI', meta: 'Meta', mistral: 'Mistral', github: 'GitHub', windows: 'Windows' };
    const providerName = value => value.provider_name || names[value.provider_id] || '官方來源';
    const channels = {
      'official-google-blog': 'Google 官方部落格', 'official-google-notebook-seed': 'Google Gemini Notebook 官方頁面',
      'official-google-api': 'Gemini API 更新紀錄', 'official-deepmind': 'Google DeepMind 官方部落格',
      'official-claude-api': 'Claude API 更新紀錄', 'official-claude-app': 'Claude 應用程式更新紀錄',
      'official-openai-news': 'OpenAI 官方消息', 'official-openai-api': 'OpenAI API 更新紀錄',
      'official-xai-api': 'xAI API 更新紀錄', 'official-meta': 'Meta 官方消息',
      'official-mistral': 'Mistral 官方消息', 'official-github': 'GitHub 更新紀錄',
      'official-windows': 'Windows 開發者部落格', 'official-chatgpt-app': 'ChatGPT 更新紀錄', 'official-xai-news': 'xAI 官方消息'
    };
    const ordinals = new Map();
    for (const [label, items] of [['可閱讀的更新摘要', summaries], ['尚待中文整理的官方公告（保留連結）', links]]) {
      const group = el('section', null, 'su-group', section);
      el('h3', label, 'su-group-heading', group);
    for (const item of items) {
      const entry = el('div', null, 'su-entry', group);
      entry.setAttribute('data-update-id', item.id || '');
      const provider = providerName(item);
      el('p', provider, 'su-provider', entry);
      let title = item.title_zh || '官方更新';
      if (!readable(item) && ['官方公告', '官方更新', provider + ' 官方公告', provider + ' 官方更新'].includes(title)) {
        const ordinal = (ordinals.get(provider) || 0) + 1;
        ordinals.set(provider, ordinal);
        title = provider + ' 官方公告 ' + ordinal;
      }
      el('h4', title, 'su-title', entry);
      const date = item.published_at || item.published_date;
      el('p', '公告日期：' + displayDate(date), 'su-date', entry);
      el('p', '更新內容：' + (readable(item) ? item.summary_zh : '僅提供官方連結；尚無已驗證的中文摘要，目前還無法說明更新內容。'), 'su-summary', entry);
      el('p', '誰能使用：' + (item.availability_zh || '開放條件請見官方公告'), 'su-availability', entry);
      const href = safeOfficialURL(item.source_url);
      if (href) {
        const link = el('a', '查看官方公告', 'su-link', entry);
        link.setAttribute('href', href);
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
      } else el('p', '官方連結無效，已停用。', 'su-label', entry);
      const details = el('details', null, 'su-details', entry);
      el('summary', '公告日期與來源說明', null, details);
      el('p', '公告日期：' + displayDate(date), 'su-date', details);
      const kinds = { 'official-page': '官方頁面', 'official-feed-excerpt': '官方摘要摘錄（非全文）', 'official-release-log': '官方更新紀錄' };
      el('p', kinds[item.source_kind] || '官方來源類型未標示', 'su-evidence', details);
      if (item.late_discovery) el('p', '延後發現：公告早於本次收錄，並非本日新公告。', 'su-label', details);
      if (item.observed_at) el('p', '查得時間：' + displayDate(item.observed_at), 'su-observed', details);
    }
    }
    el('p', healthText, 'su-health', section);
    const healthDetails = el('details', null, 'su-health-details', section);
    el('summary', '來源讀取情況（有' + health.bad + '個通道未完整讀取）', null, healthDetails);
    for (const source of health.entries) {
      const channel = channels[source.source_id || source.id] || providerName(source) + '／官方來源';
      const status = source.status === 'disabled' ? '未啟用' : source.status === 'partial' ? '部分可查核' : (source.ok === true || ['ok', 'healthy', 'success'].includes(source.status)) ? '查核正常' : '無法查核';
      el('p', channel + '：' + status, 'su-source', healthDetails);
    }
    return section;
  }
  function createController(options) {
    const cache = new Map();
    let previousKey, previousTarget, generation = 0, running;
    async function refresh() {
      const context = options.getContext();
      const request = resolveRequest(context.href, context.issue, context.currentEdition);
      const key = request ? request.invalid ? 'invalid:' + request.date + ':' + request.edition : request.url : 'hidden';
      if (key === previousKey && context.target === previousTarget) return running;
      previousKey = key;
      previousTarget = context.target;
      const token = ++generation;
      if (!context.target) return;
      if (!request) { options.commit(null, context.target); return; }
      if (request.invalid) { options.commit({ state: 'invalid' }, context.target); return; }
      let record = cache.get(key);
      if (!record) {
        record = { result: null };
        record.promise = loadRequest(request, options.fetcher).then(result => { record.result = result; return result; });
        cache.set(key, record);
      }
      if (record.result) { options.commit(record.result, context.target); return; }
      options.commit({ state: 'loading' }, context.target);
      running = record.promise.then(result => {
        if (token === generation) options.commit(result, context.target);
      });
      return running;
    }
    return { refresh };
  }
  function mount(window) {
    if (window.__softwareUpdatesMount) return window.__softwareUpdatesMount;
    const document = window.document;
    if (!document.querySelector('#software-updates-style')) {
      const style = document.createElement('style');
      style.id = 'software-updates-style';
      // Every rule is scoped to the addon; the pinned reader stylesheet is untouched.
      style.textContent = `
.su-section{box-sizing:border-box;max-width:900px;margin:24px auto;padding:24px 32px;border-top:3px double var(--ink,#1a1814);color:var(--ink,#1a1814);font-family:'Noto Serif TC','Songti TC',serif;line-height:1.8;overflow-wrap:anywhere;min-width:0}
.su-section .su-heading{font-size:clamp(25px,4vw,36px);letter-spacing:.08em;margin:0 0 12px}
.su-section .su-meta{font-size:23px;font-weight:bold;margin:8px 0}
.su-section .su-count{font-size:17px;margin:8px 0}
.su-section .su-group{margin-top:28px;min-width:0}
.su-section .su-group-heading{font-size:21px;line-height:1.5;margin:0 0 16px}
.su-section .su-details,.su-section .su-health-details{font-size:14px;margin-top:18px;color:var(--ink-muted,#6b655b)}
.su-section summary{cursor:pointer;min-height:32px}
.su-section .su-link:focus-visible,.su-section summary:focus-visible{outline:2px solid var(--accent-soft,#a8342c);outline-offset:4px}
.su-section .su-note,.su-section .su-health,.su-section .su-status{margin:12px 0}
.su-section .su-source{margin:8px 0;font-size:14px;color:var(--ink-muted,#6b655b)}
.su-section .su-entry{padding:26px 0;border-top:1px solid rgba(26,24,20,.2)}
.su-section .su-provider{font-size:13px;color:var(--accent-soft,#a8342c);font-weight:bold;letter-spacing:.05em}
.su-section .su-title{font-size:23px;margin:6px 0 12px;line-height:1.5}
.su-section .su-date,.su-section .su-evidence,.su-section .su-observed{font-size:13px;color:var(--ink-muted,#6b655b);margin:5px 0}
.su-section .su-summary,.su-section .su-availability{margin:12px 0;font-size:17px;white-space:pre-line}
.su-section .su-label{color:var(--accent-soft,#a8342c);font-size:14px;margin:10px 0}
.su-section .su-link{color:var(--accent-soft,#a8342c);text-decoration:underline;text-underline-offset:3px}
@media(max-width:600px){.su-section{padding:20px;margin:20px auto}.su-section .su-heading{font-size:23px}.su-section .su-title{font-size:21px}}
`;
      document.head.appendChild(style);
    }
    const controller = createController({
      fetcher: window.fetch.bind(window),
      getContext: function () {
        return { href: window.location.href, issue: window.__currentIssue, currentEdition: window.__currentEdition,
          target: document.querySelector('#software-updates-root') || document.querySelector('#issue-root main') || document.querySelector('#app main') || document.querySelector('#app') };
      },
      commit: function (result, target) {
        const old = document.querySelector('#software-updates-section');
        if (old) old.remove();
        if (result) target.appendChild(renderSection(document, result));
      }
    });
    const observed = document.querySelector('#app') || document.querySelector('#software-updates-root') || document.body;
    const observer = new window.MutationObserver(controller.refresh);
    observer.observe(observed, { childList: true, subtree: true });
    window.addEventListener('popstate', controller.refresh);
    window.__softwareUpdatesMount = controller;
    controller.refresh();
    return controller;
  }
  return { resolveRequest, safeOfficialURL, renderSection, loadRequest, createController, mount };
});
