// Mango KOL database -- vanilla JS frontend. No build step: this file is
// served as-is by FastAPI's StaticFiles mount. State lives in `state`;
// every mutation re-renders the relevant section explicitly (no framework).

const STRATEGIC_CLASSES = ["Top KOL", "Community Leader", "KOL", "Media / Community Account"];
const DISTRIBUTION_CLASSES = ["KOC", "Marketing Account"]; // matches backend models.DISTRIBUTION_CLASSES
const ALL_CLASSES = STRATEGIC_CLASSES.concat(DISTRIBUTION_CLASSES, ["Unknown"]);
const TAB_LABELS = { strategic: "Strategic KOLs", distribution: "KOC / Distribution", needs_review: "Needs Review" };

// Small monochrome platform marks via Simple Icons CDN (?viewbox trimmed,
// tinted to the neutral text-muted color so the table stays restrained).
const PLATFORM_ICON_SLUGS = {
  X: "x",
  YouTube: "youtube",
  Instagram: "instagram",
  TikTok: "tiktok",
  LinkedIn: "linkedin",
  Threads: "threads",
  Facebook: "facebook",
};
function platformIconUrl(platform) {
  const slug = PLATFORM_ICON_SLUGS[platform];
  return slug ? `https://cdn.simpleicons.org/${slug}/9c9d97` : null;
}

const state = {
  section: "home",
  opp: {
    filters: { priority: new Set(), category: new Set(), geography: new Set(), spend_evidence_level: new Set() },
    search: "",
    sort: "priority",
    order: "desc",
    page: 1,
    pageSize: 50,
    meta: { categories: [], geographies: [], spend_evidence_levels: [], priority_counts: {} },
    rows: [],
    total: 0,
  },
  tab: "strategic",
  search: "",
  filters: {
    creator_class: new Set(),
    platform: new Set(),
    region: new Set(),
    language: new Set(),
    promotion_level: new Set(),
    category: null,
    contact: null,
    followers_min: null,
    followers_max: null,
    avg_views_min: null,
    avg_views_max: null,
    engagement_min: null,
    engagement_max: null,
    quote_min: null,
    quote_max: null,
  },
  sort: "followers",
  order: "desc",
  page: 1,
  pageSize: 50,
  meta: { creator_classes: [], promotion_levels: [], platforms: [], regions: [], languages: [], categories: [], tab_counts: {} },
  rows: [],
  total: 0,
  shortlistId: localStorage.getItem("mango_kol_shortlist_id") ? Number(localStorage.getItem("mango_kol_shortlist_id")) : null,
  shortlistCreatorIds: new Set(),
  status: { rapid_x_configured: true, youtube_configured: true, scrapecreators_configured: true, openai_configured: true },
  selectedRowIds: new Set(), // bulk-selection checkboxes, independent of the shortlist
  hiddenColumns: new Set(JSON.parse(localStorage.getItem("mango_kol_hidden_cols") || "[]")),
};

const TOGGLEABLE_COLUMNS = [
  { key: "platform", label: "Platform" },
  { key: "category", label: "Category" },
  { key: "region", label: "Region / Lang" },
  { key: "avg_views", label: "Avg views" },
  { key: "engagement", label: "Engagement" },
  { key: "cpm", label: "Est. CPM" },
  { key: "contact", label: "Contact" },
];

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail);
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res.text();
}

function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

function fmtNum(n) {
  if (n === null || n === undefined) return "-";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n));
}

function fmtUsd(n) {
  if (n === null || n === undefined) return "-";
  return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function classTier(cls) {
  if (STRATEGIC_CLASSES.includes(cls)) return "tier-strategic";
  if (DISTRIBUTION_CLASSES.includes(cls)) return "tier-distribution";
  return "tier-review";
}

function classLabel(cls) {
  return cls === "Unknown" ? "Needs Review" : cls;
}

function initials(name) {
  const parts = (name || "?").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Broken/expired avatar URLs (common for stale profile images) fall back to
// initials via a real DOM swap on error, not inline-HTML string concatenation
// -- avoids quote-collision bugs from nesting one HTML attribute inside another.
window.__onAvatarError = function (img, name, sizeClass) {
  const span = document.createElement("span");
  span.className = `avatar-fallback ${sizeClass}`;
  span.textContent = initials(name);
  img.replaceWith(span);
};

function avatarHtml(name, url, sizeClass = "") {
  if (url) {
    // JSON.stringify gives valid JS string literals (double-quoted); escapeAttr
    // then HTML-entity-encodes those quotes so they survive sitting inside the
    // onerror="..." HTML attribute without prematurely closing it.
    const nameArg = escapeAttr(JSON.stringify(name || ""));
    const sizeArg = escapeAttr(JSON.stringify(sizeClass));
    return `<img class="avatar ${sizeClass}" src="${escapeAttr(url)}" alt="" loading="lazy" onerror="window.__onAvatarError(this, ${nameArg}, ${sizeArg})" />`;
  }
  return avatarFallbackHtml(name, sizeClass);
}
function avatarFallbackHtml(name, sizeClass = "") {
  return `<span class="avatar-fallback ${sizeClass}">${escapeHtml(initials(name))}</span>`;
}

function platformBadgeHtml(platform, platformRaw) {
  if (!platform || platform === "Unknown") {
    return `<span class="platform-badge unresolved" title="${platformRaw ? escapeAttr("Originally: " + platformRaw) : "Not specified in source sheet"}">Unspecified</span>`;
  }
  const icon = platformIconUrl(platform);
  const title = platformRaw ? ` title="${escapeAttr("Originally listed as: " + platformRaw)}"` : "";
  // onerror hides a broken CDN icon instead of showing the browser's
  // broken-image glyph -- the text label still identifies the platform.
  const iconHtml = icon ? `<img src="${icon}" alt="" onerror="this.style.display='none'" />` : "";
  return `<span class="platform-badge"${title}>${iconHtml}${escapeHtml(platform)}</span>`;
}

// ---------------------------------------------------------------------------
// Filters panel (compact collapsible checkbox groups)
// ---------------------------------------------------------------------------

async function loadMeta() {
  state.meta = await api("/api/meta/filters");
  renderTabCounts();
  renderFilters();
}

function renderTabCounts() {
  const counts = state.meta.tab_counts || {};
  document.getElementById("countStrategic").textContent = counts.strategic !== undefined ? ` (${counts.strategic})` : "";
  document.getElementById("countDistribution").textContent = counts.distribution !== undefined ? ` (${counts.distribution})` : "";
  document.getElementById("countNeedsReview").textContent = counts.needs_review !== undefined ? ` (${counts.needs_review})` : "";
}

function checkboxGroup(title, key, options, current, open = false) {
  if (!options.length) {
    return `<details class="filter-group"><summary>${title}</summary><div class="filter-options"><div class="filter-empty">No data yet</div></div></details>`;
  }
  const rows = options
    .map((opt) => {
      const checked = current.has(opt) ? "checked" : "";
      const activeCls = current.has(opt) ? "active-label" : "";
      return `<label class="filter-check ${activeCls}"><input type="checkbox" data-filter-key="${key}" data-filter-value="${escapeAttr(opt)}" ${checked} />${escapeHtml(opt === "Unknown" ? "Needs Review" : opt)}</label>`;
    })
    .join("");
  const hasActive = current.size > 0;
  return `<details class="filter-group" ${open || hasActive ? "open" : ""}><summary>${title}</summary><div class="filter-options">${rows}</div></details>`;
}

function radioGroup(title, key, options, current, open = false) {
  if (!options.length) return "";
  const rows = options
    .map((opt) => {
      const checked = current === opt ? "checked" : "";
      const activeCls = current === opt ? "active-label" : "";
      return `<label class="filter-check ${activeCls}"><input type="radio" name="single-${key}" data-single-key="${key}" data-single-value="${escapeAttr(opt)}" ${checked} />${escapeHtml(opt)}</label>`;
    })
    .join("");
  return `<details class="filter-group" ${open || current ? "open" : ""}><summary>${title}</summary><div class="filter-options">${rows}<label class="filter-check"><input type="radio" name="single-${key}" data-single-key="${key}" data-single-value="" ${!current ? "checked" : ""} />Any</label></div></details>`;
}

function rangeGroup(title, minKey, maxKey) {
  const hasActive = state.filters[minKey] !== null || state.filters[maxKey] !== null;
  return `
    <details class="filter-group" ${hasActive ? "open" : ""}>
      <summary>${title}</summary>
      <div class="range-row">
        <input type="number" placeholder="min" data-range-key="${minKey}" value="${state.filters[minKey] ?? ""}" />
        <span class="range-sep">to</span>
        <input type="number" placeholder="max" data-range-key="${maxKey}" value="${state.filters[maxKey] ?? ""}" />
      </div>
    </details>`;
}

function renderFilters() {
  const classOptions = (
    state.tab === "strategic" ? STRATEGIC_CLASSES : state.tab === "distribution" ? DISTRIBUTION_CLASSES : ["Unknown"]
  ).filter((c) => state.meta.creator_classes.includes(c));
  const body = document.getElementById("filterBody");
  const groups = [
    state.tab !== "needs_review" ? checkboxGroup("Creator class", "creator_class", classOptions, state.filters.creator_class, true) : "",
    checkboxGroup("Platform", "platform", state.meta.platforms, state.filters.platform, true),
    checkboxGroup("Promotion level", "promotion_level", state.meta.promotion_levels, state.filters.promotion_level),
    checkboxGroup("Region", "region", state.meta.regions, state.filters.region),
    checkboxGroup("Language", "language", state.meta.languages, state.filters.language),
    radioGroup("Category", "category", state.meta.categories, state.filters.category),
    rangeGroup("Followers", "followers_min", "followers_max"),
    rangeGroup("Avg views", "avg_views_min", "avg_views_max"),
    rangeGroup("Engagement rate (%)", "engagement_min", "engagement_max"),
    rangeGroup("Quote (USD)", "quote_min", "quote_max"),
    radioGroup("Contact on file", "contact", ["email", "telegram"], state.filters.contact),
  ];
  body.innerHTML = groups.join("");

  body.querySelectorAll("[data-filter-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.filterKey;
      const value = input.dataset.filterValue;
      const set = state.filters[key];
      if (input.checked) set.add(value);
      else set.delete(value);
      state.page = 1;
      renderFilters();
      loadCreators();
    });
  });
  body.querySelectorAll("[data-single-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.singleKey;
      const value = input.dataset.singleValue;
      state.filters[key] = value || null;
      state.page = 1;
      renderFilters();
      loadCreators();
    });
  });
  body.querySelectorAll("[data-range-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.rangeKey;
      state.filters[key] = input.value === "" ? null : Number(input.value);
      state.page = 1;
      loadCreators();
    });
  });
}

document.getElementById("clearFilters").addEventListener("click", () => {
  state.filters = {
    creator_class: new Set(),
    platform: new Set(),
    region: new Set(),
    language: new Set(),
    promotion_level: new Set(),
    category: null,
    contact: null,
    followers_min: null,
    followers_max: null,
    avg_views_min: null,
    avg_views_max: null,
    engagement_min: null,
    engagement_max: null,
    quote_min: null,
    quote_max: null,
  };
  state.search = "";
  document.getElementById("search").value = "";
  state.page = 1;
  renderFilters();
  loadCreators();
});

// ---------------------------------------------------------------------------
// Directory: query building + fetch + render
// ---------------------------------------------------------------------------

function buildQuery() {
  const p = new URLSearchParams();
  p.set("tab", state.tab);
  p.set("sort", state.sort);
  p.set("order", state.order);
  p.set("page", state.page);
  p.set("page_size", state.pageSize);
  if (state.search) p.set("search", state.search);
  const f = state.filters;
  f.creator_class.forEach((v) => p.append("creator_class", v));
  f.platform.forEach((v) => p.append("platform", v));
  f.region.forEach((v) => p.append("region", v));
  f.language.forEach((v) => p.append("language", v));
  f.promotion_level.forEach((v) => p.append("promotion_level", v));
  if (f.category) p.set("category", f.category);
  if (f.contact) p.set("contact", f.contact);
  ["followers_min", "followers_max", "avg_views_min", "avg_views_max", "engagement_min", "engagement_max", "quote_min", "quote_max"].forEach(
    (k) => {
      if (f[k] !== null && f[k] !== undefined) p.set(k, f[k]);
    }
  );
  return p.toString();
}

function renderSkeletonRows(n = 8) {
  const tbody = document.getElementById("creatorRows");
  const cols = 14;
  tbody.innerHTML = Array.from({ length: n })
    .map(
      () =>
        `<tr class="skeleton-row">${Array.from({ length: cols })
          .map(() => `<td><div class="skeleton-bar" style="width:${40 + Math.random() * 50}%"></div></td>`)
          .join("")}</tr>`
    )
    .join("");
}

let loadSeq = 0;
async function loadCreators() {
  const seq = ++loadSeq;
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("errorState").style.display = "none";
  renderSkeletonRows();
  try {
    const [data, metaFresh] = await Promise.all([api("/api/creators?" + buildQuery()), api("/api/meta/filters")]);
    if (seq !== loadSeq) return; // a newer request superseded this one
    state.meta = metaFresh;
    renderTabCounts();
    state.rows = data.results;
    state.total = data.total;
    renderTable();
    renderPagination();
    document.getElementById("resultCount").textContent = `${data.total} creator${data.total === 1 ? "" : "s"}`;
  } catch (err) {
    if (seq !== loadSeq) return;
    document.getElementById("creatorRows").innerHTML = "";
    document.getElementById("errorState").textContent = "Failed to load creators: " + err.message;
    document.getElementById("errorState").style.display = "block";
  }
}

function renderTable() {
  const tbody = document.getElementById("creatorRows");
  if (!state.rows.length) {
    tbody.innerHTML = "";
    const emptyEl = document.getElementById("emptyState");
    const hasFilters =
      state.search || Object.values(state.filters).some((v) => (v instanceof Set ? v.size > 0 : v !== null));
    emptyEl.innerHTML = hasFilters
      ? `<div class="empty-title">No creators match these filters</div>Try clearing a filter or widening a range.`
      : `<div class="empty-title">Nothing in ${TAB_LABELS[state.tab]} yet</div>Import data or reclassify creators into this view.`;
    emptyEl.style.display = "block";
    return;
  }
  document.getElementById("emptyState").style.display = "none";
  tbody.innerHTML = state.rows.map(rowHtml).join("");

  tbody.querySelectorAll("tr[data-creator-id]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest(".add-btn") || e.target.closest(".col-select")) return;
      openDrawer(Number(tr.dataset.creatorId));
    });
  });
  tbody.querySelectorAll(".add-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await toggleShortlist(Number(btn.dataset.creatorId));
    });
  });
  tbody.querySelectorAll(".row-select-checkbox").forEach((cb) => {
    cb.addEventListener("change", () => {
      const id = Number(cb.dataset.creatorId);
      if (cb.checked) state.selectedRowIds.add(id);
      else state.selectedRowIds.delete(id);
      cb.closest("tr").classList.toggle("row-selected", cb.checked);
      updateBulkBar();
    });
  });
  applyColumnVisibility();
  updateSelectAllCheckbox();
}

function updateSelectAllCheckbox() {
  const box = document.getElementById("selectAllCheckbox");
  if (!box) return;
  const idsOnPage = state.rows.map((r) => r.id);
  const allSelected = idsOnPage.length > 0 && idsOnPage.every((id) => state.selectedRowIds.has(id));
  box.checked = allSelected;
}

document.getElementById("selectAllCheckbox").addEventListener("change", (e) => {
  state.rows.forEach((r) => {
    if (e.target.checked) state.selectedRowIds.add(r.id);
    else state.selectedRowIds.delete(r.id);
  });
  renderTable();
  updateBulkBar();
});

// ---------------------------------------------------------------------------
// Bulk selection bar
// ---------------------------------------------------------------------------

function updateBulkBar() {
  const bar = document.getElementById("bulkBar");
  const count = state.selectedRowIds.size;
  if (count === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  document.getElementById("bulkBarSummary").textContent = `${count} selected`;
}

document.getElementById("bulkClearBtn").addEventListener("click", () => {
  state.selectedRowIds.clear();
  renderTable();
  updateBulkBar();
});

document.getElementById("bulkAddBtn").addEventListener("click", async () => {
  const ids = [...state.selectedRowIds];
  if (!ids.length) return;
  const btn = document.getElementById("bulkAddBtn");
  btn.disabled = true;
  const originalLabel = btn.textContent;
  let added = 0;
  try {
    const shortlist = await ensureShortlist();
    const existingIds = new Set(shortlist.items.map((i) => i.creator_id));
    for (const id of ids) {
      if (existingIds.has(id)) continue;
      try {
        const detail = await api(`/api/shortlists/${shortlist.id}/items`, {
          method: "POST",
          body: JSON.stringify({ creator_id: id }),
        });
        state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
        updateStickyBar(detail);
        added++;
      } catch (e) {
        /* one creator failing (e.g. already added concurrently) shouldn't stop the rest */
      }
    }
    toast(`Added ${added} of ${ids.length} to the shortlist.`);
    state.selectedRowIds.clear();
    renderTable();
    updateBulkBar();
  } catch (err) {
    toast("Bulk add failed: " + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
});

// ---------------------------------------------------------------------------
// Column visibility
// ---------------------------------------------------------------------------

function applyColumnVisibility() {
  document.querySelectorAll("[data-col]").forEach((el) => {
    el.style.display = state.hiddenColumns.has(el.dataset.col) ? "none" : "";
  });
}

function renderColumnsMenu() {
  const menu = document.getElementById("columnsMenu");
  menu.innerHTML = TOGGLEABLE_COLUMNS.map(
    (c) =>
      `<label class="filter-check"><input type="checkbox" data-col-toggle="${c.key}" ${state.hiddenColumns.has(c.key) ? "" : "checked"} />${escapeHtml(c.label)}</label>`
  ).join("");
  menu.querySelectorAll("[data-col-toggle]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const key = cb.dataset.colToggle;
      if (cb.checked) state.hiddenColumns.delete(key);
      else state.hiddenColumns.add(key);
      localStorage.setItem("mango_kol_hidden_cols", JSON.stringify([...state.hiddenColumns]));
      applyColumnVisibility();
    });
  });
}

document.getElementById("columnsToggleBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = document.getElementById("columnsMenu");
  if (menu.classList.contains("hidden")) {
    renderColumnsMenu();
    menu.classList.remove("hidden");
  } else {
    menu.classList.add("hidden");
  }
});
document.addEventListener("click", (e) => {
  const wrap = document.querySelector(".columns-toggle-wrap");
  if (wrap && !wrap.contains(e.target)) document.getElementById("columnsMenu").classList.add("hidden");
});

document.getElementById("exportFilteredBtn").addEventListener("click", () => {
  window.location.href = "/api/creators/export.csv?" + buildQuery();
});

function deliverableCellHtml(r) {
  if (r.quote_min_usd === null) {
    return `<span class="muted">See raw quote</span>`;
  }
  const extra = r.deliverable_count > 1 ? `<span class="deliverable-more">+${r.deliverable_count - 1} more deliverable${r.deliverable_count > 2 ? "s" : ""}</span>` : "";
  const priceText = r.quote_min_usd === r.quote_max_usd ? fmtUsd(r.quote_min_usd) : `from ${fmtUsd(r.quote_min_usd)}`;
  return `<div class="deliverable-cell"><span class="deliverable-price">${priceText}</span>${extra}</div>`;
}

function cleanText(v) {
  // Defensive against models emitting the literal word "null"/"n/a" instead
  // of a real empty value (see backend/classify.py normalization).
  if (!v) return null;
  const s = String(v).trim();
  return s && !["null", "none", "n/a", "unknown"].includes(s.toLowerCase()) ? s : null;
}

function categoriesCellHtml(categories) {
  const clean = (categories || []).map(cleanText).filter(Boolean);
  if (!clean.length) return '<span class="muted">-</span>';
  const shown = clean.slice(0, 2).join(", ");
  const extra = clean.length > 2 ? ` <span class="muted">+${clean.length - 2}</span>` : "";
  return escapeHtml(shown) + extra;
}

function rowHtml(r) {
  const cpm = r.cpm_min === null ? '<span class="muted">-</span>' : r.cpm_min === r.cpm_max ? `$${r.cpm_min}` : `$${r.cpm_min}-$${r.cpm_max}`;
  const contacts = [r.has_email ? "Email" : null, r.has_telegram ? "Telegram" : null].filter(Boolean).join(", ") || '<span class="muted">-</span>';
  const inShortlist = state.shortlistCreatorIds.has(r.id);
  const isSelected = state.selectedRowIds.has(r.id);
  const confDot = r.classification_confidence ? `<span class="confidence-dot ${r.classification_confidence}" title="Classification confidence: ${r.classification_confidence}"></span>` : "";
  const regionLang = [cleanText(r.region), cleanText(r.language)].filter(Boolean).join(" / ");
  return `
    <tr data-creator-id="${r.id}" class="${isSelected ? "row-selected" : ""}">
      <td class="col-select" onclick="event.stopPropagation()"><input type="checkbox" class="row-checkbox row-select-checkbox" data-creator-id="${r.id}" ${isSelected ? "checked" : ""} /></td>
      <td class="name-cell">
        <div class="creator-cell">
          ${avatarHtml(r.display_name, r.avatar_url)}
          <div>
            <div class="creator-name">${escapeHtml(r.display_name)}</div>
            <div class="creator-handle">${r.handle ? "@" + escapeHtml(r.handle) : ""}</div>
          </div>
        </div>
      </td>
      <td><span class="class-badge ${classTier(r.creator_class)}">${confDot}${escapeHtml(classLabel(r.creator_class))}</span></td>
      <td data-col="platform">${platformBadgeHtml(r.platform, r.platform_raw)}</td>
      <td data-col="category">${categoriesCellHtml(r.categories)}</td>
      <td data-col="region">${regionLang ? escapeHtml(regionLang) : '<span class="muted">-</span>'}</td>
      <td class="num">${fmtNum(r.followers)}</td>
      <td class="num" data-col="avg_views">${fmtNum(r.avg_views)}</td>
      <td class="num" data-col="engagement">${r.engagement_rate === null ? '<span class="muted">-</span>' : r.engagement_rate + "%"}</td>
      <td>${deliverableCellHtml(r)}</td>
      <td class="num quote-text" data-col="cpm">${cpm}</td>
      <td data-col="contact">${contacts}</td>
      <td><button class="add-btn ${inShortlist ? "added" : ""}" data-creator-id="${r.id}">${inShortlist ? "Added" : "Add"}</button></td>
    </tr>`;
}

function renderPagination() {
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const el = document.getElementById("pagination");
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <button class="btn btn-ghost btn-sm" id="prevPage" ${state.page <= 1 ? "disabled" : ""}>Prev</button>
    <span>Page ${state.page} of ${totalPages}</span>
    <button class="btn btn-ghost btn-sm" id="nextPage" ${state.page >= totalPages ? "disabled" : ""}>Next</button>`;
  const prev = document.getElementById("prevPage");
  const next = document.getElementById("nextPage");
  if (prev) prev.addEventListener("click", () => { state.page--; loadCreators(); });
  if (next) next.addEventListener("click", () => { state.page++; loadCreators(); });
}

// ---------------------------------------------------------------------------
// Top controls: tabs, search, sort, batch enrich
// ---------------------------------------------------------------------------

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  btn.classList.add("active");
  state.tab = btn.dataset.tab;
  state.filters.creator_class = new Set();
  state.page = 1;
  state.selectedRowIds.clear();
  updateBulkBar();
  renderFilters();
  loadCreators();
});

let searchDebounce;
document.getElementById("search").addEventListener("input", (e) => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.search = e.target.value;
    state.page = 1;
    loadCreators();
  }, 250);
});

document.getElementById("sortField").addEventListener("change", (e) => {
  state.sort = e.target.value;
  loadCreators();
});
document.getElementById("sortOrderBtn").addEventListener("click", (e) => {
  state.order = state.order === "desc" ? "asc" : "desc";
  e.target.textContent = state.order === "desc" ? "High to low" : "Low to high";
  loadCreators();
});

document.getElementById("batchEnrichBtn").addEventListener("click", async () => {
  const candidates = state.rows.filter((r) => ENRICHABLE_PLATFORMS.has((r.platform || "").toUpperCase()) && !r.last_enriched_at).map((r) => r.id);
  if (!candidates.length) {
    toast("Nothing to refresh on this page -- all eligible rows already have data, or no enrichable-platform rows are visible.");
    return;
  }
  const btn = document.getElementById("batchEnrichBtn");
  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = `Refreshing ${candidates.length}...`;
  try {
    const result = await api("/api/enrich/batch", {
      method: "POST",
      body: JSON.stringify({ creator_ids: candidates, classify: true }),
    });
    const errors = result.results.flatMap((r) => (r.accounts || []).filter((a) => a.status === "error"));
    toast(`Refreshed ${candidates.length - errors.length}/${candidates.length}.${errors.length ? " Some failed, see console." : ""}`, errors.length > 0);
    if (errors.length) console.warn("Enrichment errors:", errors);
    loadCreators();
  } catch (err) {
    toast("Refresh failed: " + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
});

// ---------------------------------------------------------------------------
// Creator detail drawer
// ---------------------------------------------------------------------------

function closeDrawer() {
  document.getElementById("drawer").classList.add("hidden");
  document.getElementById("drawerOverlay").classList.add("hidden");
}
document.getElementById("drawerClose").addEventListener("click", closeDrawer);
document.getElementById("drawerOverlay").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDrawer();
    closeShortlistModal();
    closeManager();
  }
});

async function openDrawer(creatorId) {
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("drawerOverlay");
  const body = document.getElementById("drawerBody");
  overlay.classList.remove("hidden");
  drawer.classList.remove("hidden");
  body.innerHTML = '<div class="empty-state">Loading</div>';
  try {
    const c = await api(`/api/creators/${creatorId}`);
    body.innerHTML = drawerHtml(c);
    wireDrawer(c);
  } catch (err) {
    body.innerHTML = `<div class="empty-state error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

const ENRICHABLE_PLATFORMS = new Set(["X", "YOUTUBE", "INSTAGRAM", "TIKTOK"]);

function drawerHtml(c) {
  const realXAccount = c.social_accounts.find((a) => a.platform.toUpperCase() === "X" && a.handle);
  const enrichableAccount = realXAccount || c.social_accounts.find((a) => ENRICHABLE_PLATFORMS.has(a.platform.toUpperCase()) && a.handle);
  const xAccount = enrichableAccount || c.social_accounts[0];

  const platformRow = c.social_accounts
    .map((a) => `<a href="${escapeAttr(a.profile_url || "#")}" target="_blank" rel="noopener">${platformBadgeHtml(a.platform, a.platform_raw)}</a>`)
    .join("");

  const rateRows = c.rate_cards
    .map(
      (rc) => `
      <div class="rate-row ${rc.is_confident ? "" : "low-confidence"}">
        <span class="deliverable">${escapeHtml(rc.deliverable)}${rc.platform && rc.platform !== xAccount?.platform ? ` (${escapeHtml(rc.platform)})` : ""}</span>
        <span class="amount">${
          rc.quote_amount_usd !== null
            ? fmtUsd(rc.quote_amount_usd) + (rc.quote_currency !== "USD" ? ` (${rc.quote_amount} ${rc.quote_currency})` : "") + (rc.cpm !== null ? `, CPM $${rc.cpm}` : "")
            : "see raw quote"
        }</span>
      </div>`
    )
    .join("") || '<div class="muted">No parsed rate cards.</div>';

  const rawQuotes = [...new Set(c.rate_cards.map((rc) => rc.raw_quote_text).filter(Boolean))];

  const contactRows = c.contacts.map((ct) => `<div class="kv-grid"><span class="k">${escapeHtml(ct.method_type)}</span><span class="v">${escapeHtml(ct.value)}</span></div>`).join("") || '<div class="muted">No contact info on file.</div>';

  const posts = (xAccount?.recent_content || [])
    .slice(0, 8)
    .map(
      (p) => `
      <div class="post-item">
        ${p.is_pinned ? "[pinned] " : ""}${p.is_repost ? "[repost] " : ""}${escapeHtml((p.text || "").slice(0, 220))}
        <div class="post-meta">${p.posted_at || ""}, ${p.views !== null ? fmtNum(p.views) + " views" : "views n/a"}, ${p.likes ?? 0} likes, ${p.replies ?? 0} replies${p.reposts !== null && p.reposts !== undefined ? `, ${p.reposts} reposts` : ""}</div>
      </div>`
    )
    .join("") || '<div class="muted">No recent posts pulled yet.</div>';

  const sponsors = (xAccount?.sponsors || []).map((s) => `${escapeHtml(s.project_name)} (${s.mention_count}x)`).join(", ") || "None detected yet";

  const enrichLabel = enrichableAccount ? `Refresh from ${enrichableAccount.platform}${state.status.openai_configured ? " + classify" : ""}` : "Refresh data";
  const enrichKeyName = { X: "RAPID_X_API_KEY", YOUTUBE: "SCRAPECREATORS_API_KEY / YOUTUBE_API_KEY", INSTAGRAM: "SCRAPECREATORS_API_KEY", TIKTOK: "SCRAPECREATORS_API_KEY" }[enrichableAccount?.platform.toUpperCase()];
  const enrichConfigured = enrichableAccount
    ? enrichableAccount.platform.toUpperCase() === "X"
      ? state.status.rapid_x_configured
      : enrichableAccount.platform.toUpperCase() === "YOUTUBE"
      ? state.status.youtube_configured
      : state.status.scrapecreators_configured
    : false;

  return `
    <div class="detail-head">
      ${avatarHtml(c.display_name, xAccount?.avatar_url, "lg")}
      <div>
        <div class="detail-name">${escapeHtml(c.display_name)}</div>
        <div class="detail-handle">${c.handle ? "@" + escapeHtml(c.handle) : ""}</div>
        <div class="detail-platform-row">${platformRow}</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Classification</div>
      <div class="tag-editable">
        <select class="inline-select" id="editClass">
          ${ALL_CLASSES.map((opt) => `<option value="${opt}" ${opt === c.creator_class ? "selected" : ""}>${classLabel(opt)}</option>`).join("")}
        </select>
        <select class="inline-select" id="editPromo">
          <option value="">Promotion: none</option>
          ${["Low", "Medium", "High"].map((opt) => `<option value="${opt}" ${opt === c.promotion_level ? "selected" : ""}>${opt}</option>`).join("")}
        </select>
        <button class="btn btn-ghost btn-sm" id="saveClassBtn">Save</button>
      </div>
      ${c.creator_class_locked ? '<div class="lock-note">Manually set. Auto-classify will not override it.</div>' : ""}
      ${c.creator_class_reason ? `<div class="reason-text">${escapeHtml(c.creator_class_reason)}</div>` : ""}
      ${c.classification_confidence ? `<div class="provenance-note">Confidence: ${escapeHtml(c.classification_confidence)}, source: ${escapeHtml(c.creator_class_source)}</div>` : ""}
    </div>

    <div class="section">
      <div class="section-title">Profile</div>
      <div class="kv-grid">
        <span class="k">Followers</span><span class="v">${fmtNum(c.followers)}</span>
        <span class="k">Avg views</span><span class="v">${fmtNum(c.avg_views)}</span>
        <span class="k">Engagement</span><span class="v">${c.engagement_rate === null ? "-" : c.engagement_rate + "%"}</span>
        <span class="k">Posting freq.</span><span class="v">${escapeHtml(xAccount?.posting_frequency || "-")}</span>
        <span class="k">Original/repost</span><span class="v">${xAccount?.original_repost_ratio !== null && xAccount?.original_repost_ratio !== undefined ? Math.round(xAccount.original_repost_ratio * 100) + "% original" : "-"}</span>
        <span class="k">Promo ratio (heuristic)</span><span class="v">${xAccount?.promotional_content_ratio !== null && xAccount?.promotional_content_ratio !== undefined ? Math.round(xAccount.promotional_content_ratio * 100) + "%" : "-"}</span>
        <span class="k">Verified</span><span class="v">${xAccount?.verified ? "Yes" : "-"}</span>
        <span class="k">Last refreshed</span><span class="v">${c.last_enriched_at ? new Date(c.last_enriched_at).toLocaleString() : "Never"}</span>
      </div>
      ${xAccount?.bio ? `<div class="bio-text">${escapeHtml(xAccount.bio)}</div>` : ""}
      ${xAccount?.content_summary ? `<div class="reason-text">${escapeHtml(xAccount.content_summary)}</div>` : ""}
      ${xAccount?.enrichment_error ? `<div class="provenance-note">${escapeHtml(xAccount.enrichment_error)}</div>` : ""}
      <div style="margin-top:10px">
        <button class="btn btn-ghost btn-sm" id="enrichBtn" ${enrichConfigured && enrichableAccount ? "" : "disabled"}>${enrichLabel}</button>
        ${!enrichableAccount ? '<span class="provenance-note">No X, YouTube, Instagram, or TikTok account on file for this creator, so no live enrichment source is available.</span>' : ""}
        ${enrichableAccount && !enrichConfigured ? `<span class="provenance-note">${enrichKeyName} not set on the server.</span>` : ""}
      </div>
    </div>

    <div class="section">
      <div class="section-title">Region / Language / Categories</div>
      <div class="tag-editable">
        <input class="inline-input" id="editRegion" placeholder="Region" value="${escapeAttr(cleanText(c.region) || "")}" style="width:110px" />
        <input class="inline-input" id="editLanguage" placeholder="Language" value="${escapeAttr(cleanText(c.language) || "")}" style="width:110px" />
        <input class="inline-input" id="editCategories" placeholder="Categories, comma-separated" value="${escapeAttr((c.categories || []).map(cleanText).filter(Boolean).join(", "))}" style="width:220px" />
        <button class="btn btn-ghost btn-sm" id="saveTagsBtn">Save</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Deliverables and quotes</div>
      ${rateRows}
      ${rawQuotes.length ? `<div class="raw-quote-block"><strong>Raw quote text on file:</strong>\n\n${rawQuotes.map(escapeHtml).join("\n---\n")}</div>` : ""}
    </div>

    <div class="section">
      <div class="section-title">Contact</div>
      ${contactRows}
    </div>

    <div class="section">
      <div class="section-title">Recently promoted / mentioned</div>
      <div>${sponsors}</div>
    </div>

    ${sponsorshipHistoryHtml(c)}

    <div class="section">
      <div class="section-title">Recent posts</div>
      ${posts}
    </div>

    <div class="section">
      <div class="section-title">Internal notes</div>
      <textarea class="notes-area" id="editNotes">${escapeHtml(c.internal_notes || "")}</textarea>
      <button class="btn btn-ghost btn-sm" id="saveNotesBtn" style="margin-top:6px">Save notes</button>
    </div>

    <div class="section">
      <button class="btn btn-primary" id="drawerAddBtn">${state.shortlistCreatorIds.has(c.id) ? "On shortlist" : "Add to shortlist"}</button>
    </div>
  `;
}

function sponsorshipHistoryHtml(c) {
  const history = c.sponsorship_history || [];
  if (!history.length) return "";
  const repeat = c.repeat_sponsor_companies || [];
  const rows = history
    .map(
      (s) => `
      <div class="sponsorship-row">
        <div class="top">
          <span class="company-link" data-company-id="${escapeAttr(s.company_id)}">${escapeHtml(s.company_name || "Unknown company")}</span>
          <span class="disclosure-tag disclosure-${escapeAttr(s.disclosure_type || "unknown")}">${escapeHtml((s.disclosure_type || "unknown").replace(/_/g, " "))}</span>
        </div>
        <div class="muted">${s.platform ? escapeHtml(s.platform) + ", " : ""}${s.published_at ? escapeHtml(s.published_at) : "date unknown"}${s.content_url ? ` -- <a href="${escapeAttr(s.content_url)}" target="_blank" rel="noopener">evidence</a>` : ""}</div>
      </div>`
    )
    .join("");
  return `
    <div class="section">
      <div class="section-title">Sponsorship history (from BD evidence)</div>
      ${repeat.length ? `<div class="reason-text">Repeat sponsor for: ${escapeHtml(repeat.join(", "))}</div>` : ""}
      ${rows}
    </div>`;
}

function wireDrawer(c) {
  document.querySelectorAll("#drawerBody .company-link[data-company-id]").forEach((el) => {
    el.addEventListener("click", () => openCompanyDrawer(el.dataset.companyId));
  });

  document.getElementById("saveClassBtn").addEventListener("click", async () => {
    const creator_class = document.getElementById("editClass").value;
    const promotion_level = document.getElementById("editPromo").value || null;
    try {
      await api(`/api/creators/${c.id}`, { method: "PATCH", body: JSON.stringify({ creator_class, promotion_level }) });
      toast("Classification saved.");
      openDrawer(c.id);
      loadCreators();
    } catch (err) {
      toast("Save failed: " + err.message, true);
    }
  });

  document.getElementById("saveTagsBtn").addEventListener("click", async () => {
    const region = document.getElementById("editRegion").value || null;
    const language = document.getElementById("editLanguage").value || null;
    const categories = document
      .getElementById("editCategories")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      await api(`/api/creators/${c.id}`, { method: "PATCH", body: JSON.stringify({ region, language, categories }) });
      toast("Saved.");
      loadCreators();
    } catch (err) {
      toast("Save failed: " + err.message, true);
    }
  });

  document.getElementById("saveNotesBtn").addEventListener("click", async () => {
    const internal_notes = document.getElementById("editNotes").value;
    try {
      await api(`/api/creators/${c.id}`, { method: "PATCH", body: JSON.stringify({ internal_notes }) });
      toast("Notes saved.");
    } catch (err) {
      toast("Save failed: " + err.message, true);
    }
  });

  const enrichBtn = document.getElementById("enrichBtn");
  if (enrichBtn) {
    enrichBtn.addEventListener("click", async (e) => {
      const originalLabel = e.target.textContent;
      e.target.disabled = true;
      e.target.textContent = "Refreshing...";
      try {
        const result = await api(`/api/creators/${c.id}/enrich`, { method: "POST" });
        const errored = (result.accounts || []).find((a) => a.status === "error");
        const classifyIssue = (result.accounts || []).find((a) => a.classify_error);
        if (errored) {
          toast("Refresh failed: " + errored.error, true);
        } else if (classifyIssue) {
          toast(`Refreshed. Classification skipped: ${classifyIssue.classify_error}`, true);
        } else {
          toast("Refreshed.");
        }
        openDrawer(c.id);
        loadCreators();
      } catch (err) {
        toast("Refresh failed: " + err.message, true);
        e.target.disabled = false;
        e.target.textContent = originalLabel;
      }
    });
  }

  document.getElementById("drawerAddBtn").addEventListener("click", async () => {
    await toggleShortlist(c.id);
    openDrawer(c.id);
  });
}

// ---------------------------------------------------------------------------
// Shortlist
// ---------------------------------------------------------------------------

// Guards against a race: clicking "Add" on two rows in quick succession
// (before the first POST /api/shortlists resolves) would otherwise let both
// calls see shortlistId as still null and each create its own shortlist.
// Concurrent callers share this single in-flight creation promise instead.
let _creatingShortlist = null;

async function ensureShortlist() {
  if (state.shortlistId) {
    try {
      return await api(`/api/shortlists/${state.shortlistId}`);
    } catch (e) {
      state.shortlistId = null; // stale id (deleted server-side) -- fall through and recreate
    }
  }
  if (!_creatingShortlist) {
    _creatingShortlist = api("/api/shortlists", { method: "POST", body: JSON.stringify({ name: "Untitled shortlist" }) })
      .then((created) => {
        state.shortlistId = created.id;
        localStorage.setItem("mango_kol_shortlist_id", created.id);
        return created;
      })
      .finally(() => {
        _creatingShortlist = null;
      });
  }
  return _creatingShortlist;
}

async function refreshShortlistState() {
  if (!state.shortlistId) {
    state.shortlistCreatorIds = new Set();
    updateStickyBar(null);
    return;
  }
  try {
    const detail = await api(`/api/shortlists/${state.shortlistId}`);
    state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
    updateStickyBar(detail);
  } catch (e) {
    state.shortlistId = null;
    state.shortlistCreatorIds = new Set();
    updateStickyBar(null);
  }
}

function updateStickyBar(detail) {
  const bar = document.getElementById("stickyBar");
  const summary = document.getElementById("stickySummary");
  if (!detail || detail.selected_count === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  let text = `Selected ${detail.selected_count} creator${detail.selected_count === 1 ? "" : "s"}, total ${fmtUsd(detail.total_spend_usd)}`;
  if (detail.budget_usd !== null && detail.budget_usd !== undefined) {
    text += `, remaining ${fmtUsd(detail.remaining_budget_usd)}`;
  }
  summary.innerHTML = escapeHtml(text) + (detail.over_budget ? '<span class="over-flag">Over budget</span>' : "");
}

async function toggleShortlist(creatorId) {
  const shortlist = await ensureShortlist();
  const already = state.shortlistCreatorIds.has(creatorId);
  try {
    if (already) {
      const item = shortlist.items.find((i) => i.creator_id === creatorId);
      const detail = item ? await api(`/api/shortlists/${shortlist.id}/items/${item.id}`, { method: "DELETE" }) : shortlist;
      state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
      updateStickyBar(detail);
      toast("Removed from shortlist.");
    } else {
      const detail = await api(`/api/shortlists/${shortlist.id}/items`, {
        method: "POST",
        body: JSON.stringify({ creator_id: creatorId }),
      });
      state.shortlistCreatorIds = new Set(detail.items.map((i) => i.creator_id));
      updateStickyBar(detail);
      toast("Added to shortlist.");
    }
    renderTable();
  } catch (err) {
    toast("Shortlist update failed: " + err.message, true);
  }
}

document.getElementById("openShortlistBtn").addEventListener("click", openShortlistModal);

function closeShortlistModal() {
  document.getElementById("shortlistModal").classList.add("hidden");
  document.getElementById("shortlistOverlay").classList.add("hidden");
}
document.getElementById("shortlistClose").addEventListener("click", closeShortlistModal);
document.getElementById("shortlistOverlay").addEventListener("click", closeShortlistModal);

async function openShortlistModal() {
  if (!state.shortlistId) {
    toast("Shortlist is empty, add a creator first.");
    return;
  }
  document.getElementById("shortlistOverlay").classList.remove("hidden");
  document.getElementById("shortlistModal").classList.remove("hidden");
  await renderShortlistModal();
}

async function renderShortlistModal() {
  const body = document.getElementById("shortlistBody");
  body.innerHTML = '<div class="empty-state">Loading</div>';
  try {
    const detail = await api(`/api/shortlists/${state.shortlistId}`);
    body.innerHTML = shortlistModalHtml(detail);
    wireShortlistModal(detail);
    updateStickyBar(detail);
  } catch (err) {
    body.innerHTML = `<div class="empty-state error">${escapeHtml(err.message)}</div>`;
  }
}

function shortlistModalHtml(detail) {
  const rows = detail.items
    .map((item) => {
      const options = item.rate_cards
        .map((rc) => `<option value="${rc.id}" ${rc.id === item.rate_card_id ? "selected" : ""}>${escapeHtml(rc.deliverable)}, ${rc.quote_amount_usd !== null ? fmtUsd(rc.quote_amount_usd) : "n/a"}</option>`)
        .join("");
      return `
      <tr data-item-id="${item.item_id}">
        <td>
          <div class="creator-cell">
            ${avatarHtml(item.display_name, item.avatar_url)}
            <div>
              <div class="creator-name">${escapeHtml(item.display_name)}</div>
              <div class="creator-handle">${item.handle ? "@" + escapeHtml(item.handle) : ""} <span class="class-badge ${classTier(item.creator_class)}">${escapeHtml(classLabel(item.creator_class))}</span></div>
            </div>
          </div>
        </td>
        <td>
          <select class="small-select item-deliverable" data-item-id="${item.item_id}">
            <option value="">No deliverable selected</option>
            ${options}
          </select>
        </td>
        <td class="num quote-text">${fmtUsd(item.quote_usd)}</td>
        <td><button class="btn btn-ghost btn-sm item-remove" data-item-id="${item.item_id}">Remove</button></td>
      </tr>`;
    })
    .join("");

  return `
    <div class="shortlist-toolbar">
      <input id="shortlistName" placeholder="Shortlist name" value="${escapeAttr(detail.name)}" style="width:220px" />
      <input id="shortlistCampaign" placeholder="Client / campaign" value="${escapeAttr(detail.client_or_campaign || "")}" style="width:200px" />
      <input id="shortlistBudget" type="number" placeholder="Budget (USD)" value="${detail.budget_usd ?? ""}" style="width:130px" />
      <button class="btn btn-ghost btn-sm" id="saveShortlistMetaBtn">Save</button>
      <button class="btn btn-ghost btn-sm" id="copyTsvBtn">Copy for Google Sheets</button>
      <a class="btn btn-ghost btn-sm" href="/api/shortlists/${detail.id}/export.csv">Export CSV</a>
      <a class="btn btn-ghost btn-sm" href="/api/shortlists/${detail.id}/export.xlsx">Export XLSX</a>
    </div>

    ${
      detail.company_id
        ? `<div class="campaign-form">
            <input id="shortlistObjective" placeholder="Objective" value="${escapeAttr(detail.objective || "")}" />
            <input id="shortlistAudience" placeholder="Target audience" value="${escapeAttr(detail.target_audience || "")}" />
            <input id="shortlistRegion" placeholder="Region pref" value="${escapeAttr(detail.region_pref || "")}" />
            <input id="shortlistTiming" placeholder="Timing" value="${escapeAttr(detail.timing || "")}" />
            <button class="btn btn-ghost btn-sm" id="saveCampaignFieldsBtn" style="grid-column:span 2">Save campaign details for ${escapeHtml(detail.company_name || "")}</button>
          </div>`
        : ""
    }

    <div class="budget-summary">
      <div class="budget-stat"><div class="label">Selected</div><div class="value">${detail.selected_count}</div></div>
      <div class="budget-stat"><div class="label">Strategic KOL spend</div><div class="value">${fmtUsd(detail.kol_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">KOC / distribution spend</div><div class="value">${fmtUsd(detail.koc_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">Total spend</div><div class="value">${fmtUsd(detail.total_spend_usd)}</div></div>
      <div class="budget-stat"><div class="label">Remaining budget</div><div class="value ${detail.over_budget ? "over" : ""}">${detail.budget_usd !== null ? fmtUsd(detail.remaining_budget_usd) : "-"}</div></div>
      ${detail.unpriced_count ? `<div class="budget-stat"><div class="label">Unpriced</div><div class="value">${detail.unpriced_count}</div></div>` : ""}
    </div>

    <table class="shortlist-table">
      <thead><tr><th>Creator</th><th>Deliverable</th><th class="num">Quote</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">No creators yet.</td></tr>'}</tbody>
    </table>

    ${detail.company_id ? '<div id="suggestedCreatorsPanel" class="suggested-creators-panel">Loading suggested creators...</div>' : ""}
  `;
}

async function loadSuggestedCreators(detail) {
  const panel = document.getElementById("suggestedCreatorsPanel");
  if (!panel) return;
  try {
    const data = await api(`/api/companies/${encodeURIComponent(detail.company_id)}/suggested-creators`);
    const existingIds = new Set(detail.items.map((i) => i.creator_id));
    const rows = data.results
      .map(
        (r) => `
        <div class="suggested-row">
          <div class="info">
            <div class="name">${escapeHtml(r.display_name)}${r.prior_relationship ? ' <span class="confirmed-tag">prior relationship</span>' : ""}</div>
            <div class="reasons">${escapeHtml(r.reasons.join("; "))}</div>
          </div>
          <button class="btn btn-ghost btn-sm suggested-add" data-creator-id="${r.id}" ${existingIds.has(r.id) ? "disabled" : ""}>${existingIds.has(r.id) ? "Added" : "Add"}</button>
        </div>`
      )
      .join("");
    panel.innerHTML = `<div class="section-title">Suggested creators for ${escapeHtml(detail.company_name || "this campaign")}</div>${rows || '<div class="muted">No candidates yet -- no quote on file and no prior relationship with this company.</div>'}`;
    panel.querySelectorAll(".suggested-add").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/shortlists/${detail.id}/items`, { method: "POST", body: JSON.stringify({ creator_id: Number(btn.dataset.creatorId) }) });
          toast("Added to shortlist.");
          await refreshShortlistState();
          renderShortlistModal();
        } catch (err) {
          toast("Add failed: " + err.message, true);
        }
      });
    });
  } catch (err) {
    panel.innerHTML = `<div class="muted">Failed to load suggested creators: ${escapeHtml(err.message)}</div>`;
  }
}

function wireShortlistModal(detail) {
  if (detail.company_id) loadSuggestedCreators(detail);

  const saveCampaignBtn = document.getElementById("saveCampaignFieldsBtn");
  if (saveCampaignBtn) {
    saveCampaignBtn.addEventListener("click", async () => {
      const objective = document.getElementById("shortlistObjective").value || null;
      const target_audience = document.getElementById("shortlistAudience").value || null;
      const region_pref = document.getElementById("shortlistRegion").value || null;
      const timing = document.getElementById("shortlistTiming").value || null;
      try {
        await api(`/api/shortlists/${detail.id}`, { method: "PATCH", body: JSON.stringify({ objective, target_audience, region_pref, timing }) });
        toast("Campaign details saved.");
        renderShortlistModal();
      } catch (err) {
        toast("Save failed: " + err.message, true);
      }
    });
  }

  document.getElementById("saveShortlistMetaBtn").addEventListener("click", async () => {
    const name = document.getElementById("shortlistName").value;
    const client_or_campaign = document.getElementById("shortlistCampaign").value || null;
    const budgetRaw = document.getElementById("shortlistBudget").value;
    const budget_usd = budgetRaw === "" ? null : Number(budgetRaw);
    try {
      await api(`/api/shortlists/${detail.id}`, { method: "PATCH", body: JSON.stringify({ name, client_or_campaign, budget_usd }) });
      toast("Shortlist saved.");
      renderShortlistModal();
    } catch (err) {
      toast("Save failed: " + err.message, true);
    }
  });

  document.getElementById("copyTsvBtn").addEventListener("click", async () => {
    try {
      const { tsv } = await api(`/api/shortlists/${detail.id}/export.tsv`);
      await navigator.clipboard.writeText(tsv);
      toast("Copied. Paste directly into Google Sheets.");
    } catch (err) {
      toast("Copy failed: " + err.message, true);
    }
  });

  document.querySelectorAll(".item-deliverable").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const itemId = sel.dataset.itemId;
      const rate_card_id = sel.value ? Number(sel.value) : null;
      try {
        await api(`/api/shortlists/${detail.id}/items/${itemId}`, { method: "PATCH", body: JSON.stringify({ rate_card_id }) });
        renderShortlistModal();
      } catch (err) {
        toast("Update failed: " + err.message, true);
      }
    });
  });

  document.querySelectorAll(".item-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/shortlists/${detail.id}/items/${btn.dataset.itemId}`, { method: "DELETE" });
        toast("Removed.");
        await refreshShortlistState();
        renderShortlistModal();
        renderTable();
      } catch (err) {
        toast("Remove failed: " + err.message, true);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Shortlist manager (list / switch / create / delete)
// ---------------------------------------------------------------------------

document.getElementById("shortlistManagerBtn").addEventListener("click", openManager);
document.getElementById("managerClose").addEventListener("click", closeManager);
document.getElementById("managerOverlay").addEventListener("click", closeManager);

function closeManager() {
  document.getElementById("managerModal").classList.add("hidden");
  document.getElementById("managerOverlay").classList.add("hidden");
}

async function openManager() {
  document.getElementById("managerOverlay").classList.remove("hidden");
  document.getElementById("managerModal").classList.remove("hidden");
  await renderManager();
}

async function renderManager() {
  const body = document.getElementById("managerBody");
  body.innerHTML = '<div class="empty-state">Loading</div>';
  try {
    const shortlists = await api("/api/shortlists");
    body.innerHTML = `
      <div class="shortlist-toolbar">
        <input id="newShortlistName" placeholder="New shortlist name" style="width:240px" />
        <button class="btn btn-primary btn-sm" id="createShortlistBtn">Create</button>
      </div>
      <table class="shortlist-table">
        <thead><tr><th>Name</th><th>Campaign</th><th class="num">Selected</th><th class="num">Budget</th><th></th></tr></thead>
        <tbody>
          ${
            shortlists.length
              ? shortlists
                  .map(
                    (s) => `
            <tr>
              <td>${escapeHtml(s.name)}${s.id === state.shortlistId ? ' <span class="muted">(current)</span>' : ""}</td>
              <td>${escapeHtml(s.client_or_campaign || "-")}</td>
              <td class="num">${s.selected_count}</td>
              <td class="num">${s.budget_usd !== null ? fmtUsd(s.budget_usd) : "-"}</td>
              <td>
                <button class="link-btn switch-shortlist" data-id="${s.id}">Switch to this</button>
                <button class="link-btn delete-shortlist" data-id="${s.id}">Delete</button>
              </td>
            </tr>`
                  )
                  .join("")
              : '<tr><td colspan="5" class="muted">No shortlists yet.</td></tr>'
          }
        </tbody>
      </table>`;

    document.getElementById("createShortlistBtn").addEventListener("click", async () => {
      const name = document.getElementById("newShortlistName").value.trim() || "Untitled shortlist";
      const created = await api("/api/shortlists", { method: "POST", body: JSON.stringify({ name }) });
      state.shortlistId = created.id;
      localStorage.setItem("mango_kol_shortlist_id", created.id);
      await refreshShortlistState();
      renderManager();
    });
    body.querySelectorAll(".switch-shortlist").forEach((btn) => {
      btn.addEventListener("click", async () => {
        state.shortlistId = Number(btn.dataset.id);
        localStorage.setItem("mango_kol_shortlist_id", btn.dataset.id);
        await refreshShortlistState();
        renderTable();
        renderManager();
        toast("Switched active shortlist.");
      });
    });
    body.querySelectorAll(".delete-shortlist").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Delete this shortlist? This cannot be undone.")) return;
        await api(`/api/shortlists/${btn.dataset.id}`, { method: "DELETE" });
        if (Number(btn.dataset.id) === state.shortlistId) {
          state.shortlistId = null;
          localStorage.removeItem("mango_kol_shortlist_id");
          await refreshShortlistState();
          renderTable();
        }
        renderManager();
      });
    });
  } catch (err) {
    body.innerHTML = `<div class="empty-state error">${escapeHtml(err.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Section switching
// ---------------------------------------------------------------------------

const SECTION_LOADERS = {
  home: () => loadHome(),
  opportunities: () => loadOpportunities(true),
  network: () => loadNetwork(),
  creators: () => {}, // already loaded at init; tab/filter state is preserved
};

function switchSection(section) {
  state.section = section;
  closeDrawer();
  document.querySelectorAll(".section-btn").forEach((b) => b.classList.toggle("active", b.dataset.section === section));
  document.querySelectorAll(".app-section").forEach((el) => el.classList.add("hidden"));
  document.getElementById(section + "Section").classList.remove("hidden");
  SECTION_LOADERS[section]();
}

document.getElementById("sectionNav").addEventListener("click", (e) => {
  const btn = e.target.closest(".section-btn");
  if (!btn) return;
  switchSection(btn.dataset.section);
});

function priorityBadgeHtml(label) {
  return `<span class="priority-badge priority-${escapeAttr(label)}">${escapeHtml(label)}</span>`;
}

// ---------------------------------------------------------------------------
// Home / Action Map
// ---------------------------------------------------------------------------

async function loadHome() {
  const body = document.getElementById("homeBody");
  body.innerHTML = '<div class="empty-state">Loading</div>';
  try {
    const data = await api("/api/home/summary");
    body.innerHTML = homeHtml(data);
    wireHome();
  } catch (err) {
    body.innerHTML = `<div class="empty-state error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function homeHtml(d) {
  const priorityRows = (d.top_priority_companies || [])
    .map(
      (c) => `
      <div class="home-list-row" data-company-id="${escapeAttr(c.company_id)}">
        <div>
          <div class="name">${escapeHtml(c.name)}</div>
          <div class="sub">${escapeHtml(c.reachability_label)}${c.category ? " -- " + escapeHtml(c.category) : ""}</div>
        </div>
        ${priorityBadgeHtml(c.priority)}
      </div>`
    )
    .join("") || '<div class="muted">No companies yet.</div>';

  const actionRows = (d.top_actions || [])
    .map(
      (a) => `
      <div class="home-action-row">
        <span class="company" data-company-id="${escapeAttr(a.company_id)}">${escapeHtml(a.company_name)}</span>
        <span class="wave">wave ${a.execution_wave ?? "-"}${a.owner ? ", " + escapeHtml(a.owner) : ""}</span>
        <div class="text">${escapeHtml(a.primary_next_action)}</div>
      </div>`
    )
    .join("") || '<div class="muted">No open actions.</div>';

  const readyRows = (d.campaign_ready || [])
    .map(
      (c) => `
      <div class="home-list-row" data-company-id="${escapeAttr(c.company_id)}">
        <div>
          <div class="name">${escapeHtml(c.name)}</div>
          <div class="sub">${c.paid_sponsorship_count} paid sponsorship${c.paid_sponsorship_count === 1 ? "" : "s"} on record</div>
        </div>
        ${priorityBadgeHtml(c.priority)}
      </div>`
    )
    .join("") || '<div class="muted">No campaign-ready companies yet -- these need both High priority and prior paid-sponsorship evidence.</div>';

  const evidenceRows = (d.recent_paid_evidence || [])
    .map(
      (s) => `
      <div class="sponsorship-row">
        <div class="top">
          <span>${escapeHtml(s.creator_name || "Unknown creator")} &rarr; <span class="company-link" data-company-id="${escapeAttr(s.company_id)}">${escapeHtml(s.company_name || "Unknown company")}</span></span>
          <span class="disclosure-tag disclosure-paid_sponsorship">paid</span>
        </div>
        <div class="muted">${s.platform ? escapeHtml(s.platform) + ", " : ""}${s.published_at ? escapeHtml(s.published_at) : "date unknown"}${s.content_url ? ` -- <a href="${escapeAttr(s.content_url)}" target="_blank" rel="noopener">evidence</a>` : ""}</div>
      </div>`
    )
    .join("") || '<div class="muted">No paid sponsorship evidence yet.</div>';

  return `
    <div class="home-totals">
      <div class="home-total-stat"><div class="label">Companies tracked</div><div class="value">${d.totals.companies}</div></div>
      <div class="home-total-stat"><div class="label">Confirmed operators</div><div class="value">${d.totals.confirmed_operators}</div></div>
      <div class="home-total-stat"><div class="label">Paid sponsorships on record</div><div class="value">${d.totals.paid_sponsorships}</div></div>
    </div>
    <div class="home-grid">
      <div class="home-card">
        <div class="home-card-title">Most worth contacting right now<span class="hint">reachability weighted highest</span></div>
        ${priorityRows}
      </div>
      <div class="home-card">
        <div class="home-card-title">Today's highest-value next actions</div>
        ${actionRows}
      </div>
    </div>
    <div class="home-grid">
      <div class="home-card">
        <div class="home-card-title">Campaign-ready companies</div>
        ${readyRows}
      </div>
      <div class="home-card">
        <div class="home-card-title">Recent paid sponsorship evidence</div>
        ${evidenceRows}
      </div>
    </div>
  `;
}

function wireHome() {
  document.querySelectorAll("#homeBody [data-company-id]").forEach((el) => {
    if (!el.dataset.companyId) return;
    el.addEventListener("click", () => {
      switchSection("opportunities");
      openCompanyDrawer(el.dataset.companyId);
    });
  });
}

// ---------------------------------------------------------------------------
// Opportunities
// ---------------------------------------------------------------------------

function oppBuildQuery() {
  const p = new URLSearchParams();
  p.set("sort", state.opp.sort);
  p.set("order", state.opp.order);
  p.set("page", state.opp.page);
  p.set("page_size", state.opp.pageSize);
  if (state.opp.search) p.set("search", state.opp.search);
  const f = state.opp.filters;
  f.priority.forEach((v) => p.append("priority", v));
  f.category.forEach((v) => p.append("category", v));
  f.geography.forEach((v) => p.append("geography", v));
  f.spend_evidence_level.forEach((v) => p.append("spend_evidence_level", v));
  return p.toString();
}

async function loadOpportunities(refreshMeta = false) {
  document.getElementById("oppEmptyState").style.display = "none";
  document.getElementById("oppErrorState").style.display = "none";
  try {
    const calls = [api("/api/companies?" + oppBuildQuery())];
    if (refreshMeta || !state.opp.meta.total) calls.push(api("/api/companies/meta/filters"));
    const [data, meta] = await Promise.all(calls);
    if (meta) state.opp.meta = meta;
    state.opp.rows = data.results;
    state.opp.total = data.total;
    renderOppFilters();
    renderOppTable();
    renderOppPagination();
    document.getElementById("oppResultCount").textContent = `${data.total} compan${data.total === 1 ? "y" : "ies"}`;
  } catch (err) {
    document.getElementById("oppRows").innerHTML = "";
    document.getElementById("oppErrorState").textContent = "Failed to load companies: " + err.message;
    document.getElementById("oppErrorState").style.display = "block";
  }
}

function renderOppFilters() {
  const body = document.getElementById("oppFilterBody");
  const priorityOptions = Object.keys(state.opp.meta.priority_counts || {});
  body.innerHTML = [
    checkboxGroup("Priority", "priority", priorityOptions, state.opp.filters.priority, true),
    checkboxGroup("Spend evidence", "spend_evidence_level", state.opp.meta.spend_evidence_levels || [], state.opp.filters.spend_evidence_level, true),
    checkboxGroup("Category", "category", state.opp.meta.categories || [], state.opp.filters.category),
    checkboxGroup("Geography", "geography", state.opp.meta.geographies || [], state.opp.filters.geography),
  ].join("");
  body.querySelectorAll("[data-filter-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.filterKey;
      const value = input.dataset.filterValue;
      const set = state.opp.filters[key];
      if (input.checked) set.add(value);
      else set.delete(value);
      state.opp.page = 1;
      renderOppFilters();
      loadOpportunities();
    });
  });
}

document.getElementById("oppClearFilters").addEventListener("click", () => {
  state.opp.filters = { priority: new Set(), category: new Set(), geography: new Set(), spend_evidence_level: new Set() };
  state.opp.page = 1;
  renderOppFilters();
  loadOpportunities();
});

document.getElementById("oppSortField").addEventListener("change", (e) => {
  state.opp.sort = e.target.value;
  loadOpportunities();
});
document.getElementById("oppSortOrderBtn").addEventListener("click", (e) => {
  state.opp.order = state.opp.order === "desc" ? "asc" : "desc";
  e.target.textContent = state.opp.order === "desc" ? "High to low" : "Low to high";
  loadOpportunities();
});

function renderOppTable() {
  const tbody = document.getElementById("oppRows");
  if (!state.opp.rows.length) {
    tbody.innerHTML = "";
    document.getElementById("oppEmptyState").innerHTML = `<div class="empty-title">No companies match these filters</div>Try clearing a filter.`;
    document.getElementById("oppEmptyState").style.display = "block";
    return;
  }
  document.getElementById("oppEmptyState").style.display = "none";
  tbody.innerHTML = state.opp.rows
    .map(
      (r) => `
      <tr data-company-id="${escapeAttr(r.company_id)}">
        <td class="name-cell"><div class="creator-name">${escapeHtml(r.name)}</div></td>
        <td>${priorityBadgeHtml(r.priority)}</td>
        <td><span class="reach-label reach-level-${r.reachability_level}">${escapeHtml(r.reachability_label)}</span></td>
        <td data-col="category">${r.category ? escapeHtml(r.category) : '<span class="muted">-</span>'}</td>
        <td data-col="geography">${r.geography ? escapeHtml(r.geography) : '<span class="muted">-</span>'}</td>
        <td class="num">${r.sponsorship_count}${r.paid_sponsorship_count ? ` <span class="muted">(${r.paid_sponsorship_count} paid)</span>` : ""}</td>
        <td>${r.next_action ? escapeHtml(r.next_action.slice(0, 90)) + (r.next_action.length > 90 ? "..." : "") : '<span class="muted">-</span>'}</td>
      </tr>`
    )
    .join("");
  tbody.querySelectorAll("tr[data-company-id]").forEach((tr) => {
    tr.addEventListener("click", () => openCompanyDrawer(tr.dataset.companyId));
  });
}

function renderOppPagination() {
  const totalPages = Math.max(1, Math.ceil(state.opp.total / state.opp.pageSize));
  const el = document.getElementById("oppPagination");
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <button class="btn btn-ghost btn-sm" id="oppPrevPage" ${state.opp.page <= 1 ? "disabled" : ""}>Prev</button>
    <span>Page ${state.opp.page} of ${totalPages}</span>
    <button class="btn btn-ghost btn-sm" id="oppNextPage" ${state.opp.page >= totalPages ? "disabled" : ""}>Next</button>`;
  const prev = document.getElementById("oppPrevPage");
  const next = document.getElementById("oppNextPage");
  if (prev) prev.addEventListener("click", () => { state.opp.page--; loadOpportunities(); });
  if (next) next.addEventListener("click", () => { state.opp.page++; loadOpportunities(); });
}

// ---------------------------------------------------------------------------
// Company drawer (reuses the same #drawer chrome as the creator drawer)
// ---------------------------------------------------------------------------

async function openCompanyDrawer(companyId) {
  if (!companyId) return;
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("drawerOverlay");
  const body = document.getElementById("drawerBody");
  overlay.classList.remove("hidden");
  drawer.classList.remove("hidden");
  body.innerHTML = '<div class="empty-state">Loading</div>';
  try {
    const c = await api(`/api/companies/${encodeURIComponent(companyId)}`);
    body.innerHTML = companyDrawerHtml(c);
    wireCompanyDrawer(c);
  } catch (err) {
    body.innerHTML = `<div class="empty-state error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function pathBlockHtml(title, path) {
  if (!path) return `<div class="path-block">${escapeHtml(title)}: no path on record.</div>`;
  return `<div class="path-block">
    <span class="path-label">${escapeHtml(title)}:</span> ${path.path_labels ? escapeHtml(path.path_labels) : escapeHtml(path.degree_label || "unknown route")}
    <div class="muted" style="margin-top:4px">${path.graph_reachable ? "Technically reachable" : "Not confirmed reachable"} -- ${escapeHtml(path.human_intro_status || "unvalidated")}</div>
  </div>`;
}

function companyDrawerHtml(c) {
  const operatorRows = (c.operators || [])
    .map(
      (o) => `
      <div class="op-list-row">
        <div>
          <div>${escapeHtml(o.name || "Unnamed")} ${o.identity_confirmed ? '<span class="confirmed-tag">confirmed</span>' : ""}</div>
          <div class="role">${escapeHtml(o.role || "")}${o.x_handle ? " -- @" + escapeHtml(o.x_handle) : ""}</div>
        </div>
        ${o.budget_authority_confirmed ? '<span class="confirmed-tag">budget authority</span>' : ""}
      </div>`
    )
    .join("") || '<div class="muted">No named operators on record.</div>';

  const actionRows = (c.action_items || [])
    .map(
      (a) => `
      <div class="action-item-block">
        <div><strong>Wave ${a.execution_wave ?? "-"}</strong>${a.owner ? " -- " + escapeHtml(a.owner) : ""}</div>
        <div>${escapeHtml(a.primary_next_action)}</div>
        ${a.fallback ? `<div class="muted">Fallback: ${escapeHtml(a.fallback)}</div>` : ""}
        ${a.success_condition ? `<div class="muted">Success: ${escapeHtml(a.success_condition)}</div>` : ""}
      </div>`
    )
    .join("") || '<div class="muted">No action items on record.</div>';

  const sponsorshipRows = (c.sponsorships || [])
    .map(
      (s) => `
      <div class="sponsorship-row">
        <div class="top">
          <span class="creator-name" ${s.creator ? `data-creator-id="${s.creator.id}"` : ""}>${escapeHtml(s.creator_name || s.creator?.display_name || "Unknown creator")}</span>
          <span class="disclosure-tag disclosure-${escapeAttr(s.disclosure_type || "unknown")}">${escapeHtml((s.disclosure_type || "unknown").replace(/_/g, " "))}</span>
        </div>
        <div class="muted">${s.platform ? escapeHtml(s.platform) + ", " : ""}${s.published_at ? escapeHtml(s.published_at) : "date unknown"}${s.creator && !s.creator.has_quote ? " -- no Mango quote yet" : ""}${s.content_url ? ` -- <a href="${escapeAttr(s.content_url)}" target="_blank" rel="noopener">evidence</a>` : ""}</div>
      </div>`
    )
    .join("") || '<div class="muted">No sponsorship evidence on record.</div>';

  return `
    <div class="detail-head">
      <div>
        <div class="detail-name">${escapeHtml(c.name)}</div>
        <div class="detail-handle">${c.category ? escapeHtml(c.category) : ""}${c.geography ? " -- " + escapeHtml(c.geography) : ""}</div>
        <div style="margin-top:6px">${priorityBadgeHtml(c.priority)}</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Why this priority</div>
      <ul class="reasons-list">${(c.priority_reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>
    </div>

    ${c.why_now ? `<div class="section"><div class="section-title">Why now</div><div class="bio-text">${escapeHtml(c.why_now)}</div></div>` : ""}
    ${c.budget_evidence ? `<div class="section"><div class="section-title">Budget evidence</div><div class="bio-text">${escapeHtml(c.budget_evidence)}</div></div>` : ""}

    <div class="section">
      <div class="section-title">Reachability</div>
      ${pathBlockHtml("Path to a named operator", c.best_person_path)}
      ${pathBlockHtml("Path to the company account", c.best_company_path)}
    </div>

    <div class="section">
      <div class="section-title">Operators</div>
      ${operatorRows}
    </div>

    <div class="section">
      <div class="section-title">Action items</div>
      ${actionRows}
    </div>

    <div class="section">
      <div class="section-title">Sponsorship evidence (${c.sponsorship_count})</div>
      ${sponsorshipRows}
    </div>

    ${
      c.gtm_case
        ? `<div class="section"><div class="section-title">GTM notes</div>
        <div class="kv-grid"><span class="k">Motion</span><span class="v">${escapeHtml(c.gtm_case.gtm_motion || "-")}</span></div>
        ${c.gtm_case.what_mango_should_copy ? `<div class="reason-text">Copy: ${escapeHtml(c.gtm_case.what_mango_should_copy)}</div>` : ""}
        ${c.gtm_case.what_not_to_copy ? `<div class="reason-text">Avoid: ${escapeHtml(c.gtm_case.what_not_to_copy)}</div>` : ""}
        </div>`
        : ""
    }

    <div class="section">
      <button class="btn btn-primary" id="buildCampaignBtn">Build a campaign for ${escapeHtml(c.name)}</button>
      <div id="buildCampaignForm" style="margin-top:12px"></div>
    </div>
  `;
}

function wireCompanyDrawer(c) {
  document.querySelectorAll("#drawerBody .creator-name[data-creator-id]").forEach((el) => {
    el.addEventListener("click", () => openDrawer(Number(el.dataset.creatorId)));
  });
  document.getElementById("buildCampaignBtn").addEventListener("click", () => {
    document.getElementById("buildCampaignForm").innerHTML = `
      <div class="campaign-form">
        <input id="cfObjective" placeholder="Objective (e.g. launch awareness push)" style="grid-column:span 2" />
        <input id="cfBudget" type="number" placeholder="Budget (USD)" />
        <input id="cfAudience" placeholder="Target audience" />
      </div>
      <button class="btn btn-primary btn-sm" id="cfSubmit">Create campaign shortlist</button>`;
    document.getElementById("cfSubmit").addEventListener("click", async () => {
      const objective = document.getElementById("cfObjective").value || null;
      const budgetRaw = document.getElementById("cfBudget").value;
      const target_audience = document.getElementById("cfAudience").value || null;
      try {
        const created = await api(`/api/companies/${encodeURIComponent(c.company_id)}/campaigns`, {
          method: "POST",
          body: JSON.stringify({ objective, target_audience, budget_usd: budgetRaw === "" ? null : Number(budgetRaw) }),
        });
        state.shortlistId = created.id;
        localStorage.setItem("mango_kol_shortlist_id", created.id);
        await refreshShortlistState();
        closeDrawer();
        toast(`Campaign shortlist "${created.name}" created.`);
        await openShortlistModal();
      } catch (err) {
        toast("Could not create campaign: " + err.message, true);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------

async function loadNetwork() {
  const body = document.getElementById("networkBody");
  body.innerHTML = '<div class="empty-state">Loading</div>';
  try {
    const [connectors, operators] = await Promise.all([api("/api/network/connectors"), api("/api/network/operators")]);
    body.innerHTML = networkHtml(connectors.results, operators.results);
    wireNetwork();
  } catch (err) {
    body.innerHTML = `<div class="empty-state error">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function networkHtml(connectors, operators) {
  const connectorCards = connectors
    .map(
      (b) => `
      <div class="connector-card">
        <div class="connector-head">
          <span class="connector-name">${escapeHtml(b.connector_name)}</span>
          <span class="connector-handle">${escapeHtml(b.connector_x_handle || "")}</span>
        </div>
        <div class="connector-companies">
          ${b.companies.map((c) => `<span class="company-pill" data-company-id="${escapeAttr(c.company_id)}">${escapeHtml(c.name)}</span>`).join("")}
        </div>
        <div class="connector-action">${escapeHtml(b.best_current_action || "")}</div>
      </div>`
    )
    .join("") || '<div class="muted">No connector briefings on record.</div>';

  const operatorRows = operators
    .map(
      (o) => `
      <div class="op-list-row">
        <div>
          <div>${escapeHtml(o.name)} ${o.budget_authority_confirmed ? '<span class="confirmed-tag">budget authority</span>' : ""}</div>
          <div class="role">${escapeHtml(o.role || "")} -- <span class="company-link" data-company-id="${escapeAttr(o.company_id)}">${escapeHtml(o.company_name)}</span></div>
        </div>
      </div>`
    )
    .join("") || '<div class="muted">No confirmed operators on record.</div>';

  return `
    <div class="home-card-title" style="margin-bottom:10px">Connector conversations that unlock the most companies</div>
    ${connectorCards}
    <div class="home-card-title" style="margin:22px 0 10px">Confirmed operators</div>
    <div class="home-card">${operatorRows}</div>
  `;
}

function wireNetwork() {
  document.querySelectorAll("#networkBody [data-company-id]").forEach((el) => {
    el.addEventListener("click", () => openCompanyDrawer(el.dataset.companyId));
  });
}

// ---------------------------------------------------------------------------
// Global search
// ---------------------------------------------------------------------------

let globalSearchDebounce;
document.getElementById("globalSearch").addEventListener("input", (e) => {
  clearTimeout(globalSearchDebounce);
  const q = e.target.value.trim();
  const resultsEl = document.getElementById("globalSearchResults");
  if (q.length < 2) {
    resultsEl.classList.add("hidden");
    return;
  }
  globalSearchDebounce = setTimeout(async () => {
    try {
      const data = await api("/api/search?q=" + encodeURIComponent(q));
      renderGlobalSearchResults(data);
    } catch (err) {
      /* silent -- search is a convenience affordance, not a critical path */
    }
  }, 220);
});

function renderGlobalSearchResults(data) {
  const resultsEl = document.getElementById("globalSearchResults");
  const groups = [
    { label: "Companies", items: data.companies, kind: "company" },
    { label: "People", items: data.operators, kind: "operator" },
    { label: "Creators", items: data.creators, kind: "creator" },
  ].filter((g) => g.items && g.items.length);

  if (!groups.length) {
    resultsEl.innerHTML = '<div class="search-empty">No matches.</div>';
    resultsEl.classList.remove("hidden");
    return;
  }

  resultsEl.innerHTML = groups
    .map(
      (g) => `
      <div class="search-group-label">${g.label}</div>
      ${g.items
        .map((item) => {
          if (g.kind === "company") return `<div class="search-result-row" data-kind="company" data-id="${escapeAttr(item.company_id)}"><span class="name">${escapeHtml(item.name)}</span></div>`;
          if (g.kind === "operator") return `<div class="search-result-row" data-kind="company" data-id="${escapeAttr(item.company_id)}"><span class="name">${escapeHtml(item.name)}</span><span class="meta">${escapeHtml(item.company_name)}</span></div>`;
          return `<div class="search-result-row" data-kind="creator" data-id="${item.id}"><span class="name">${escapeHtml(item.display_name)}</span></div>`;
        })
        .join("")}`
    )
    .join("");
  resultsEl.classList.remove("hidden");

  resultsEl.querySelectorAll(".search-result-row").forEach((row) => {
    row.addEventListener("click", () => {
      resultsEl.classList.add("hidden");
      document.getElementById("globalSearch").value = "";
      if (row.dataset.kind === "company") {
        switchSection("opportunities");
        openCompanyDrawer(row.dataset.id);
      } else {
        openDrawer(Number(row.dataset.id));
      }
    });
  });
}

document.addEventListener("click", (e) => {
  const wrap = document.getElementById("globalSearchWrap");
  if (wrap && !wrap.contains(e.target)) document.getElementById("globalSearchResults").classList.add("hidden");
});

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(str) {
  return escapeHtml(str);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async function init() {
  try {
    state.status = await api("/api/meta/status");
  } catch (e) {
    /* keep optimistic default; enrich calls will surface the real error */
  }
  if (!state.status.rapid_x_configured && !state.status.youtube_configured) {
    document.getElementById("batchEnrichBtn").disabled = true;
    document.getElementById("batchEnrichBtn").title = "No RAPID_X_API_KEY or YOUTUBE_API_KEY set on the server.";
  }
  await loadMeta();
  await refreshShortlistState();
  await loadCreators();
  await loadHome();
})();
