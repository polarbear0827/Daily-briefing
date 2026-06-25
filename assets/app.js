// =============================================================================
// Daily Briefing — Client-side renderer
// Loads data/latest.json (or data/issues/YYYY-MM-DD.json) and renders UI.
// =============================================================================

(function () {
  'use strict';

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---------------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------------

  async function fetchJson(path) {
    try {
      const res = await fetch(path, { cache: 'no-store' });
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      return null;
    }
  }

  async function loadAllEditions() {
    // URL param ?date=YYYY-MM-DD&edition=morning|evening overrides
    const params = new URLSearchParams(window.location.search);
    const date = params.get('date');
    const editionParam = params.get('edition');

    if (date) {
      const morning = await fetchJson(`data/issues/${date}-morning.json`);
      const evening = await fetchJson(`data/issues/${date}-evening.json`);
      // Fallback to legacy single-file format if neither edition file exists.
      const legacy = (!morning && !evening) ? await fetchJson(`data/issues/${date}.json`) : null;
      return { morning, evening, legacy, requestedEdition: editionParam };
    }

    const [morning, evening, latest] = await Promise.all([
      fetchJson('data/morning.json'),
      fetchJson('data/evening.json'),
      fetchJson('data/latest.json'),
    ]);
    return { morning, evening, legacy: (!morning && !evening) ? latest : null, requestedEdition: editionParam };
  }

  function renderError(err) {
    const main = qs('#app');
    if (!main) return;
    main.innerHTML = `
      <div style="max-width:600px;margin:120px auto;padding:40px;text-align:center;">
        <h1 style="font-family:var(--font-display-zh);font-size:32px;margin-bottom:16px;">
          載入失敗 · Load Failed
        </h1>
        <p style="color:var(--ink-muted);">${err.message}</p>
      </div>
    `;
  }

  // ---------------------------------------------------------------------------
  // Renderers
  // ---------------------------------------------------------------------------

  function getWeatherLocations(issue) {
    if (Array.isArray(issue.weather_locations) && issue.weather_locations.length > 0) {
      return issue.weather_locations.filter(location => location && typeof location === 'object');
    }

    if (issue.weather && typeof issue.weather === 'object') {
      return [{
        location_id: 'primary',
        location_zh: issue.weather.location_zh,
        location_en: issue.weather.location_en,
        temp_c: issue.weather.temp_c,
        condition_zh: issue.weather.condition_zh,
        condition_en: issue.weather.condition_en,
      }];
    }

    return [];
  }

  function renderWeather(issue) {
    const locations = getWeatherLocations(issue).filter(location => {
      const hasName = Boolean(location.location_zh || location.location_en);
      const hasTemp = Number.isFinite(Number(location.temp_c));
      const hasCondition = Boolean(location.condition_zh || location.condition_en);
      return hasName && hasTemp && hasCondition;
    });

    if (locations.length === 0) {
      return '<div class="weather-empty">天氣資料暫缺</div>';
    }

    return `
      <div class="weather-list">
        ${locations.map(location => {
          const name = location.location_zh || location.location_en || '天氣';
          const temp = `${Math.round(Number(location.temp_c))}°C`;
          const condition = location.condition_zh || location.condition_en || '資料暫缺';
          return `
            <span class="weather-chip">
              <span class="weather-city">${escapeHtml(name)}</span>
              <span class="weather-reading">${escapeHtml(temp)} · ${escapeHtml(condition)}</span>
            </span>
          `;
        }).join('')}
      </div>
    `;
  }

  function renderMasthead(issue) {
    return `
      <header class="masthead">
        <div class="masthead-meta">
          <div class="issue-number">第 ${issue.issue_number} 期 · No. ${issue.issue_number}</div>
          <div class="weather">
            ${renderWeather(issue)}
          </div>
          <div class="date">${issue.date_display_zh}</div>
        </div>
        <h1 class="masthead-title">Daily Briefing</h1>
        <p class="masthead-tagline">
          ${escapeHtml(issue.tagline_zh)}
          <span class="en">${escapeHtml(issue.tagline_en)}</span>
        </p>
        <p class="masthead-cross">
          <a href="https://polarbear0827.github.io/My-Way/" style="color:inherit;text-decoration:none;border:1px solid currentColor;border-radius:999px;padding:.3rem 1rem;font-size:.85rem;opacity:.85;">
            穗稻忠武的專欄 · AI Radar · Podcast →
          </a>
        </p>
      </header>
    `;
  }

  function renderTabs(issue) {
    const counts = {};
    issue.articles.forEach(a => { counts[a.category] = (counts[a.category] || 0) + 1; });
    const total = issue.articles.length;

    const items = [
      `<button class="tab active" data-cat="all">全部<span class="count">${total}</span></button>`
    ];
    issue.categories.forEach(cat => {
      const c = counts[cat.id] || 0;
      if (c === 0) return;
      items.push(
        `<button class="tab" data-cat="${cat.id}">${escapeHtml(cat.name_zh)}<span class="count">${c}</span></button>`
      );
    });

    return `<div class="tabs-wrap"><div class="tabs">${items.join('')}</div></div>`;
  }

  function renderCategorySection(cat, articles) {
    if (articles.length === 0) return '';
    const articleHtml = articles.map(a => renderArticle(a, cat)).join('');
    return `
      <section class="category-section" data-cat="${cat.id}">
        <div class="category-title">
          <h2>${escapeHtml(cat.name_zh)}</h2>
          <span class="count-label">${articles.length} 則 · ${escapeHtml(cat.name_en)}</span>
        </div>
        <div class="articles">${articleHtml}</div>
      </section>
    `;
  }

  function renderArticle(article, cat, opts) {
    opts = opts || {};
    const headlineClass = article.is_headline ? ' is-headline' : '';
    const breakingClass = opts.breaking ? ' is-breaking' : '';
    const kickerLabel = opts.breaking
      ? '🚨 重大快訊 · Breaking'
      : (article.is_headline ? '頭條 · Headline' : escapeHtml(cat.name_zh));
    const bulletsArr = Array.isArray(article.bullets_zh) ? article.bullets_zh.filter(b => b && String(b).trim()) : [];
    const bulletsHtml = bulletsArr.length
      ? `<ul class="article-bullets">${bulletsArr.map(b => `<li>${escapeHtml(b)}</li>`).join('')}</ul>`
      : '';
    const readingTime = article.source.reading_time_min || 3;
    const publishedAgo = relativeTime(article.source.published_at);

    return `
      <article class="article${headlineClass}${breakingClass}" data-id="${article.id}">
        <div class="article-kicker">
          <span class="kicker-badge">${kickerLabel}</span>
          ${article.is_teaching_material ? '<span class="teaching-badge" title="教學素材：適合企業學員上手的 AI 工具或概念">🎓 教學素材</span>' : ''}
        </div>
        <h3 class="article-title">${escapeHtml(article.title_zh)}</h3>
        ${article.title_en && article.title_en !== article.title_zh ? `<p class="article-title-en">${escapeHtml(article.title_en)}</p>` : ''}
        <p class="article-lede article-lede--prose">${escapeHtml(article.lede_zh)}</p>
        ${bulletsHtml}
        <div class="article-footer">
          <span class="article-source">${escapeHtml(article.source.name)}</span>
          <span class="dot">·</span>
          <span>${publishedAgo}</span>
          <span class="dot">·</span>
          <span>${readingTime} 分鐘</span>
          <div class="article-actions">
            <a href="${escapeAttr(article.source.url)}" target="_blank" rel="noopener"
               onclick="event.stopPropagation()">原文 EN</a>
          </div>
          <div class="article-reactions" data-article-id="${escapeAttr(article.id)}">
            <button class="reaction-btn" data-reaction="interested"
                    title="有興趣 — 想之後深入了解"
                    onclick="event.stopPropagation(); window.toggleReaction(this)">❤️</button>
            <button class="reaction-btn" data-reaction="to-implement"
                    title="想實作 — 看起來能用在工作或專案上"
                    onclick="event.stopPropagation(); window.toggleReaction(this)">🔧</button>
            <button class="reaction-btn" data-reaction="fun"
                    title="有趣 — 純粹覺得好玩"
                    onclick="event.stopPropagation(); window.toggleReaction(this)">😄</button>
          </div>
        </div>
      </article>
    `;
  }

  // ---------------------------------------------------------------------------
  // Reactions (Phase 1: localStorage; Phase 2: GitHub Issues sync)
  // ---------------------------------------------------------------------------
  const REACTION_KEY = 'dailyBriefing.reactions.v1';

  function loadReactions() {
    try { return JSON.parse(localStorage.getItem(REACTION_KEY) || '{}'); }
    catch { return {}; }
  }
  function saveReactions(map) {
    localStorage.setItem(REACTION_KEY, JSON.stringify(map));
  }

  window.toggleReaction = function (btn) {
    const wrap = btn.closest('.article-reactions');
    if (!wrap) return;
    const articleId = wrap.dataset.articleId;
    const reaction = btn.dataset.reaction;
    const map = loadReactions();
    map[articleId] = map[articleId] || {};
    map[articleId][reaction] = !map[articleId][reaction];
    if (!map[articleId][reaction]) delete map[articleId][reaction];
    if (Object.keys(map[articleId]).length === 0) delete map[articleId];
    saveReactions(map);
    btn.classList.toggle('is-active', !!(map[articleId] && map[articleId][reaction]));
  };

  function applyReactionStateAll() {
    const map = loadReactions();
    qsa('.article-reactions').forEach(wrap => {
      const id = wrap.dataset.articleId;
      const state = map[id] || {};
      qsa('.reaction-btn', wrap).forEach(b => {
        b.classList.toggle('is-active', !!state[b.dataset.reaction]);
      });
    });
  }

  function renderFooter(issue) {
    return `
      <footer class="site-footer">
        <div>第 ${issue.issue_number} 期 · 共 ${issue.meta.total_articles} 則 · 生成時間 ${formatTime(issue.meta.generated_at)}</div>
        <div style="margin-top:12px;">
          <a href="index.html">最新一期</a>
          <a href="archive.html">歷史期數</a>
        </div>
      </footer>
    `;
  }

  // ---------------------------------------------------------------------------
  // Reader overlay
  // ---------------------------------------------------------------------------

  function openReader(article, cat) {
    const overlay = qs('#reader-overlay');
    const bzArr = Array.isArray(article.bullets_zh) ? article.bullets_zh.filter(b => b && String(b).trim()) : [];
    const beArr = Array.isArray(article.bullets_en) ? article.bullets_en.filter(b => b && String(b).trim()) : [];
    const bulletsZh = bzArr.map(b => `<li>${escapeHtml(b)}</li>`).join('');
    const bulletsEn = beArr.map(b => `<li>${escapeHtml(b)}</li>`).join('');
    const hasDistinctEn = article.title_en && article.title_en !== article.title_zh;

    overlay.innerHTML = `
      <div class="reader-paper" data-lang="${hasDistinctEn ? 'both' : 'zh'}">
        <button class="reader-close" aria-label="Close">×</button>
        ${hasDistinctEn ? `<div class="reader-lang-toggle">
          <button data-lang="zh">中文</button>
          <button data-lang="both" class="active">中英對照</button>
          <button data-lang="en">EN</button>
        </div>` : ''}

        <div class="reader-kicker">${escapeHtml(cat.name_zh)}${hasDistinctEn ? ' · ' + escapeHtml(cat.name_en) : ''}</div>

        <h1 class="reader-title zh-only">${escapeHtml(article.title_zh)}</h1>
        <h1 class="reader-title en-only" style="font-family:var(--font-display-en);font-style:italic;">${escapeHtml(article.title_en)}</h1>
        <p class="reader-title-en en-only" style="display:none;"></p>
        <p class="reader-title-en zh-only" style="display:none;"></p>

        <p class="reader-lede zh-only">${escapeHtml(article.lede_zh)}</p>
        <p class="reader-lede en-only" style="font-family:var(--font-body-en);">${escapeHtml(article.lede_en)}</p>

        ${article.is_teaching_material && article.teaching_takeaway ? `
        <div class="reader-takeaway">
          <div class="reader-takeaway-label">🎓 教學素材 · 知識點</div>
          <p class="reader-takeaway-text">${escapeHtml(article.teaching_takeaway)}</p>
        </div>` : ''}

        <ul class="reader-bullets zh-only"${bulletsZh ? '' : ' style="display:none;"'}>${bulletsZh}</ul>
        <ul class="reader-bullets en-only"${bulletsEn ? '' : ' style="display:none;"'}>${bulletsEn}</ul>

        <div class="reader-source-block">
          <div class="label">原文來源 · Source</div>
          <a href="${escapeAttr(article.source.url)}" target="_blank" rel="noopener">
            ${escapeHtml(article.source.name)} · ${escapeHtml(new URL(article.source.url).hostname)}
          </a>
        </div>
      </div>
    `;

    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';

    // Close handlers
    qs('.reader-close', overlay).addEventListener('click', closeReader);
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeReader();
    });

    // Language toggle
    qsa('.reader-lang-toggle button', overlay).forEach(btn => {
      btn.addEventListener('click', () => {
        const lang = btn.dataset.lang;
        qs('.reader-paper', overlay).dataset.lang = lang;
        qsa('.reader-lang-toggle button', overlay).forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });
  }

  function closeReader() {
    const overlay = qs('#reader-overlay');
    overlay.classList.remove('open');
    overlay.innerHTML = '';
    document.body.style.overflow = '';
  }

  // ---------------------------------------------------------------------------
  // Tab filtering
  // ---------------------------------------------------------------------------

  function bindTabs() {
    qsa('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const cat = tab.dataset.cat;
        qsa('.tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        // Find headline's actual category (for tab filtering)
        const headlineEl = qs('.headline-section .article.is-headline');
        const headlineCat = headlineEl ? headlineEl.dataset.ownerCat : null;

        qsa('.category-section').forEach(section => {
          const sectionCat = section.dataset.cat;
          const isHeadlineSection = section.classList.contains('headline-section');

          let show;
          if (cat === 'all') {
            show = true;
          } else if (isHeadlineSection) {
            // Show headline if the active tab matches its owner category
            show = (headlineCat === cat);
          } else {
            show = (sectionCat === cat);
          }

          section.style.display = show ? '' : 'none';
        });
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------------------

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function escapeAttr(str) {
    return escapeHtml(str);
  }

  function relativeTime(iso) {
    if (!iso) return '';
    const then = new Date(iso);
    const now = new Date();
    const diffMin = Math.round((now - then) / 60000);
    if (diffMin < 60) return `${diffMin} 分鐘前`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr} 小時前`;
    const diffDay = Math.round(diffHr / 24);
    return `${diffDay} 天前`;
  }

  function formatTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleString('zh-TW', { hour: '2-digit', minute: '2-digit' });
  }

  // ---------------------------------------------------------------------------
  // Main
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // Edition switcher + Breaking News
  // ---------------------------------------------------------------------------

  function renderEditionSwitcher(editions, activeEdition) {
    const has = (k) => Boolean(editions[k]);
    const btn = (k, label) => {
      const disabled = !has(k);
      const active = (k === activeEdition);
      return `<button class="edition-tab${active ? ' active' : ''}${disabled ? ' disabled' : ''}"
              data-edition="${k}" ${disabled ? 'disabled' : ''}>${label}</button>`;
    };
    return `
      <div class="edition-switcher-wrap">
        <div class="edition-switcher">
          ${btn('morning', '☀️ 早報')}
          ${btn('evening', '🌙 晚報')}
        </div>
      </div>
    `;
  }

  function renderBreakingSection(issue, catMap) {
    const ids = Array.isArray(issue.breaking_news) ? issue.breaking_news : [];
    if (ids.length === 0) return '';
    const articles = ids
      .map(id => issue.articles.find(a => a.id === id))
      .filter(Boolean);
    if (articles.length === 0) return '';
    const articleHtml = articles.map(a => {
      const cat = catMap[a.category] || { name_zh: '', name_en: '' };
      return renderArticle(a, cat, { breaking: true });
    }).join('');
    return `
      <section class="category-section breaking-section" data-cat="all">
        <div class="category-title breaking-title">
          <h2>🚨 重大快訊</h2>
          <span class="count-label">Breaking News · ${articles.length} 則</span>
        </div>
        <div class="articles">${articleHtml}</div>
      </section>
    `;
  }

  function renderIssueInto(rootEl, issue) {
    const catMap = Object.fromEntries(issue.categories.map(c => [c.id, c]));
    const headlineArticle = issue.articles.find(a => a.is_headline);
    const headlineId = headlineArticle ? headlineArticle.id : null;

    const articlesByCategory = {};
    issue.categories.forEach(c => { articlesByCategory[c.id] = []; });
    issue.articles.forEach(a => {
      if (a.id === headlineId) return;
      if (articlesByCategory[a.category]) articlesByCategory[a.category].push(a);
    });

    let headlineSection = '';
    if (headlineArticle) {
      const headlineCat = catMap[headlineArticle.category] || { name_zh: '頭條', name_en: 'Headline' };
      const articleHtml = renderArticle(headlineArticle, headlineCat)
        .replace('class="article', `data-owner-cat="${headlineArticle.category}" class="article`);
      headlineSection = `
        <section class="category-section headline-section" data-cat="all">
          <div class="category-title">
            <h2>頭條</h2>
            <span class="count-label">Headline · ${escapeHtml(headlineCat.name_zh)}</span>
          </div>
          <div class="articles">${articleHtml}</div>
        </section>
      `;
    }

    const breakingSection = renderBreakingSection(issue, catMap);
    const sections = issue.categories
      .map(cat => renderCategorySection(cat, articlesByCategory[cat.id] || []))
      .join('');

    rootEl.innerHTML = `
      ${renderMasthead(issue)}
      ${renderTabs(issue)}
      <main>${breakingSection}${headlineSection}${sections}</main>
      ${renderFooter(issue)}
    `;

    qsa('.article', rootEl).forEach(el => {
      el.addEventListener('click', () => {
        const id = el.dataset.id;
        const article = issue.articles.find(a => a.id === id);
        const cat = catMap[article.category];
        openReader(article, cat);
      });
    });
    bindTabs();
    applyReactionStateAll();
    document.title = `第 ${issue.issue_number} 期 · ${issue.edition_label_zh || ''} · Daily Briefing`;
  }

  // ---------------------------------------------------------------------------
  // Main
  // ---------------------------------------------------------------------------

  async function main() {
    const { morning, evening, legacy, requestedEdition } = await loadAllEditions();

    if (!morning && !evening && !legacy) {
      renderError(new Error('No issue data available'));
      return;
    }

    // Legacy single-file: render as-is, no switcher.
    if (legacy && !morning && !evening) {
      renderIssueInto(qs('#app'), legacy);
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeReader();
      });
      return;
    }

    const editions = { morning, evening };

    // Pick default edition.
    let active;
    if (requestedEdition && editions[requestedEdition]) {
      active = requestedEdition;
    } else if (morning && evening) {
      const mt = new Date(morning.meta.generated_at).getTime();
      const et = new Date(evening.meta.generated_at).getTime();
      active = (et >= mt) ? 'evening' : 'morning';
    } else {
      active = morning ? 'morning' : 'evening';
    }

    const app = qs('#app');
    function show(editionKey) {
      const issue = editions[editionKey];
      if (!issue) return;
      app.innerHTML = renderEditionSwitcher(editions, editionKey)
        + '<div id="issue-root"></div>';
      renderIssueInto(qs('#issue-root'), issue);
      qsa('.edition-tab', app).forEach(btn => {
        btn.addEventListener('click', () => {
          const k = btn.dataset.edition;
          if (k === editionKey || !editions[k]) return;
          show(k);
        });
      });
    }
    show(active);

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeReader();
    });
  }

  document.addEventListener('DOMContentLoaded', main);
})();