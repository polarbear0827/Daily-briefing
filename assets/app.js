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

  async function loadIssue() {
    // URL param ?date=YYYY-MM-DD overrides latest
    const params = new URLSearchParams(window.location.search);
    const date = params.get('date');
    const path = date ? `data/issues/${date}.json` : 'data/latest.json';

    try {
      const res = await fetch(path);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('Failed to load issue:', err);
      renderError(err);
      return null;
    }
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

  function renderArticle(article, cat) {
    const headlineClass = article.is_headline ? ' is-headline' : '';
    const kickerLabel = article.is_headline ? '頭條 · Headline' : escapeHtml(cat.name_zh);
    const bullets = (article.bullets_zh || []).map(b => `<li>${escapeHtml(b)}</li>`).join('');
    const readingTime = article.source.reading_time_min || 3;
    const publishedAgo = relativeTime(article.source.published_at);

    return `
      <article class="article${headlineClass}" data-id="${article.id}">
        <div class="article-kicker">
          <span class="kicker-badge">${kickerLabel}</span>
        </div>
        <h3 class="article-title">${escapeHtml(article.title_zh)}</h3>
        <p class="article-title-en">${escapeHtml(article.title_en)}</p>
        <p class="article-lede">${escapeHtml(article.lede_zh)}</p>
        <ul class="article-bullets">${bullets}</ul>
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
        </div>
      </article>
    `;
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
    const bulletsZh = (article.bullets_zh || []).map(b => `<li>${escapeHtml(b)}</li>`).join('');
    const bulletsEn = (article.bullets_en || []).map(b => `<li>${escapeHtml(b)}</li>`).join('');

    overlay.innerHTML = `
      <div class="reader-paper" data-lang="both">
        <button class="reader-close" aria-label="Close">×</button>
        <div class="reader-lang-toggle">
          <button data-lang="zh">中文</button>
          <button data-lang="both" class="active">中英對照</button>
          <button data-lang="en">EN</button>
        </div>

        <div class="reader-kicker">${escapeHtml(cat.name_zh)} · ${escapeHtml(cat.name_en)}</div>

        <h1 class="reader-title zh-only">${escapeHtml(article.title_zh)}</h1>
        <h1 class="reader-title en-only" style="font-family:var(--font-display-en);font-style:italic;">${escapeHtml(article.title_en)}</h1>
        <p class="reader-title-en en-only" style="display:none;"></p>
        <p class="reader-title-en zh-only" style="display:none;"></p>

        <p class="reader-lede zh-only">${escapeHtml(article.lede_zh)}</p>
        <p class="reader-lede en-only" style="font-family:var(--font-body-en);">${escapeHtml(article.lede_en)}</p>

        <ul class="reader-bullets zh-only">${bulletsZh}</ul>
        <ul class="reader-bullets en-only">${bulletsEn}</ul>

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

  async function main() {
    const issue = await loadIssue();
    if (!issue) return;

    const catMap = Object.fromEntries(issue.categories.map(c => [c.id, c]));

    // Separate the headline from the category flow.
    // Headline article is rendered in its own top-level section,
    // and is NOT duplicated inside its category section below.
    const headlineArticle = issue.articles.find(a => a.is_headline);
    const headlineId = headlineArticle ? headlineArticle.id : null;

    const articlesByCategory = {};
    issue.categories.forEach(c => { articlesByCategory[c.id] = []; });
    issue.articles.forEach(a => {
      if (a.id === headlineId) return;  // skip headline in category flow
      if (articlesByCategory[a.category]) {
        articlesByCategory[a.category].push(a);
      }
    });

    // Headline section — single-article section at the top
    let headlineSection = '';
    if (headlineArticle) {
      const headlineCat = catMap[headlineArticle.category] || { name_zh: '頭條', name_en: 'Headline' };
      // Render article, then inject data-owner-cat for tab-filter logic
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

    const sections = issue.categories
      .map(cat => renderCategorySection(cat, articlesByCategory[cat.id] || []))
      .join('');

    qs('#app').innerHTML = `
      ${renderMasthead(issue)}
      ${renderTabs(issue)}
      <main>${headlineSection}${sections}</main>
      ${renderFooter(issue)}
    `;

    // Wire up article clicks
    qsa('.article').forEach(el => {
      el.addEventListener('click', () => {
        const id = el.dataset.id;
        const article = issue.articles.find(a => a.id === id);
        const cat = catMap[article.category];
        openReader(article, cat);
      });
    });

    bindTabs();

    // ESC to close reader
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeReader();
    });

    // Update page title
    document.title = `第 ${issue.issue_number} 期 · Daily Briefing`;
  }

  document.addEventListener('DOMContentLoaded', main);
})();