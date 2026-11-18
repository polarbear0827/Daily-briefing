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
    el('p', data.date + ' · ' + (data.edition === 'evening' ? '晚報' : '早報') + ' · 共 ' + data.items.length + ' 則', 'su-meta', section);
    el('p', '獨立收錄官方功能與模型更新；不套用新聞重要性篩選或篇數配額。', 'su-note', section);
    if (data.publication && data.publication.mode === 'initial-user-requested-rollout') {
      el('p', '欄位啟用補收（非正常排程出刊）｜實際公開時間：' + (data.publication.published_at || '未提供'), 'su-note', section);
    }
    const health = sourceHealth(data);
    let healthText;
    if (!health.entries.length) healthText = '來源查核狀態未提供；無法判定是否有更新。';
    else if (!health.good) healthText = '來源目前無法查核；無法判定是否有更新。';
    else if (health.bad) healthText = '部分來源無法查核；下列僅為已取得的官方更新，並非完整清單。';
    else if (!data.items.length) healthText = '已成功檢查的官方來源未發現尚未收錄的新公告。';
    else healthText = '所列來源查核正常。';
    el('p', healthText, 'su-health', section);
    for (const source of health.entries) {
      const names = { google: 'Google／Gemini／NotebookLM', anthropic: 'Anthropic', deepmind: 'DeepMind', openai: 'OpenAI', xai: 'Grok／xAI', meta: 'Meta' };
      const name = source.provider_name || names[source.provider_id] || source.provider_id || '官方來源';
      const channel = source.source_name || source.source_id || source.id;
      const status = source.status === 'disabled' ? '未啟用' : source.status === 'partial' ? '部分可查核' : (source.ok === true || ['ok', 'healthy', 'success'].includes(source.status)) ? '查核正常' : '無法查核';
      el('p', name + (channel ? '／' + channel : '') + '：' + status, 'su-source', section);
    }
    for (const item of data.items) {
      const entry = el('div', null, 'su-entry', section);
      el('p', item.provider_name || item.provider_id || '官方來源', 'su-provider', entry);
      el('h3', item.title_zh || '官方更新', 'su-title', entry);
      const date = item.published_at || item.published_date;
      el('p', '公告日期：' + (date || '官方未提供') + (item.published_at ? '' : date ? '（僅日期，未提供時間）' : ''), 'su-date', entry);
      el('p', '開放情況：' + (item.availability_zh || '開放條件請見官方公告'), 'su-availability', entry);
      const kinds = { 'official-page': '官方頁面', 'official-feed-excerpt': '官方摘要摘錄（非全文）', 'official-release-log': '官方更新紀錄' };
      el('p', kinds[item.source_kind] || '官方來源類型未標示', 'su-evidence', entry);
      if (item.status === 'link-only') el('p', '僅提供官方連結；尚無已驗證摘要。', 'su-label', entry);
      if (item.summary_zh && item.status !== 'link-only') el('p', item.summary_zh, 'su-summary', entry);
      if (item.late_discovery) el('p', '延後發現：公告早於本次收錄，並非本日新公告。', 'su-label', entry);
      if (item.observed_at) el('p', '查得時間：' + item.observed_at, 'su-observed', entry);
      const href = safeOfficialURL(item.source_url);
      if (href) {
        const link = el('a', '官方連結', 'su-link', entry);
        link.setAttribute('href', href);
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
      } else el('p', '官方連結無效，已停用。', 'su-label', entry);
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
.su-section{max-width:1280px;margin:48px auto;padding:32px;border-top:3px double var(--ink,#1a1814);color:var(--ink,#1a1814);font-family:'Noto Serif TC','Songti TC',serif;line-height:1.8;overflow-wrap:anywhere}
.su-section .su-heading{font-size:clamp(25px,4vw,36px);letter-spacing:.08em;margin:0 0 12px}
.su-section .su-meta{color:var(--ink-muted,#6b655b);font-size:15px;margin-bottom:12px}
.su-section .su-note,.su-section .su-health,.su-section .su-status{margin:12px 0}
.su-section .su-source{display:inline-block;margin:0 16px 8px 0;font-size:13px;color:var(--ink-muted,#6b655b)}
.su-section .su-entry{padding:26px 0;border-top:1px solid rgba(26,24,20,.2)}
.su-section .su-provider{font-size:13px;color:var(--accent-soft,#a8342c);font-weight:bold;letter-spacing:.05em}
.su-section .su-title{font-size:23px;margin:6px 0 12px;line-height:1.5}
.su-section .su-date,.su-section .su-evidence,.su-section .su-observed{font-size:13px;color:var(--ink-muted,#6b655b);margin:5px 0}
.su-section .su-summary,.su-section .su-availability{margin:10px 0}
.su-section .su-label{color:var(--accent-soft,#a8342c);font-size:14px;margin:10px 0}
.su-section .su-link{color:var(--accent-soft,#a8342c);text-decoration:underline;text-underline-offset:3px}
@media(max-width:600px){.su-section{padding:24px 20px;margin:24px auto}.su-section .su-title{font-size:21px}}
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
