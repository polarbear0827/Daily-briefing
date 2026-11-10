// =============================================================================
// Daily Briefing — Client-side renderer
// October reader template: Chinese UI; modern reader-editions with legacy fallback.
// =============================================================================

(function () {
  'use strict';

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---------------------------------------------------------------------------
  // Reader state — read markers / category affinity / theme (localStorage)
  // ---------------------------------------------------------------------------
  const READ_KEY = 'dailyBriefing.read.v1';
  const AFFINITY_KEY = 'dailyBriefing.affinity.v1';
  const THEME_KEY = 'dailyBriefing.theme.v1';
  function _loadJSON(k, dflt) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? dflt : v; } catch { return dflt; } }
  const readSet = new Set(_loadJSON(READ_KEY, []));
  function storageId(id) {
    const issue = window.__currentIssue || {};
    const dated = String(issue.issue_number || '').match(/^(\d{4}-\d{2}-\d{2})-(morning|midday|evening)$/);
    const date = issue.date || (dated && dated[1]) || new URLSearchParams(window.location.search).get('date') || window.EDITORIAL_REQUESTED_DATE;
    const edition = issue.edition || (dated && dated[2]) || window.__currentEdition;
    // Keep already-projected namespaces and their saved state byte-for-byte.
    if (date && edition && String(id).startsWith(`${date}-${edition}:`)) return id;
    // Unscoped historical identifiers remain readable; modern IDs never leak across issues.
    return date && edition ? JSON.stringify([date, edition, id]) : id;
  }
  function markRead(id) {
    if (!id || readSet.has(storageId(id))) return;
    readSet.add(storageId(id));
    localStorage.setItem(READ_KEY, JSON.stringify([...readSet]));
    const card = qs(`.article[data-id="${CSS.escape(id)}"]`);
    if (card) card.classList.add('is-read');
    updateOverviewRead();
  }
  function loadAffinity() { return _loadJSON(AFFINITY_KEY, {}); }
  function bumpAffinity(cat, delta) {
    if (!cat) return;
    const a = loadAffinity();
    a[cat] = Math.max(0, (a[cat] || 0) + delta);
    localStorage.setItem(AFFINITY_KEY, JSON.stringify(a));
  }
  // Dark mode — apply saved theme immediately (before render) to avoid flash.
  if (localStorage.getItem(THEME_KEY) === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
  function isDark() { return document.documentElement.getAttribute('data-theme') === 'dark'; }
  window.__toggleTheme = function () {
    const next = isDark() ? 'light' : 'dark';
    if (next === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
    localStorage.setItem(THEME_KEY, next);
    const b = qs('#theme-toggle'); if (b) b.textContent = next === 'dark' ? '☀️' : '🌙';
  };

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
    // URL param ?date=YYYY-MM-DD&edition=morning|midday|evening overrides
    const params = new URLSearchParams(window.location.search);
    const date = params.get('date') || window.EDITORIAL_REQUESTED_DATE;
    const editionParam = params.get('edition') || window.EDITORIAL_REQUESTED_EDITION;

    // Prefer the whole modern set; never mix missing slots with legacy aliases.
    const prefix = date ? `${encodeURIComponent(date)}-` : '';
    const [readerMorning, readerMidday, readerEvening] = await Promise.all([
      fetchJson(`data/reader-editions/${prefix}morning.json`),
      fetchJson(`data/reader-editions/${prefix}midday.json`),
      fetchJson(`data/reader-editions/${prefix}evening.json`),
    ]);
    if (readerMorning || readerMidday || readerEvening) {
      return { morning: readerMorning, midday: readerMidday, evening: readerEvening, legacy: null, requestedEdition: editionParam };
    }

    if (date) {
      const datePath = encodeURIComponent(date);
      const [morning, evening] = await Promise.all([
        fetchJson(`data/issues/${datePath}-morning.json`),
        fetchJson(`data/issues/${datePath}-evening.json`),
      ]);
      // Fallback to legacy single-file format if neither edition file exists.
      const legacy = (!morning && !evening) ? await fetchJson(`data/issues/${datePath}.json`) : null;
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
          載入失敗
        </h1>
        <p style="color:var(--ink-muted);">${escapeHtml(err.message)}</p>
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
      const hasName = Boolean(location.location_zh);
      const hasTemp = Number.isFinite(Number(location.temp_c));
      const hasCondition = Boolean(location.condition_zh);
      return hasName && hasTemp && hasCondition;
    });

    if (locations.length === 0) {
      return '<div class="weather-empty">天氣資料暫缺</div>';
    }

    return `
      <div class="weather-list">
        ${locations.map(location => {
          const name = location.location_zh || '天氣';
          const temp = `${Math.round(Number(location.temp_c))}°C`;
          const condition = location.condition_zh || '資料暫缺';
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

  function issueLabel(issue) {
    if (issue.issue_label_zh) return String(issue.issue_label_zh);
    const value = String(issue.issue_number == null ? '' : issue.issue_number);
    const dated = value.match(/^(\d{4})-(\d{2})-(\d{2})-(morning|midday|evening)$/);
    if (dated) return `${dated[1]}年${Number(dated[2])}月${Number(dated[3])}日${{ morning: '早報', midday: '中報', evening: '晚報' }[dated[4]]}`;
    return /^\d+$/.test(value) ? `第 ${value} 期` : value;
  }

  function renderMasthead(issue) {
    const notice = [
      issue.trial ? (issue.trial_label_zh || '試刊') : '',
      (issue.trial || issue.coverage_note_zh) && issue.window_start && issue.window_end
        ? `收錄窗口：${issue.window_start} 至 ${issue.window_end}（不含終點）` : '',
      issue.coverage_note_zh || '',
    ].filter(Boolean).join(' · ');
    return `
      <header class="masthead">
        <div class="masthead-meta">
          <div class="issue-number">${escapeHtml(issueLabel(issue))}</div>
          <div class="weather">
            ${renderWeather(issue)}
          </div>
          <div class="date">${escapeHtml(issue.date_display_zh)}</div>
        </div>
        <h1 class="masthead-title">Daily Briefing</h1>
        <p class="masthead-tagline">
          ${escapeHtml(issue.tagline_zh)}
          ${notice ? `<br>${escapeHtml(notice)}` : ''}
        </p>
        <p class="masthead-cross">
          <a href="https://polarbear0827.github.io/My-Way/" style="color:inherit;text-decoration:none;border:1px solid currentColor;border-radius:999px;padding:.3rem 1rem;font-size:.85rem;opacity:.85;">
            穗稻忠武的專欄 · 人工智慧雷達 · 播客 →
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
        `<button class="tab" data-cat="${escapeAttr(cat.id)}">${escapeHtml(cat.name_zh)}<span class="count">${c}</span></button>`
      );
    });

    return `<div class="tabs-wrap"><div class="tabs">${items.join('')}</div></div>`;
  }

  function renderCategorySection(cat, articles) {
    if (articles.length === 0) return '';
    const articleHtml = articles.map(a => renderArticle(a, cat)).join('');
    return `
      <section class="category-section" data-cat="${escapeAttr(cat.id)}">
        <div class="category-title">
          <h2>${escapeHtml(cat.name_zh)}</h2>
          <span class="count-label">${articles.length} 則</span>
        </div>
        <div class="articles">${articleHtml}</div>
      </section>
    `;
  }

  function renderPhotoCredit(article, full = false) {
    if (!article.image_url) return '';
    let sourceUrl;
    try {
      const image = new URL(article.image_url);
      const source = new URL(article.image_source_url || article.source.url);
      if (image.protocol !== 'https:' || source.protocol !== 'https:' || image.username || image.password || source.username || source.password) return '';
      sourceUrl = article.image_source_url || article.source.url;
    } catch { return ''; }
    const credit = (article.image_credit || '').replace(/Image source,\s*/g, '');
    let licenseLink = '';
    try {
      const license = new URL(article.image_license_url);
      if (license.protocol === 'https:' && !license.username && !license.password) licenseLink = ` · <a href="${escapeAttr(article.image_license_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">授權條款</a>`;
    } catch {}
    if (!credit && !licenseLink) return '';
    const outlet = article.image_source_name || (credit.includes(' · ') ? credit.split(' · ')[0] : article.source.name);
    const label = full ? (credit || outlet) : outlet;
    const archive = /^資料照片|檔案照片/.test(article.image_caption || '') || /檔案照片|資料照/.test(credit) ? '（資料照）' : '';
    const link = `<a href="${escapeAttr(sourceUrl)}" title="${escapeAttr(credit)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">圖片：${escapeHtml(label)}${archive}</a>${licenseLink}`;
    return full ? `<p class="reader-evidence-note">${link}</p>` : `<small class="article-photo-credit article-source">${link}</small>`;
  }

  function renderPhoto(article) {
    if (!article.image_url) return '';
    let imageUrl, sourceUrl;
    try {
      const image = new URL(article.image_url);
      const source = new URL(article.image_source_url || article.source.url);
      if (image.protocol !== 'https:' || source.protocol !== 'https:' || image.username || image.password || source.username || source.password) return '';
      imageUrl = article.image_url; sourceUrl = article.image_source_url || article.source.url;
    } catch { return ''; }
    const caption = article.image_caption || '';
    const y = article.image_focus_y ?? (article.is_headline && article.image_publication_basis === 'user-directed-source-reference' ? 0 : undefined);
    const focus = typeof y === 'number' && Number.isFinite(y) && y >= 0 && y <= 100 ? ` style="object-position:50% ${y}%"` : '';
    return `<div class="article-photo"><div class="article-media"><img src="${escapeAttr(imageUrl)}" alt="${escapeAttr(article.image_alt || caption || '來源新聞配圖')}"${focus} loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.article-photo').remove()"></div>${renderPhotoCredit(article)}</div>`;
  }

  function renderArticle(article, cat, opts) {
    opts = opts || {};
    const headlineClass = article.is_headline ? ' is-headline' : '';
    const breakingClass = opts.breaking ? ' is-breaking' : '';
    const kickerLabel = opts.breaking
      ? '🚨 重大快訊'
      : (article.is_headline ? '頭條' : escapeHtml(cat.name_zh));
    const bulletsArr = Array.isArray(article.bullets_zh) ? article.bullets_zh.filter(b => b && String(b).trim()) : [];
    const bulletsHtml = bulletsArr.length
      ? `<ul class="article-bullets">${bulletsArr.map(b => `<li>${escapeHtml(b)}</li>`).join('')}</ul>`
      : '';
    const readingTime = article.source.reading_time_min || 3;
    const publishedAgo = relativeTime(article.source.published_at);
    const imageHtml = renderPhoto(article);
    const pulseHtml = article.community_pulse
      ? `<div class="article-pulse"><span class="pulse-label">💬 社群觀察</span><p>${escapeHtml(article.community_pulse)}</p></div>`
      : '';
    // 產業標籤 chip(垂直導覽);不重複顯示等於本身分類的標籤(robotics 分類就不再掛「機器人」)
    const TAG_EMOJI = { '機器人': '🤖', '金融': '💰', '製造': '🏭', '零售': '🛒' };
    const CAT_OWN_TAG = { robotics: '機器人' };
    const tagsArr = (Array.isArray(article.industry_tags) ? article.industry_tags : [])
      .filter(t => CAT_OWN_TAG[article.category] !== t);
    const tagsHtml = tagsArr.length
      ? `<div class="article-tags">${tagsArr.map(t => `<span class="itag">${TAG_EMOJI[t] || ''} ${escapeHtml(t)}</span>`).join('')}</div>`
      : '';

    return `
      <article class="article${headlineClass}${breakingClass}${readSet.has(storageId(article.id)) ? ' is-read' : ''}" data-id="${escapeAttr(article.id)}">
        ${imageHtml}
        <div class="article-kicker">
          <span class="kicker-badge">${kickerLabel}</span>
          ${article.is_teaching_material ? '<span class="teaching-badge" title="教學素材：適合企業學員上手的 AI 工具或概念">🎓 教學素材</span>' : ''}
        </div>
        <h3 class="article-title">${escapeHtml(article.title_zh)}</h3>
        <p class="article-summary">${escapeHtml(article.lede_zh)}</p>
        ${tagsHtml}
        <span class="article-more">閱讀摘要 →</span>
        <div class="article-footer">
          <span class="article-source">${escapeHtml(article.source.name)}</span>
          <span class="dot">·</span>
          <span>${publishedAgo}</span>
          <span class="dot">·</span>
          <span>${readingTime} 分鐘</span>
          <div class="article-actions">
            <a href="${escapeAttr(article.source.url)}" target="_blank" rel="noopener"
               onclick="event.stopPropagation()">原文來源</a>
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
    const originalId = wrap.dataset.articleId;
    const articleId = storageId(originalId);
    const reaction = btn.dataset.reaction;
    const map = loadReactions();
    map[articleId] = map[articleId] || {};
    map[articleId][reaction] = !map[articleId][reaction];
    if (!map[articleId][reaction]) delete map[articleId][reaction];
    if (Object.keys(map[articleId]).length === 0) delete map[articleId];
    saveReactions(map);
    // 把互動回饋累積成分類 affinity → 下次載入依此把喜歡的分類往上排
    const _art = ((window.__currentIssue && window.__currentIssue.articles) || []).find(a => a.id === originalId);
    if (_art) bumpAffinity(_art.category, (map[articleId] && map[articleId][reaction]) ? 1 : -1);
    btn.classList.toggle('is-active', !!(map[articleId] && map[articleId][reaction]));
  };

  function applyReactionStateAll() {
    const map = loadReactions();
    qsa('.article-reactions').forEach(wrap => {
      const id = wrap.dataset.articleId;
      const state = map[storageId(id)] || {};
      qsa('.reaction-btn', wrap).forEach(b => {
        b.classList.toggle('is-active', !!state[b.dataset.reaction]);
      });
    });
  }

  function renderFooter(issue) {
    return `
      <footer class="site-footer">
        <div>${escapeHtml(issueLabel(issue))} · 共 ${issue.meta.total_articles} 則 · 生成時間 ${formatTime(issue.meta.generated_at)}</div>
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
    markRead(article.id);
    // Analytics(GoatCounter):記一次文章開啟事件 → 看哪幾則被讀最多
    try {
      if (window.goatcounter && typeof window.goatcounter.count === 'function') {
        window.goatcounter.count({ path: 'article/' + article.id, title: article.title_zh, event: true });
      }
    } catch (e) { /* analytics 不可阻斷閱讀 */ }
    const navList = window.__navList || [];
    const _idx = navList.findIndex(a => a.id === article.id);
    const bzArr = Array.isArray(article.bullets_zh) ? article.bullets_zh.filter(b => b && String(b).trim()) : [];
    const bulletsZh = bzArr.map(b => `<li>${escapeHtml(b)}</li>`).join('');

    overlay.innerHTML = `
      <div class="reader-paper" data-lang="zh">
        <button class="reader-close" aria-label="關閉">×</button>

        <div class="reader-kicker">${escapeHtml(cat.name_zh)}</div>

        <h1 class="reader-title zh-only">${escapeHtml(article.title_zh)}</h1>


        <p class="reader-lede zh-only">${escapeHtml(article.lede_zh)}</p>

        ${article.is_teaching_material && article.teaching_takeaway ? `
        <div class="reader-takeaway">
          <div class="reader-takeaway-label">🎓 教學素材 · 知識點</div>
          <p class="reader-takeaway-text">${escapeHtml(article.teaching_takeaway)}</p>
        </div>` : ''}

        <ul class="reader-bullets zh-only"${bulletsZh ? '' : ' style="display:none;"'}>${bulletsZh}</ul>

        ${article.editorial_analysis_zh ? `<aside class="reader-takeaway reader-editorial-analysis">
          <div class="reader-takeaway-label">編輯觀察</div>
          <p class="reader-takeaway-text">${escapeHtml(article.editorial_analysis_zh)}</p>
        </aside>` : ''}

        ${article.community_pulse ? `<div class="reader-pulse">
          <div class="reader-pulse-label">💬 社群觀察</div>
          <p>${escapeHtml(article.community_pulse)}</p>
        </div>` : ''}

        <div class="reader-source-block">
          <div class="label">原文來源</div>
          <p class="reader-evidence-note">${escapeHtml(article.source_evidence_note_zh || article.evidence_note_zh || '本文為來源摘要整理，未取得的原文內容不作全文轉述；請參閱原文核對。')}</p>
          <a href="${escapeAttr(article.source.url)}" target="_blank" rel="noopener">
            ${escapeHtml(article.source.name)} · ${escapeHtml(new URL(article.source.url).hostname)}
          </a>
          ${(Array.isArray(article.supporting_sources) ? article.supporting_sources : []).filter(s => s && /^https?:\/\//i.test(s.source_url || '')).map(s => `
          <p><a href="${escapeAttr(s.source_url)}" target="_blank" rel="noopener">${escapeHtml(s.source_name || '延伸來源')}</a></p>`).join('')}
          ${renderPhotoCredit(article, true)}
        </div>

        <div class="reader-nav">
          <button class="reader-prev" ${_idx <= 0 ? 'disabled' : ''}>‹ 上一則</button>
          <span class="reader-navpos">${_idx >= 0 ? (_idx + 1) + ' / ' + navList.length : ''}</span>
          <button class="reader-next" ${_idx < 0 || _idx >= navList.length - 1 ? 'disabled' : ''}>下一則 ›</button>
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

    // Prev / next navigation (keeps reading flow without closing)
    const gotoReader = (j) => {
      const a = navList[j];
      if (a) openReader(a, (window.__catMap || {})[a.category] || { name_zh: '', name_en: '' });
    };
    const _pv = qs('.reader-prev', overlay);
    if (_pv) _pv.addEventListener('click', (e) => { e.stopPropagation(); gotoReader(_idx - 1); });
    const _nx = qs('.reader-next', overlay);
    if (_nx) _nx.addEventListener('click', (e) => { e.stopPropagation(); gotoReader(_idx + 1); });
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
          ${btn('midday', '中報')}
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
          <span class="count-label">${articles.length} 則</span>
        </div>
        <div class="articles">${articleHtml}</div>
      </section>
    `;
  }

  function renderOverview(issue) {
    const arts = issue.articles || [];
    const total = arts.length;
    const mins = arts.reduce((s, a) => s + ((a.source && a.source.reading_time_min) || 3), 0);
    const breaking = (issue.breaking_news || []).length;
    const read = arts.filter(a => readSet.has(storageId(a.id))).length;
    const personalized = Object.keys(loadAffinity()).length > 0;
    return `<div class="overview-bar" id="overview">
      <span class="ov-item"><b>${total}</b> 則</span><span class="ov-sep">·</span>
      <span class="ov-item">約 <b>${mins}</b> 分鐘讀完</span>
      ${breaking ? `<span class="ov-sep">·</span><span class="ov-item ov-breaking">🚨 ${breaking} 則重大快訊</span>` : ''}
      <span class="ov-sep">·</span>
      <span class="ov-item ov-read">已讀 <b id="ov-read-n">${read}</b>/${total}</span>
      ${personalized ? '<span class="ov-sep">·</span><span class="ov-item ov-personalized" title="已依你按過的興趣把分類往上排">🎯 為你排序</span>' : ''}
      <button id="theme-toggle" class="theme-toggle" title="深色 / 淺色" onclick="window.__toggleTheme()">${isDark() ? '☀️' : '🌙'}</button>
    </div>`;
  }
  function updateOverviewRead() {
    const el = qs('#ov-read-n'); const issue = window.__currentIssue;
    if (el && issue) el.textContent = (issue.articles || []).filter(a => readSet.has(storageId(a.id))).length;
  }

  function renderIssueInto(rootEl, issue) {
    const catMap = Object.fromEntries(issue.categories.map(c => [c.id, c]));
    window.__currentIssue = issue;
    window.__catMap = catMap;
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
        .replace('class="article', `data-owner-cat="${escapeAttr(headlineArticle.category)}" class="article`);
      headlineSection = `
        <section class="category-section headline-section" data-cat="all">
          <div class="category-title">
            <h2>頭條</h2>
            <span class="count-label">${escapeHtml(headlineCat.name_zh)}</span>
          </div>
          <div class="articles">${articleHtml}</div>
        </section>
      `;
    }

    const breakingSection = renderBreakingSection(issue, catMap);
    // reactions 推薦:依 affinity 把你常按興趣的分類往上排(頭條/快訊不動)
    const aff = loadAffinity();
    const orderedCats = [...issue.categories].sort((a, b) => (aff[b.id] || 0) - (aff[a.id] || 0));
    const sections = orderedCats
      .map(cat => renderCategorySection(cat, articlesByCategory[cat.id] || []))
      .join('');
    // reader 上一則/下一則的閱讀順序(頭條 → 各分類)
    const navList = [];
    if (headlineArticle) navList.push(headlineArticle);
    orderedCats.forEach(c => (articlesByCategory[c.id] || []).forEach(a => navList.push(a)));
    window.__navList = navList;

    rootEl.innerHTML = `
      ${renderMasthead(issue)}
      ${renderTabs(issue)}
      ${renderOverview(issue)}
      <main>${breakingSection}${headlineSection}${sections}</main>
      ${renderFooter(issue)}
    `;

    qsa('.article', rootEl).forEach(el => {
      el.addEventListener('click', () => {
        const id = el.dataset.id;
        markRead(id);
        const article = issue.articles.find(a => a.id === id);
        openReader(article, catMap[article.category] || { name_zh: '', name_en: '' });
      });
    });
    bindTabs();
    applyReactionStateAll();
    document.title = `${issueLabel(issue)} · ${issue.edition_label_zh || ''} · Daily Briefing`;
  }

  // ---------------------------------------------------------------------------
  // Main
  // ---------------------------------------------------------------------------

  async function main() {
    const { morning, midday, evening, legacy, requestedEdition } = await loadAllEditions();
    if (requestedEdition && (!['morning', 'midday', 'evening'].includes(requestedEdition)
        || !({ morning, midday, evening })[requestedEdition])) {
      renderError(new Error('指定的報期不存在或尚未提供'));
      return;
    }

    if (!morning && !midday && !evening && !legacy) {
      renderError(new Error('暫無可讀的報期資料'));
      return;
    }

    // Legacy single-file: render as-is, no switcher.
    if (legacy && !morning && !midday && !evening) {
      renderIssueInto(qs('#app'), legacy);
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeReader();
      });
      return;
    }

    const editions = { morning, midday, evening };

    // Pick default edition.
    let active;
    if (requestedEdition && editions[requestedEdition]) {
      active = requestedEdition;
    } else {
      const timestamp = key => Date.parse(editions[key].publication?.published_at || editions[key].trial_staged_at || editions[key].meta?.generated_at) || 0;
      active = Object.keys(editions).filter(k => editions[k] && timestamp(k) <= Date.now()).reduce((latest, k) => {
        return !latest || timestamp(k) >= timestamp(latest) ? k : latest;
      }, null);
    }

    const app = qs('#app');
    function show(editionKey, push = false) {
      const issue = editions[editionKey];
      if (!issue) return;
      closeReader();
      if (push) {
        const params = new URLSearchParams(window.location.search);
        if (!params.has('date') && window.EDITORIAL_REQUESTED_DATE) params.set('date', window.EDITORIAL_REQUESTED_DATE);
        params.set('edition', editionKey);
        window.history.pushState(null, '', `${window.location.pathname}?${params}${window.location.hash || ''}`);
      }
      app.innerHTML = renderEditionSwitcher(editions, editionKey)
        + '<div id="issue-root"></div>';
      window.__currentEdition = editionKey;
      renderIssueInto(qs('#issue-root'), issue);
      qsa('.edition-tab', app).forEach(btn => {
        btn.addEventListener('click', () => {
          const k = btn.dataset.edition;
          if (k === editionKey || !editions[k]) return;
          show(k, true);
        });
      });
    }
    show(active);
    window.addEventListener('popstate', () => {
      const key = new URLSearchParams(window.location.search).get('edition') || active;
      closeReader();
      if (!['morning', 'midday', 'evening'].includes(key) || !editions[key]) {
        renderError(new Error('指定的報期不存在或尚未提供'));
        return;
      }
      show(key);
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeReader();
    });
  }

  document.addEventListener('DOMContentLoaded', main);
})();
