"use strict";
/* Marketplace Monitor control panel. Hebrew by default, English selectable (Settings). Data is only ever inserted with textContent / createElement (never innerHTML). */
const LANG = document.documentElement.lang === "en" ? "en" : "he";
const L = (he, en) => (LANG === "en" ? en : he);
const LOCALE = LANG === "en" ? "en-GB" : "he-IL";
const $ = (s, r = document) => r.querySelector(s);
let CSRF = "", TIMER = null, ROUTE_TOKEN = 0, LAST_STATE = "";

/* ---------------------------------------------------------------- small toolkit */
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (v === true) e.setAttribute(k, "");
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
  return e;
}
const ICONS = {
  home: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z", search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  list: "M4 6h16M4 12h16M4 18h10", link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  bell: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21a2 2 0 0 0 4 0", gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4",
  star: "M12 2l3 6.9 7.5.7-5.7 5 1.7 7.4L12 18l-6.5 4 1.7-7.4-5.7-5 7.5-.7z", plus: "M12 5v14M5 12h14", check: "M20 6L9 17l-5-5", ext: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14", cart: "M3 4h2l2.4 11h10.2L20 7H6.2M9 20a1 1 0 1 0 0 .01M17 20a1 1 0 1 0 0 .01",
};
function icon(name, fill) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", fill ? "currentColor" : "none"); s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name]); s.appendChild(p);
  return s;
}
const SYM = { ILS: "₪", USD: "$", EUR: "€", GBP: "£", "$": "$", "¥": "¥" };
const fmtMoney = (n, cur) => (n == null ? L("ללא מחיר", "No price") : (cur ? (SYM[cur] || cur + " ") : "") + Number(n).toLocaleString(LOCALE));
const fmtDur = sec => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
const fmtTime = ts => ts ? new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleString(LOCALE, { dateStyle: "short", timeStyle: "short" }) : "—";
const SCANWHY = { timeout: ["חרגה מהזמן המותר", "timed out"], checkpoint: ["פייסבוק ביקשה אימות", "Facebook asked for verification"], crashed: ["הסורק קרס", "the scraper crashed"], stopped: ["בוטלה", "cancelled"] };
const scanWhy = r => (SCANWHY[r] ? L(SCANWHY[r][0], SCANWHY[r][1]) : r);
function ago(ts) {
  if (!ts) return "—";
  const m = Math.max(0, (Date.now() - (typeof ts === "number" ? ts * 1000 : new Date(ts))) / 6e4);
  return m < 2 ? L("ממש עכשיו", "Just now") : m < 60 ? L(`לפני ${Math.round(m)} דקות`, `${Math.round(m)} min ago`) : m < 1440 ? L(`לפני ${Math.round(m / 60)} שעות`, `${Math.round(m / 60)} h ago`) : L(`לפני ${Math.round(m / 1440)} ימים`, `${Math.round(m / 1440)} d ago`);
}
async function api(method, path, body) {
  const opt = { method, headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
  if (method !== "GET") opt.headers["X-CSRF-Token"] = CSRF;
  if (body !== undefined) opt.body = JSON.stringify(body);
  let r;
  try { r = await fetch("/api/" + path, opt); } catch (e) { return { ok: false, status: 0, data: { error: L("אין חיבור לשרת. בדקו שהמחשב והרשת פועלים.", "No connection to the server. Check that the computer and the network are running.") } }; }
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty body */ }
  return { ok: r.ok, status: r.status, data };
}
const errText = d => d.error || (d.errors ? d.errors.map(e => e.message).join(" · ") : L("הפעולה נכשלה", "The action failed"));

function toast(msg, opts = {}) {
  const box = $("#toasts");
  const t = h("div", { class: "toast" + (opts.err ? " err" : "") }, h("span", {}, msg));
  if (opts.action) t.append(h("button", { onclick: () => { t.remove(); opts.action.fn(); } }, opts.action.label));
  box.append(t);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(() => t.remove(), opts.ms || (opts.err ? 7000 : 4000));
}
async function busy(btn, label, fn) {
  const old = btn ? [...btn.childNodes] : [];
  if (btn) { btn.disabled = true; btn.replaceChildren(h("span", { class: "spin" }), label || L("רגע…", "One moment…")); }
  try { return await fn(); } finally { if (btn && btn.isConnected) { btn.disabled = false; btn.replaceChildren(...old); } }
}
function openDialog(title, body, actions) {
  const d = $("#dlg"); d.replaceChildren(h("h2", {}, title), body, actions ? h("div", { class: "dact" }, actions) : null);
  if (!d.open) d.showModal();
  return d;
}
const closeDialog = () => { const d = $("#dlg"); if (d.open) d.close(); };
function confirmDialog(title, text, okLabel, danger) {
  return new Promise(res => {
    const d = openDialog(title, h("p", {}, text), [h("button", { class: danger ? "danger" : "primary", onclick: () => { closeDialog(); res(true); } }, okLabel), h("button", { class: "quiet", onclick: () => { closeDialog(); res(false); } }, L("ביטול", "Cancel"))]);
    d.addEventListener("close", () => res(false), { once: true });
  });
}
function notice(tone, title, text, actions) {
  return h("div", { class: "notice " + tone, role: tone === "bad" ? "alert" : null }, h("div", { class: "grow" }, title ? h("strong", {}, title) : null, text ? h("span", {}, text) : null), actions || null);
}
function field(label, input, hint, error, id) {
  const f = h("div", { class: "field" + (error ? " bad" : "") }, h("label", { for: id || input.id }, label), input, hint ? h("div", { class: "hint" }, hint) : null);
  if (error) f.append(h("div", { class: "err", role: "alert" }, error));
  return f;
}
function setError(fieldEl, msg) {
  fieldEl.classList.toggle("bad", !!msg);
  let e = fieldEl.querySelector(".err");
  if (msg) { if (!e) { e = h("div", { class: "err", role: "alert" }); fieldEl.append(e); } e.textContent = msg; } else if (e) e.remove();
}
function switchEl(id, label, checked, onchange) {
  return h("label", { class: "switch", for: id }, h("input", { type: "checkbox", id, checked, onchange }), h("span", { class: "track" }), h("span", {}, label));
}
function empty(title, text, action) {
  return h("div", { class: "empty card" }, h("h3", {}, title), h("p", {}, text), action || null);
}
function skeleton(n = 3) { return h("div", { class: "stack" }, Array.from({ length: n }, () => h("div", { class: "skel", style: "height:72px" }))); }

/* ---------------------------------------------------------------- shell + routing */
const NAV = [["home", L("בית", "Home"), "home"], ["searches", L("חיפושים", "Searches"), "search"], ["listings", L("מודעות", "Listings"), "list"], ["facebook", L("פייסבוק", "Facebook"), "link"], ["notifications", L("התראות", "Notifications"), "bell"], ["settings", L("הגדרות", "Settings"), "gear"]];
function drawShell(cur, attention) {
  const brand = $("#brand"); if (!brand.firstChild) brand.append(icon("cart"), h("span", {}, L("ניטור מרקטפלייס", "Marketplace Monitor")));
  const n = $("#tabs"); n.replaceChildren();
  for (const [id, label, ic] of NAV) n.append(h("a", { href: "#/" + id, "aria-current": id === cur ? "page" : null, title: label }, icon(ic), h("span", {}, label), id === "facebook" && attention ? h("i", { class: "dot", title: L("דורש טיפול", "Needs attention") }) : null));
}
let ATTENTION = false;
async function refreshAttention() {
  const r = await api("GET", "status");
  if (r.ok) { const a = ["warn", "bad"].includes(r.data.connection.severity) && r.data.connection.code !== "not_connected"; if (a !== ATTENTION) { ATTENTION = a; drawShell(currentRoute(), a); } }
}
const currentRoute = () => (location.hash.replace(/^#\/?/, "") || "home").split("?")[0];
const PAGES = {};
async function route() {
  clearInterval(TIMER); TIMER = null; closeDialog();
  const [seg, arg] = currentRoute().split("/");
  const id = PAGES[seg] ? seg : "home";
  const token = ++ROUTE_TOKEN;
  drawShell(id === "search" ? "searches" : id, ATTENTION);
  const main = $("#main"); main.replaceChildren(skeleton());
  document.title = ({ searches: L("החיפושים שלי", "My searches"), search: L("מודעות לפי חיפוש", "Listings by search") }[id] || (NAV.find(x => x[0] === id) || NAV[0])[1]) + L(" · ניטור מרקטפלייס", " · Marketplace Monitor");
  await PAGES[id](main, () => token === ROUTE_TOKEN, arg ? decodeURIComponent(arg) : null);
  window.scrollTo(0, 0);
}
const go = r => { location.hash = "#/" + r; };
function head(title, ...actions) { return h("div", { class: "page-head" }, h("h1", { class: "grow" }, title), actions); }

/* ---------------------------------------------------------------- HOME */
function scanBox(d) {
  const ss = d.scan_status;
  if (ss) {
    return h("section", { class: "card stack-s", id: "scan-card", "aria-live": "polite" }, h("h2", {}, L("סריקה ידנית בתהליך", "Manual scan in progress")),
      h("div", { class: "progress", role: "progressbar", "aria-label": ss.label }, h("i")),
      h("p", { id: "scan-label" }, h("strong", {}, ss.label)),
      h("div", { class: "small muted", id: "scan-counts" }, L(`חיפושים: ${Math.min(ss.searches, ss.total_searches)}/${ss.total_searches} · מודעות שנבדקו: ${ss.items_opened} · דורגו: ${ss.ai_rated} · עברו את הסף: ${ss.passed} · זמן: ${fmtDur(ss.elapsed)}`, `Searches: ${Math.min(ss.searches, ss.total_searches)}/${ss.total_searches} · listings reviewed: ${ss.items_opened} · rated: ${ss.ai_rated} · passed the threshold: ${ss.passed} · time: ${fmtDur(ss.elapsed)}`)),
      ss.last_event ? h("div", { class: "small muted", id: "scan-last-event" }, L(`פעולה אחרונה: ${ss.last_event} (לפני ${fmtDur(ss.last_event_age)})`, `Last step: ${ss.last_event} (${fmtDur(ss.last_event_age)} ago)`)) : null,
      ss.ai_failed ? notice("warn", "", L(`הדירוג החכם נכשל ב-${ss.ai_failed} מודעות. הן נשמרות ללא דירוג.`, `AI rating failed for ${ss.ai_failed} listings. They are kept unrated.`)) : null,
      ss.item_failed ? notice("warn", "", L(`${ss.item_failed} מודעות לא נקראו.`, `${ss.item_failed} listings could not be read.`)) : null,
      ss.error ? notice("bad", ss.error.title, ss.error.detail) : null,
      ss.stuck ? notice("warn", L("נראה שהסריקה תקועה", "The scan looks stuck"), L(`אין פעילות כבר ${Math.round(ss.idle / 60)} דקות. אפשר לבטל ולנסות שוב.`, `No activity for ${Math.round(ss.idle / 60)} minutes. You can cancel and try again.`)) : null);
  }
  return null;
}

// Scan activity: the latest scan in plain words + the last few scans (manual and automatic). Everything comes from the real scan counters.
function activityCard(d) {
  const hist = (d.scan_history && d.scan_history.length) ? d.scan_history : (d.last_scan && d.last_scan_outcome ? [{ ...d.last_scan, kind: "manual", outcome: d.last_scan_outcome }] : []);
  if (!hist.length) return null;
  const [last, ...rest] = hist, o = last.outcome, kindLabel = k => (k === "monitor" ? L("אוטומטית", "automatic") : L("ידנית", "manual"));
  const okish = ["done", "no_results", "no_listings", "known"].includes(o.code);
  const title = okish ? L("הסריקה הסתיימה", "Scan completed") : o.code === "cancelled" ? L("הסריקה בוטלה", "Scan cancelled") : L("הסריקה לא הסתיימה כראוי", "The scan did not finish properly");
  const tone = o.level === "bad" ? "bad" : o.level === "warn" ? "warn" : o.level === "ok" ? "ok" : "info";
  const tiles = [[last.reviewed, L("מודעות ייחודיות שנבדקו", "unique listings reviewed")], [last.ai_rated, L("הערכות AI", "AI evaluations")], last.ai_cached ? [last.ai_cached, L("הערכות מהזיכרון (ללא פנייה ל-AI)", "evaluations from cache (no AI call)")] : null, last.ai_failed ? [last.ai_failed, L("הערכות AI שנכשלו", "AI evaluations failed")] : null,
    [last.below, L("הערכות שנדחו (מתחת לסף)", "evaluations rejected (below threshold)")], last.ingest_errors ? [last.ingest_errors, L("שגיאות שמירה", "storage errors")] : null, [last.passed, L("מודעות מתאימות", "matching listings")], [last.forwarded, L("הועברו ללוח הבקרה", "forwarded")], [last.saved, L("נשמרו", "saved")]].filter(Boolean);
  const why = o.code === "no_results" && last.reviewed ? L(`נמצאו ${last.reviewed} מודעות, אך כל ${last.ai_rated} הערכות ה-AI היו מתחת לסף הרלוונטיות.`, `${last.reviewed} listings were found, but all ${last.ai_rated} AI evaluations were below the relevance threshold.`) : "";
  const tm = last.timing, dur = last.duration != null ? last.duration : (last.ended && last.started ? last.ended - last.started : null);
  const when = last.started ? h("div", { class: "small muted", id: "scan-when" }, L(`התחילה ${fmtTime(last.started)} · הסתיימה ${fmtTime(last.ended || last.at)}${dur != null ? " · משך " + fmtDur(dur) : ""}`, `Started ${fmtTime(last.started)} · ended ${fmtTime(last.ended || last.at)}${dur != null ? " · took " + fmtDur(dur) : ""}`)) : null;
  const timing = tm ? h("div", { class: "small muted", id: "scan-timing" }, L(`לאן הלך הזמן: דפי מודעות ${fmtDur(tm.details)} (${tm.pages_opened} נפתחו) · הערכת AI ${fmtDur(tm.ai)}${tm.ai_wait ? ` (מתוכם המתנה למכסה ${fmtDur(tm.ai_wait)})` : ""} · שליחה ושמירה ${fmtDur(tm.notify)} · חיפוש, דפדפן והתחברות ${fmtDur(tm.other)}`,
    `Where the time went: listing pages ${fmtDur(tm.details)} (${tm.pages_opened} opened) · AI evaluation ${fmtDur(tm.ai)}${tm.ai_wait ? ` (incl. ${fmtDur(tm.ai_wait)} waiting for quota)` : ""} · forwarding and storage ${fmtDur(tm.notify)} · search, browser and login ${fmtDur(tm.other)}`)) : null;
  const rt = last.ratings || {}, hist5 = ["5", "4", "3", "2", "1"].filter(k => rt[k]).map(k => `${k}★×${rt[k]}`).join(" · ");
  const diag = (hist5 || (last.examples || []).length) ? h("details", { class: "small", id: "scan-diag" }, h("summary", {}, L("למה נדחו? דירוגי AI ודוגמאות", "Why rejected? AI ratings and examples")),
    hist5 ? h("div", { class: "muted" }, L("פיזור דירוגים: ", "Rating spread: ") + hist5) : null,
    (last.examples || []).length ? h("ul", {}, last.examples.map(e => h("li", {}, h("b", {}, `${e.rating}★ `), e.title, h("div", { class: "muted" }, e.reason)))) : null) : null;
  const row = r => h("div", { class: "hrow" }, h("span", { class: "when small muted" }, fmtTime(r.at)), h("span", { class: "chip " + (r.outcome.level === "bad" ? "bad" : r.outcome.level === "warn" ? "warn" : r.outcome.level === "ok" ? "ok" : "") }, kindLabel(r.kind)),
    h("span", { class: "grow" }, r.outcome.message), r.reviewed || r.outcome.code === "done" ? h("span", { class: "small muted" }, L(`${r.reviewed} נבדקו · ${r.passed} מתאימות · ${r.saved} נשמרו${r.duration ? " · " + fmtDur(r.duration) : ""}`, `${r.reviewed} reviewed · ${r.passed} matching · ${r.saved} saved${r.duration ? " · " + fmtDur(r.duration) : ""}`)) : null);
  return h("section", { class: "card stack activity", id: "scan-activity", "aria-labelledby": "act-h" },
    h("h2", { id: "act-h" }, L("פעילות סריקות", "Scan activity")),
    h("div", { class: "notice " + tone, id: "scan-outcome", role: o.level === "bad" ? "alert" : null }, h("div", { class: "grow" },
      h("strong", {}, title + " · " + fmtTime(last.at) + " · " + kindLabel(last.kind)), h("div", { id: "scan-outcome-msg" }, o.message), why ? h("div", { class: "small", id: "scan-why" }, why) : null, when, timing,
      o.detail && !okish ? h("div", { class: "small muted", id: "scan-outcome-detail" }, o.detail) : null)),
    last.reviewed || okish ? h("div", { class: "stats", id: "scan-stats" }, tiles.map(([n, l]) => h("div", { class: "stat" }, h("b", {}, n), h("span", {}, l)))) : null,
    diag,
    rest.length ? h("div", { class: "hist", id: "scan-history" }, h("h3", {}, L("סריקות אחרונות", "Recent scans")), rest.map(row)) : null);
}

let nextMs = 10000;
PAGES.home = async function (main, alive) {
  async function draw(first) {
    const r = await api("GET", "home");
    if (!alive()) return;
    if (!r.ok) { main.replaceChildren(notice("bad", L("לא הצלחנו לטעון", "Could not load"), errText(r.data))); return; }
    const d = r.data, st = d.state, m = d.metrics;
    nextMs = d.scan_status ? 3000 : 10000;
    if (LAST_STATE === "scanning" && st.code !== "scanning" && d.last_scan_outcome) toast(d.last_scan_outcome.message, { err: d.last_scan_outcome.level === "bad", ms: 8000 });
    LAST_STATE = st.code;
    const primary = st.primary;
    const hero = h("section", { class: "card hero " + st.tone, "aria-labelledby": "hero-h" },
      h("h1", { id: "hero-h" }, h("span", { class: "tone-dot" }), st.headline),
      st.sub ? h("p", { class: "muted" }, st.sub) : null,
      h("div", { class: "row" },
        h("button", { class: "primary big", onclick: e => primaryAction(e.currentTarget, primary, d) }, primary.label),
        h("button", { class: "big", id: "scan-btn", disabled: !d.scan.available, title: d.scan.available ? "" : d.scan.reason, onclick: e => scanDialog(e.currentTarget) }, L("סריקה ידנית", "Manual scan")),
        d.scraper.running && st.code !== "scanning" ? h("button", { class: "quiet", onclick: e => stopDialog(e.currentTarget) }, L("עצירה", "Stop")) : null));
    const tiles = h("div", { class: "grid g3" },
      h("a", { class: "card tile", href: "#/listings" }, h("div", { class: "num" }, m.new), h("div", { class: "lbl" }, L("מודעות חדשות מאז הביקור האחרון", "New listings since your last visit"))),
      h("a", { class: "card tile", href: "#/listings" }, h("div", { class: "num" }, m.today), h("div", { class: "lbl" }, L(`נמצאו היום${m.great_today ? ` · ${m.great_today} מצוינות` : ""}`, `Found today${m.great_today ? ` · ${m.great_today} great` : ""}`))),
      h("div", { class: "card tile" }, h("div", { class: "num" }, m.last_scan ? ago(m.last_scan) : "—"), h("div", { class: "lbl" }, L("סריקה אחרונה", "Last scan"))));
    if (!d.scan.available) hero.append(h("div", { class: "small muted", id: "scan-hint" }, L("סריקה ידנית לא זמינה כרגע: ", "Manual scan is unavailable right now: ") + d.scan.reason));
    main.replaceChildren(h("div", { class: "stack" },
      hero,
      scanBox(d),
      activityCard(d),
      ...d.warnings.map(w => notice(w.severity === "bad" ? "bad" : "warn", w.title, w.detail, w.action && w.action !== "start" ? h("button", { class: "sm", onclick: () => go(w.action === "reconnect" || w.action === "connect" || w.action === "check" || w.action === "language" || w.action === "save_login" ? "facebook" : w.action === "searches" ? "searches" : w.action) }, L("טיפול", "Fix")) : null)),
      !d.setup.done && !d.setup.dismissed ? setupCard(d.setup) : null,
      tiles,
      h("section", {}, h("div", { class: "row", style: "margin-bottom:12px" }, h("h2", { class: "grow" }, L("מציאות אחרונות", "Latest finds")), h("a", { class: "btn quiet", href: "#/listings" }, L("כל המודעות", "All listings"))),
        d.latest.length ? h("div", { class: "feed" }, d.latest.map(x => listingCard(x, { compact: true }))) : empty(L("עדיין אין מודעות", "No listings yet"), d.scraper.running ? L("המעקב פעיל. מודעות מתאימות יופיעו כאן ברגע שנמצא אותן.", "Monitoring is on. Matching listings will appear here as soon as they are found.") : L("כשהמעקב יפעל, מודעות מתאימות יופיעו כאן.", "When monitoring runs, matching listings will appear here.")))));
  }
  function setupCard(setup) {
    const cur = setup.steps.find(x => !x.done);
    return h("section", { class: "card stack-s", "aria-labelledby": "setup-h" }, h("div", { class: "row" }, h("h2", { id: "setup-h", class: "grow" }, L("הגדרה ראשונית", "Getting started")), h("button", { class: "quiet sm", onclick: async () => { await api("POST", "setup", { setup_dismissed: true }); draw(); } }, L("הסתר", "Hide"))),
      h("div", { class: "steps" }, setup.steps.map((s, i) => h("div", { class: "step" + (s.done ? " done" : s === cur ? " cur" : "") },
        h("div", { class: "ico" }, s.done ? icon("check") : String(i + 1)), h("div", {}, h("strong", {}, s.title), h("div", { class: "small muted" }, s.hint)),
        !s.done && s === cur && s.route !== "home" ? h("button", { class: "primary sm", onclick: () => go(s.route) }, L("להמשך", "Continue")) : null))));
  }
  async function primaryAction(btn, p, d) {
    if (p.action === "goto") return go(p.route);
    if (p.action === "pause") return pauseDialog();
    if (p.action === "start") return startDialog(btn, d);
    if (p.action === "stop") return stopDialog(btn);
  }
  function startDialog(btn, d) {
    const n = d.setup.steps.find(x => x.id === "search");
    openDialog(L("להתחיל מעקב?", "Start monitoring?"), h("div", { class: "stack" },
      h("p", {}, L("הסורק ייכנס לפייסבוק עם החיבור שלכם ויחפש את החיפושים הפעילים, פעם בשעה בערך. כשימצא מודעה טובה תקבלו התראה.", "The scraper will log in to Facebook with your saved connection and check your active searches about once an hour. You get an alert when it finds a good listing.")),
      h("ul", { class: "small", style: "margin:0;padding-inline-start:20px" }, h("li", {}, L("אפשר להשהות או לעצור בכל רגע.", "You can pause or stop at any time.")), h("li", {}, L("אם פייסבוק תבקש אימות, המעקב ייעצר ותקבלו הודעה. אנחנו לא עוקפים אימותים.", "If Facebook asks for verification, monitoring stops and you get a message. We never bypass verification.")), h("li", {}, L("פייסבוק עלולה להגביל חשבונות שמשתמשים באוטומציה. מומלץ חשבון משני.", "Facebook may restrict accounts that use automation. A secondary account is recommended."))),
      notice("warn", "", L("פעולה זו מתחילה סריקה אמיתית בפייסבוק.", "This starts a real scan on Facebook."))),
      [h("button", { class: "primary big", onclick: async e => { await busy(e.currentTarget, L("מתחיל…", "Starting…"), async () => { const x = await api("POST", "scraper", { action: "start", confirm: true }); if (x.ok) { closeDialog(); toast(L("המעקב התחיל", "Monitoring started")); draw(); } else toast(errText(x.data), { err: true }); }); } }, L("כן, להתחיל", "Yes, start")), h("button", { class: "quiet", onclick: closeDialog }, L("ביטול", "Cancel"))]);
  }
  function scanDialog(btn) {
    openDialog(L("לבצע סריקה ידנית?", "Run a manual scan?"), h("div", { class: "stack" },
      h("p", {}, L("הסורק יבדוק את החיפושים הפעילים פעם אחת, עכשיו, ויעצור מעצמו כשיסיים. המעקב הרציף נשאר כבוי.", "The scraper will check your active searches once, right now, and then stop by itself. Continuous monitoring stays off.")),
      h("ul", { class: "small", style: "margin:0;padding-inline-start:20px" }, h("li", {}, L("זה לוקח בדרך כלל כמה דקות. אפשר לבטל בכל רגע.", "It usually takes a few minutes. You can cancel at any time.")), h("li", {}, L("התראות על מציאות נשלחות כרגיל.", "Alerts for good finds are sent as usual.")), h("li", {}, L("אם פייסבוק תבקש אימות, הסריקה תיעצר ותקבלו הודעה. אנחנו לא עוקפים אימותים.", "If Facebook asks for verification, the scan stops and you get a message. We never bypass verification."))),
      notice("warn", "", L("פעולה זו מתחילה סריקה אמיתית בפייסבוק.", "This starts a real scan on Facebook."))),
      [h("button", { class: "primary big", onclick: async e => { await busy(e.currentTarget, L("מתחיל…", "Starting…"), async () => { const x = await api("POST", "scraper", { action: "scan", confirm: true }); if (x.ok) { closeDialog(); toast(L("הסריקה התחילה", "Scan started")); draw(); } else toast(errText(x.data), { err: true }); }); } }, L("כן, לסרוק עכשיו", "Yes, scan now")), h("button", { class: "quiet", onclick: closeDialog }, L("ביטול", "Cancel"))]);
  }
  function pauseDialog() {
    const sel = h("select", { id: "ph" }, [[1, L("שעה", "1 hour")], [3, L("3 שעות", "3 hours")], [6, L("6 שעות", "6 hours")], [12, L("12 שעות", "12 hours")], [24, L("יממה", "1 day")]].map(([v, l]) => h("option", { value: v }, l)));
    openDialog(L("השהיית המעקב", "Pause monitoring"), h("div", {}, field(L("להשהות ל…", "Pause for…"), sel, L("המעקב יחזור לפעול אוטומטית בתום הזמן.", "Monitoring resumes automatically when the time is up.")), ), [h("button", { class: "primary", onclick: async e => { await busy(e.currentTarget, L("משהה…", "Pausing…"), async () => { const x = await api("POST", "scraper", { action: "pause", hours: +sel.value }); if (x.ok) { closeDialog(); toast(L("המעקב הושהה", "Monitoring paused")); draw(); } else toast(errText(x.data), { err: true }); }); } }, L("השהיה", "Pause")), h("button", { class: "quiet", onclick: closeDialog }, L("ביטול", "Cancel"))]);
  }
  async function stopDialog(btn) {
    const scanning = LAST_STATE === "scanning";
    if (!await confirmDialog(scanning ? L("לבטל את הסריקה?", "Cancel the scan?") : L("לעצור את המעקב?", "Stop monitoring?"), scanning ? L("הסריקה תיעצר עכשיו. מודעות שכבר נמצאו נשמרות.", "The scan stops now. Listings found so far are kept.") : L("הסריקה תיעצר ולא תתחדש עד שתתחילו אותה שוב.", "Scanning stops and will not resume until you start it again."), scanning ? L("ביטול הסריקה", "Cancel scan") : L("עצירה", "Stop"))) return;
    const x = await api("POST", "scraper", { action: "stop" }); if (x.ok) { toast(scanning ? L("הסריקה בוטלה", "Scan cancelled") : L("המעקב נעצר", "Monitoring stopped")); draw(); } else toast(errText(x.data), { err: true });
  }

  const tick = async () => { await draw(false); if (alive()) TIMER = setTimeout(tick, nextMs); };
  await draw(true);
  if (alive()) TIMER = setTimeout(tick, nextMs);
};

/* ---------------------------------------------------------------- LISTINGS */
const REASONS = [["wrong", L("פריט או מפרט שגוי", "Wrong item or spec")], ["price", L("יקר מדי", "Too expensive")], ["stale", L("נמכר או לא עדכני", "Sold or stale")], ["bad", L("פגום או באיכות ירודה", "Broken or poor quality")], ["scam", L("מוכר חשוד", "Suspicious seller")], ["other", L("סיבה אחרת", "Another reason")]];
const LF = { q: "", quick: "all", product: "", sort: "new", min: "", max: "", loc: "", drops: false, shown: 30, bulk: false, picked: new Set() };

function blockDialog(urls, after) {
  const d = openDialog(urls.length === 1 ? L("למה המודעה לא רלוונטית?", "Why is this listing not relevant?") : L(`למה ${urls.length} המודעות לא רלוונטיות?`, `Why are these ${urls.length} listings not relevant?`), h("div", { class: "stack-s" },
    h("p", { class: "muted small" }, L("המודעה תוסתר ולא תופיע שוב. הסיבה עוזרת לדירוג החכם ללמוד מה לא מתאים.", "The listing will be hidden and will not show up again. The reason helps the AI rating learn what does not fit.")),
    REASONS.map(([k, l]) => h("button", { style: "width:100%;justify-content:flex-start", onclick: async () => {
      closeDialog();
      const x = await api("POST", "listings/action", { urls, action: "block", reason: k });
      if (!x.ok) return toast(errText(x.data), { err: true });
      toast(urls.length === 1 ? L("המודעה הוסתרה", "Listing hidden") : L(`${urls.length} מודעות הוסתרו`, `${urls.length} listings hidden`), { ms: 9000, action: { label: L("ביטול פעולה", "Undo"), fn: async () => { await api("POST", "listings/action", { urls, action: "unblock" }); toast(L("השחזור בוצע", "Restored")); after(); } } });
      after();
    } }, l))), [h("button", { class: "quiet", onclick: closeDialog }, L("ביטול", "Cancel"))]);
  return d;
}

function priceChange(x) {
  // only when the stored history shows a real change (server checks it); never for a missing price
  if (!x.price_dir || x.prev_price == null || x.price == null) return null;
  const down = x.price_dir === "down";
  return [h("span", { class: "was", "aria-label": L("מחיר קודם ", "Previous price ") + fmtMoney(x.prev_price, x.currency) }, fmtMoney(x.prev_price, x.currency)),
    h("span", { class: "chip " + (down ? "down" : "up") }, down ? L("📉 ירידת מחיר", "📉 Price drop") : L("📈 המחיר עלה", "📈 Price went up"))];
}

function deleteDialog(urls, after) {
  const n = urls.length;
  confirmDialog(n === 1 ? L("למחוק את המודעה?", "Delete this listing?") : L(`למחוק ${n} מודעות?`, `Delete ${n} listings?`), L("המודעות יימחקו מההיסטוריה. אם הן יימצאו שוב בסריקה הן עשויות להופיע מחדש. אי אפשר לבטל.", "The listings are removed from the history. If a scan finds them again they may reappear. This cannot be undone."), L("מחיקה", "Delete"), true).then(async ok => {
    if (!ok) return;
    const x = await api("POST", "listings/action", { urls, action: "delete" });
    if (x.ok) { toast(L(`${x.data.removed} מודעות נמחקו`, `${x.data.removed} listings deleted`)); after(); } else toast(errText(x.data), { err: true });
  });
}

function cleanupDialog(after) {
  const days = h("select", { id: "cu-days", "aria-label": L("גיל מודעות", "Listing age") }, [7, 14, 30, 60, 90].map(d => h("option", { value: d, selected: d === 30 }, L(`לא נראו ${d} ימים`, `not seen for ${d} days`))));
  const rows = [["orphans", L("מודעות של חיפושים שנמחקו", "Listings of deleted searches")], ["stale", L("מודעות מגרסה ישנה של חיפוש שנערך", "Listings from an older version of an edited search")], ["older", null]];
  const box = h("div", { class: "stack-s" });
  const count = {};
  async function refresh() {
    for (const [k] of rows) { const x = await api("POST", "listings/cleanup", { kind: k, days: k === "older" ? +days.value : undefined }); count[k] = x.ok ? x.data.count : 0; }
    box.replaceChildren(...rows.map(([k, label]) => h("div", { class: "svc" }, h("div", { class: "grow" }, k === "older" ? h("div", {}, h("div", {}, L("מודעות ישנות", "Old listings")), days) : label, h("div", { class: "small muted", id: "cu-n-" + k }, L(`${count[k]} מודעות`, `${count[k]} listings`))),
      h("button", { class: "sm danger", disabled: !count[k], onclick: async () => {
        if (!await confirmDialog(L(`למחוק ${count[k]} מודעות?`, `Delete ${count[k]} listings?`), L("אי אפשר לבטל. מודעות שמורות לא נמחקות.", "This cannot be undone. Saved listings are never deleted."), L("מחיקה", "Delete"), true)) return cleanupDialog(after);
        const x = await api("POST", "listings/cleanup", { kind: k, days: k === "older" ? +days.value : undefined, confirm: true });
        toast(x.ok ? L(`${x.data.removed} מודעות נמחקו`, `${x.data.removed} listings deleted`) : errText(x.data), { err: !x.ok }); after();
      } }, L("מחיקה", "Delete")))));
  }
  days.addEventListener("change", refresh);
  openDialog(L("ניקוי מודעות", "Clean up listings"), h("div", { class: "stack" }, h("p", { class: "muted small" }, L("כלום לא נמחק אוטומטית. בחרו מה לנקות, תראו כמה מודעות ייפגעו, ותתבקשו לאשר. מודעות שמורות לא נמחקות.", "Nothing is deleted automatically. Choose what to clean up, see how many listings are affected, and confirm. Saved listings are never deleted.")), box), [h("button", { class: "quiet", onclick: closeDialog }, L("סגירה", "Close"))]);
  refresh();
}

// Availability from the bounded re-checks: "sold" only when Facebook itself says so; "unavailable" after repeated looks; never from one failed look.
function availChip(x) {
  if (x.availability === "sold") return h("span", { class: "chip gone", title: L("פייסבוק מסמנת את המודעה כנמכרה", "Facebook marks this listing as sold") }, L("נמכר", "Sold"));
  if (x.availability === "unavailable") return h("span", { class: "chip gone", title: L("המודעה לא מוצגת יותר בפייסבוק (נבדק כמה פעמים). ייתכן שנמחקה, פגה או נמכרה.", "Facebook no longer shows this listing (checked several times). It may have been removed, expired or sold.") }, L("לא זמינה", "Unavailable"));
  if (x.availability === "active" && x.avail_checked) return h("span", { class: "chip low", title: L("נבדק ", "Checked ") + fmtTime(x.avail_checked) }, L("זמינה", "Available"));
  return null;
}
const gone = x => x.availability === "sold" || x.availability === "unavailable";

function listingCard(x, o = {}) {
  // Hebrew mode with an explanation written before explanations followed the panel language: show it, labelled, rather than hide it
  const reason = x.reason || "", heb = /[\u0590-\u05FF]/.test(reason), oldLang = LANG === "he" && reason && !heb;
  const long = reason.length > 140;
  const reasonBox = x.unrated ? h("div", { class: "reason" }, "⚠️ " + L("הדירוג החכם לא הצליח לדרג את המודעה (תקלה זמנית), לכן זה לא דירוג נמוך. כדאי להסתכל בעצמכם.", "The AI rating could not rate this listing (a temporary problem), so this is not a low rating. Take a look yourself."))
    : (reason ? h("div", { class: "reason", dir: "auto" }, oldLang ? h("span", { class: "src" }, "הסבר מקורי באנגלית") : null,
      h("span", {}, long ? reason.slice(0, 140) + "… " : reason), long ? h("button", { onclick: e => { const s = e.currentTarget.previousSibling; s.textContent = reason + " "; e.currentTarget.remove(); } }, L("קרא עוד", "Read more")) : null) : null);
  const picked = LF.picked.has(x.url);
  const card = h("article", { class: "card lcard" + (picked ? " picked" : "") + (x.new ? " is-new" : "") + (gone(x) ? " is-gone" : ""), "data-new": x.new ? "1" : null },
    h("div", { class: "row badges" },
      LF.bulk && !o.compact ? h("input", { type: "checkbox", checked: picked, "aria-label": L("בחירת מודעה", "Select listing"), onchange: e => { e.target.checked ? LF.picked.add(x.url) : LF.picked.delete(x.url); o.onChange && o.onChange(true); } }) : null,
      x.new ? h("span", { class: "chip new" }, L("🆕 מודעה חדשה", "🆕 New listing")) : null, availChip(x),
      h("span", { class: "chip " + x.tone }, x.score ? `${x.score}/5 · ${x.verdict}` : "⚠️ " + x.verdict), h("span", { class: "small muted when" }, ago(x.found_at))),
    h("a", { class: "title", href: x.url, target: "_blank", rel: "noopener noreferrer", dir: "auto", title: x.title }, x.title),
    h("div", { class: "pricerow" }, h("span", { class: "price" }, fmtMoney(x.price, x.currency)), priceChange(x)),
    h("div", { class: "meta" }, x.loc ? h("span", { dir: "auto" }, "📍 " + x.loc) : null, x.product ? h("span", {}, L("חיפוש: ", "Search: ") + x.product) : null),
    reasonBox,
    h("div", { class: "acts" },
      h("a", { class: "btn sm primary", href: x.url, target: "_blank", rel: "noopener noreferrer" }, icon("ext"), L("פתיחה בפייסבוק", "Open on Facebook")),
      o.compact ? null : h("button", { class: "sm", "aria-pressed": x.saved, onclick: async () => { const y = await api("POST", "listings/action", { urls: [x.url], action: x.saved ? "unsave" : "save" }); if (y.ok) { x.saved = !x.saved; toast(x.saved ? L("נשמר", "Saved") : L("הוסר מהשמורות", "Removed from saved")); o.onChange && o.onChange(); } } }, icon("star", x.saved), x.saved ? L("שמור", "Saved") : L("שמירה", "Save")),
      o.compact ? null : h("button", { class: "sm quiet", onclick: () => blockDialog([x.url], () => o.onChange && o.onChange(false, true)) }, L("לא רלוונטי", "Not relevant")),
      o.compact ? null : h("button", { class: "sm quiet", onclick: () => deleteDialog([x.url], () => o.onChange && o.onChange(false, true)) }, L("מחיקה", "Delete"))));
  return card;
}

// One listings view for both pages: all searches together (FIXED = null) or only the listings of one saved search (FIXED = its id).
// A listing can match several searches, so the filter uses the list of searches stored with each listing (product_ids), never a copy of the listing.
async function listingsView(main, alive, FIXED) {
  const F = FIXED ? { q: "", quick: "all", product: FIXED, sort: "new", min: "", max: "", loc: "", drops: false, shown: 30, bulk: false, picked: new Set() } : LF;
  const r = await api("GET", "listings");
  if (!alive()) return;
  if (!r.ok) { main.replaceChildren(notice("bad", L("לא הצלחנו לטעון את המודעות", "Could not load the listings"), errText(r.data))); return; }
  const scope = a => (FIXED ? a.filter(x => (x.product_ids || [x.product_id]).includes(FIXED)) : a);
  let ALL = scope(r.data.listings); const products = r.data.products;
  const cur = FIXED ? products.find(p => p.id === FIXED) : null;
  if (FIXED && !cur) { main.replaceChildren(h("div", { class: "stack" }, notice("warn", L("החיפוש לא נמצא", "Search not found"), L("יכול להיות שהחיפוש נמחק.", "It may have been deleted.")), h("div", {}, h("a", { class: "btn", href: "#/searches" }, L("חזרה לחיפושים", "Back to searches"))))); return; }
  api("POST", "listings/action", { action: "seen", urls: [] });  // later visits count only what is newer than now
  const ctl = h("div", { class: "stack" });
  const feed = h("div", {});
  main.replaceChildren(h("div", { class: "stack" }, FIXED ? h("div", { class: "stack-s" }, h("div", { class: "row" }, h("a", { class: "btn quiet", id: "back-all", href: "#/listings" }, L("← כל המודעות", "← All listings")), h("a", { class: "btn quiet", href: "#/searches" }, L("← החיפושים שלי", "← My searches"))),
    head(L(`מודעות: ${cur.name}`, `Listings: ${cur.name}`)), h("p", { class: "muted small", id: "search-view-note" }, L("מוצגות רק המודעות שנמצאו עבור החיפוש הזה. מודעה שמתאימה לכמה חיפושים מופיעה בכל אחד מהם.", "Only the listings found for this search are shown. A listing that matches several searches appears in each of them."))) : head(L("מודעות", "Listings")), ctl, feed));
  function filtered() {
    const q = F.q.trim().toLowerCase();
    let a = ALL.filter(x => (!q || (x.title + " " + x.loc + " " + x.reason + " " + (x.product || "")).toLowerCase().includes(q)) && (!F.product || (x.product_ids || [x.product_id]).includes(F.product)) && (!F.loc || x.loc === F.loc)
      && (F.min === "" || (x.price != null && x.price >= +F.min)) && (F.max === "" || (x.price != null && x.price <= +F.max)) && (!F.drops || x.drop) && (F.gone || !gone(x)));
    const quick = { new: x => x.new, great: x => x.great, saved: x => x.saved }[F.quick];
    if (quick) a = a.filter(quick);
    const key = { new: (x, y) => y.found_at - x.found_at, price_asc: (x, y) => (x.price ?? 1e12) - (y.price ?? 1e12), price_desc: (x, y) => (y.price ?? -1) - (x.price ?? -1), score: (x, y) => y.score - x.score || y.found_at - x.found_at }[F.sort];
    return a.sort(key);
  }
  async function reload() { const y = await api("GET", "listings"); if (y.ok) { ALL = scope(y.data.listings); drawControls(); draw(); } }
  function drawControls() {
    const cnt = { all: ALL.length, new: ALL.filter(x => x.new).length, great: ALL.filter(x => x.great).length, saved: ALL.filter(x => x.saved).length };
    const qi = h("input", { type: "search", id: "lq", value: F.q, placeholder: L("חיפוש לפי כותרת, מקום או הערה", "Search by title, place or note"), "aria-label": L("חיפוש", "Search"), oninput: e => { F.q = e.target.value; F.shown = 30; draw(true); } });
    const quick = h("div", { class: "seg", role: "group", "aria-label": L("סינון מהיר", "Quick filter") }, [["all", L("הכול", "All")], ["new", L("חדשות", "New")], ["great", L("מצוינות", "Great")], ["saved", L("שמורות", "Saved")]].map(([k, l]) => h("button", { "aria-pressed": F.quick === k, onclick: () => { F.quick = k; F.shown = 30; drawControls(); draw(); } }, l, h("span", { class: "n" }, cnt[k]))));
    const prod = h("select", { "aria-label": L("מוצר", "Search"), onchange: e => { F.product = e.target.value; draw(); } }, h("option", { value: "" }, L("כל החיפושים", "All searches")), products.map(p => h("option", { value: p.id, selected: F.product === p.id }, p.name)));
    const sort = h("select", { "aria-label": L("מיון", "Sort"), onchange: e => { F.sort = e.target.value; draw(); } }, [["new", L("החדשות קודם", "Newest first")], ["price_asc", L("מחיר: מהזול ליקר", "Price: low to high")], ["price_desc", L("מחיר: מהיקר לזול", "Price: high to low")], ["score", L("הדירוג הגבוה קודם", "Highest rating first")]].map(([v, l]) => h("option", { value: v, selected: F.sort === v }, l)));
    const locs = [...new Set(ALL.map(x => x.loc).filter(Boolean))].sort((a, b) => a.localeCompare(b, "he"));
    const more = h("details", { class: "more", open: !!(F.min || F.max || F.loc || F.drops || F.gone) }, h("summary", {}, L("סינון נוסף", "More filters")),
      h("div", { class: "grid g3", style: "margin-top:8px" },
        field(L("מחיר מינימום", "Minimum price"), h("input", { type: "number", min: 0, id: "fmin", value: F.min, oninput: e => { F.min = e.target.value; draw(true); } })),
        field(L("מחיר מקסימום", "Maximum price"), h("input", { type: "number", min: 0, id: "fmax", value: F.max, oninput: e => { F.max = e.target.value; draw(true); } })),
        field(L("מיקום", "Location"), h("select", { id: "floc", onchange: e => { F.loc = e.target.value; draw(); } }, h("option", { value: "" }, L("כל המקומות", "All locations")), locs.map(l => h("option", { value: l, selected: F.loc === l }, l))))),
      switchEl("fdrop", L("רק מודעות שהמחיר שלהן ירד", "Only listings whose price dropped"), F.drops, e => { F.drops = e.target.checked; draw(); }),
      switchEl("fgone", L(`להציג גם מודעות שנמכרו או אינן זמינות (${ALL.filter(gone).length})`, `Also show sold or unavailable listings (${ALL.filter(gone).length})`), !!F.gone, e => { F.gone = e.target.checked; draw(); }));
    ctl.replaceChildren(h("div", { class: "card stack-s" }, h("div", { class: "row" }, h("div", { class: "grow", style: "min-width:220px" }, qi), FIXED ? null : prod, sort), h("div", { class: "row" }, quick, h("span", { class: "grow" }), h("button", { class: "sm", onclick: () => cleanupDialog(() => reload()) }, L("ניקוי מודעות", "Clean up")), h("button", { class: "sm", "aria-pressed": F.bulk, onclick: () => { F.bulk = !F.bulk; F.picked.clear(); drawControls(); draw(); } }, F.bulk ? L("סיום בחירה", "Done selecting") : L("בחירת כמה", "Select several"))), more));
    if (document.activeElement && document.activeElement.id === "lq") qi.focus();
  }
  function draw(keepFocus) {
    const a = filtered(), page = a.slice(0, F.shown);
    const onChange = (soft, reloadNeeded) => { if (reloadNeeded) reload(); else draw(true); };
    const bar = F.bulk && F.picked.size ? h("div", { class: "bar" }, h("strong", {}, L(`${F.picked.size} נבחרו`, `${F.picked.size} selected`)), h("button", { class: "sm", onclick: () => { page.forEach(x => F.picked.add(x.url)); draw(true); } }, L("בחירת כל המוצגות", "Select all shown")), h("button", { class: "sm", onclick: () => blockDialog([...F.picked], () => { F.picked.clear(); reload(); }) }, L("לא רלוונטי", "Not relevant")), h("button", { class: "sm danger", onclick: () => deleteDialog([...F.picked], () => { F.picked.clear(); reload(); }) }, L("מחיקה", "Delete"))) : null;
    feed.replaceChildren(a.length ? h("div", { class: "stack" }, h("div", { class: "small muted" }, L(`מציגים ${page.length} מתוך ${a.length}`, `Showing ${page.length} of ${a.length}`)), h("div", { class: "feed" }, page.map(x => listingCard(x, { onChange }))), a.length > page.length ? h("div", { class: "row", style: "justify-content:center" }, h("button", { onclick: () => { F.shown += 30; draw(true); } }, L("הצגת עוד", "Show more"))) : null, bar)
      : ALL.length ? empty(L("אין מודעות שמתאימות לסינון", "No listings match the filters"), L("נסו להרחיב את הסינון.", "Try widening the filters."), h("button", { onclick: () => { Object.assign(F, { q: "", quick: "all", product: FIXED || "", sort: "new", min: "", max: "", loc: "", drops: false }); drawControls(); draw(); } }, L("ניקוי הסינון", "Clear filters")))
        : FIXED ? empty(L(`עדיין אין מודעות רלוונטיות עבור “${cur.name}”`, `No relevant listings for “${cur.name}” yet`), L("כשסריקה תמצא מודעה שמתאימה לחיפוש הזה, היא תופיע כאן.", "When a scan finds a listing that matches this search, it will appear here."), h("a", { class: "btn", href: "#/listings" }, L("כל המודעות", "All listings")))
        : empty(L("עדיין לא נמצאו מודעות", "No listings found yet"), L("כשהמעקב יפעל, מודעות שהדירוג החכם מצא מתאימות יופיעו כאן.", "When monitoring runs, listings the AI rated as a match will appear here."), h("a", { class: "btn primary", href: "#/home" }, L("לדף הבית", "Go to Home"))));
  }
  drawControls(); draw();
}
PAGES.listings = (main, alive) => listingsView(main, alive, null);
PAGES.search = (main, alive, id) => listingsView(main, alive, id || "");

/* ---------------------------------------------------------------- SEARCHES */
const RADII = [1, 2, 5, 10, 20, 40, 60, 80, 100, 250, 500];
const CONDITIONS = { new: L("חדש", "New"), used_like_new: L("משומש כמו חדש", "Used, like new"), used_good: L("משומש במצב טוב", "Used, good condition"), used_fair: L("משומש במצב סביר", "Used, fair condition") };
const DATEL = { 0: L("בכל זמן", "Any time"), 1: L("24 השעות האחרונות", "Last 24 hours"), 7: L("7 הימים האחרונים", "Last 7 days"), 30: L("30 הימים האחרונים", "Last 30 days") };
const DELIV = { all: L("הכול", "All"), local_pick_up: L("איסוף עצמי", "Local pickup"), shipping: L("משלוח", "Shipping") };
const lines = v => (v || []).join("\n");
const SKIP = new Set(["search", "category", "item", "you", "create", "notifications", "inbox", "selling", "buying", "saved", "latest"]);
function parseCity(txt) {
  txt = (txt || "").trim();
  const m = txt.match(/marketplace\/([A-Za-z0-9_.-]{2,60})(?:[/?#]|$)/i);
  const c = m ? m[1] : txt;
  if (!c) return { error: L("הדביקו קישור של Marketplace או כתבו את שם העיר", "Paste a Marketplace link or type the city name") };
  if (!/^[A-Za-z0-9_.-]{2,60}$/.test(c) || SKIP.has(c.toLowerCase())) return { error: L("לא זיהינו עיר. פתחו את Marketplace בפייסבוק, בחרו עיר, והעתיקו את הכתובת מהדפדפן", "We could not recognise a city. Open Marketplace on Facebook, choose a city, and copy the address from the browser") };
  return { city: c };
}

PAGES.searches = async function (main, alive) {
  const [r, l] = await Promise.all([api("GET", "settings"), api("GET", "listings")]);
  if (!alive()) return;
  if (!r.ok) { main.replaceChildren(notice("bad", L("לא הצלחנו לטעון", "Could not load"), errText(r.data))); return; }
  const S = r.data.settings, LIM = r.data.limits, fb = S.facebook;
  const counts = {}; (l.ok ? l.data.listings : []).forEach(x => { (x.product_ids || [x.product_id]).forEach(id => { counts[id] = (counts[id] || 0) + 1; }); });
  const cityLabel = fb.search_city || null;
  const locCard = h("section", { class: "card row" }, h("div", { class: "grow" }, h("h3", {}, L("איפה מחפשים", "Where to search")),
    cityLabel ? h("p", {}, h("strong", { dir: "ltr" }, cityLabel), L(` · עד ${fb.radius} ק״מ`, ` · up to ${fb.radius} km`)) : h("p", { class: "muted" }, L("עוד לא נבחר מיקום. בלי מיקום אי אפשר להתחיל לחפש.", "No location chosen yet. Without a location, searching cannot start."))),
    h("button", { class: cityLabel ? "" : "primary", onclick: () => locationDialog() }, cityLabel ? L("שינוי מיקום", "Change location") : L("בחירת מיקום", "Choose location")));
  const list = h("div", { class: "stack-s" });
  S.products.forEach(p => {
    const problems = []; if (!cityLabel && !p.search_city) problems.push(L("חסר מיקום", "Location missing"));
    const price = p.min_price != null || p.max_price != null ? L(`₪${p.min_price ?? 0}–${p.max_price != null ? "₪" + p.max_price : "ללא תקרה"}`, `₪${p.min_price ?? 0}–${p.max_price != null ? "₪" + p.max_price : "no limit"}`) : L("כל מחיר", "Any price");
    const where = p.search_city ? p.search_city : cityLabel;
    list.append(h("div", { class: "card search" + (p.enabled ? "" : " off") },
      h("div", {}, h("div", { class: "name" }, h("a", { class: "search-link", href: "#/search/" + encodeURIComponent(p.id), title: L("הצגת המודעות של החיפוש הזה", "Show the listings of this search") }, p.name)), h("div", { class: "sum" }, [where ? L(`ב${where}`, `in ${where}`) : null, price, p.antikeywords.length ? L(`בלי: ${p.antikeywords.slice(0, 3).join(", ")}`, `without: ${p.antikeywords.slice(0, 3).join(", ")}`) : null].filter(Boolean).join(" · ")),
        h("div", { class: "row", style: "margin-top:6px" }, problems.length ? h("span", { class: "chip bad" }, problems[0]) : h("span", { class: "chip " + (p.enabled ? "ok" : "") }, p.enabled ? L("פעיל", "Active") : L("מושהה", "Paused")), h("a", { class: "chip search-count", href: "#/search/" + encodeURIComponent(p.id) }, counts[p.id] ? L(`${counts[p.id]} מודעות נמצאו · הצגה`, `${counts[p.id]} listings found · view`) : L("עוד אין מודעות", "No listings yet")))),
      h("span", {}),
      h("div", { class: "acts" },
        switchEl("sw_" + p.id, p.enabled ? L("פעיל", "Active") : L("מושהה", "Paused"), p.enabled, async e => { const x = await api("POST", `products/${p.id}/toggle`, { enabled: e.target.checked }); if (x.ok) { toast(e.target.checked ? L("החיפוש הופעל", "Search enabled") : L("החיפוש הושהה", "Search paused")); PAGES.searches(main, alive); } else { toast(errText(x.data), { err: true }); e.target.checked = !e.target.checked; } }),
        h("button", { class: "sm", onclick: () => productDialog(p) }, L("עריכה", "Edit")),
        h("button", { class: "sm quiet", onclick: async e => { const x = await busy(e.currentTarget, L("משכפל…", "Duplicating…"), () => api("POST", `products/${p.id}/duplicate`, {})); if (x.ok) { toast(L("שוכפל. העותק מושהה עד שתפעילו אותו", "Duplicated. The copy is paused until you enable it")); PAGES.searches(main, alive); } else toast(errText(x.data), { err: true }); } }, L("שכפול", "Duplicate")),
        h("button", { class: "sm quiet", onclick: async () => { if (await confirmDialog(L("מחיקת חיפוש", "Delete search"), L(`למחוק את “${p.name}”? מודעות שכבר נמצאו נשמרות.`, `Delete “${p.name}”? Listings already found are kept.`), L("מחיקה", "Delete"), true)) { const x = await api("DELETE", `products/${p.id}`); if (x.ok) { toast(L("החיפוש נמחק", "Search deleted")); PAGES.searches(main, alive); } else toast(errText(x.data), { err: true }); } } }, L("מחיקה", "Delete")))));
  });
  main.replaceChildren(h("div", { class: "stack" }, head(L("החיפושים שלי", "My searches"), h("button", { class: "primary", onclick: () => productDialog(null) }, icon("plus"), L("חיפוש חדש", "New search"))), locCard,
    S.products.length ? list : empty(L("עוד אין חיפושים", "No searches yet"), L("חיפוש הוא מוצר שרוצים למצוא, למשל “אייפון 13”, עם טווח מחירים.", "A search is something you want to find, for example “iPhone 13”, with a price range."), h("button", { class: "primary", onclick: () => productDialog(null) }, L("הוספת החיפוש הראשון", "Add your first search")))));

  function locationDialog() {
    const inp = h("input", { id: "city", value: cityLabel || "", dir: "ltr", placeholder: "https://www.facebook.com/marketplace/haifa/", autocomplete: "off" });
    const rad = h("select", { id: "rad" }, [...new Set([...RADII, fb.radius])].sort((a, b) => a - b).map(v => h("option", { value: v, selected: v === fb.radius }, L(`${v} ק״מ`, `${v} km`))));
    const f1 = field(L("קישור של Marketplace בעיר שלכם, או שם העיר", "A Marketplace link for your city, or the city name"), inp, L("פתחו את Marketplace בפייסבוק, בחרו עיר, והעתיקו את הכתובת מהדפדפן. אנחנו נזהה את העיר מתוכה.", "Open Marketplace on Facebook, choose a city, and copy the address from the browser. We will recognise the city from it."), null);
    const prev = h("div", { class: "small", style: "margin-top:-8px;margin-bottom:12px" });
    const upd = () => { const c = parseCity(inp.value); prev.replaceChildren(c.city ? h("span", { class: "chip ok" }, L(`זוהתה עיר: ${c.city}`, `City recognised: ${c.city}`)) : null); setError(f1, inp.value.trim() && c.error ? c.error : null); return c; };
    inp.addEventListener("input", upd); upd();
    openDialog(L("איפה מחפשים?", "Where to search?"), h("div", {}, f1, prev, field(L("רדיוס חיפוש", "Search radius"), rad, L("המרחק מהעיר שבה מחפשים.", "Distance from the city."))), [
      h("button", { class: "primary", onclick: async e => { const c = upd(); if (c.error) { setError(f1, c.error); return; } await busy(e.currentTarget, L("שומר ובודק…", "Saving and checking…"), async () => { const x = await api("PUT", "settings/facebook", { search_city: c.city, radius: +rad.value }); if (x.ok) { closeDialog(); toast(L("המיקום נשמר", "Location saved")); PAGES.searches(main, alive); } else setError(f1, errText(x.data)); }); } }, L("שמירה", "Save")), h("button", { class: "quiet", onclick: closeDialog }, L("ביטול", "Cancel"))]);
  }

  function productDialog(p) {
    const isNew = !p;
    p = p || { enabled: true, search_phrases: [], keywords: [], antikeywords: [], description: "", min_price: null, max_price: null, great_deal_price: null, search_city: "", radius: null, rating: null, search_interval_min: null, condition: [], date_listed: 0, delivery_method: "all", seller_locations: [], exclude_sellers: [], name: "" };
    const v = x => (x == null ? "" : x);
    const name = h("input", { id: "pn", value: p.name, maxlength: 40, placeholder: L("למשל: אייפון 13", "e.g. iPhone 13"), autocomplete: "off" });
    const minp = h("input", { id: "pmin", type: "number", min: 0, inputmode: "numeric", value: v(p.min_price), placeholder: L("ללא", "None") }), maxp = h("input", { id: "pmax", type: "number", min: 0, inputmode: "numeric", value: v(p.max_price), placeholder: L("ללא", "None") });
    const desc = h("textarea", { id: "pd", rows: 3, maxlength: 1500, placeholder: L("למשל: במצב תקין, בלי סדקים. לא מעניינים אותי חלקים או מכשיר שבור.", "e.g. In working condition, no cracks. I am not interested in parts or a broken device.") }, p.description);
    const phr = h("textarea", { id: "pp", rows: 3 }, lines((p.search_phrases || []).filter(x => x !== p.name))), kw = h("textarea", { id: "pk", rows: 2 }, lines(p.keywords)), akw = h("textarea", { id: "pa", rows: 2 }, lines(p.antikeywords));
    const great = h("input", { id: "pg", type: "number", min: 0, value: v(p.great_deal_price) });
    const pcity = h("input", { id: "pc", dir: "ltr", value: p.search_city, placeholder: L("ריק = המיקום הכללי", "Empty = the general location") });
    const dist = h("select", { id: "pr" }, h("option", { value: "" }, L(`כמו ההגדרה הכללית (${fb.radius} ק״מ)`, `Same as the general setting (${fb.radius} km)`)), [...new Set([...RADII, p.radius].filter(Boolean))].sort((a, b) => a - b).map(x => h("option", { value: x, selected: p.radius === x }, x === 500 ? L("ללא הגבלה מעשית (500 ק״מ)", "Practically unlimited (500 km)") : L(`עד ${x} ק״מ`, `Up to ${x} km`))));
    const cur = h("select", { id: "pcur" }, h("option", { value: "" }, L("ללא הגבלה (כל מטבע)", "Any currency (no restriction)")), LIM.currencies.map(c => h("option", { value: c, selected: p.currency === c }, `${SYM[c] ? SYM[c] + " " : ""}${c}`)));
    const prate = h("select", { id: "prt" }, h("option", { value: "" }, L("כמו ההגדרות הכלליות", "Same as general settings")), [3, 4, 5].map(n => h("option", { value: n, selected: p.rating === n }, L(`${n} ומעלה`, `${n} and up`))));
    const piv = h("input", { id: "piv", type: "number", min: LIM.min_interval, max: 1440, value: v(p.search_interval_min), placeholder: L("כמו הכללי", "Same as general") });
    const dl = h("select", { id: "pdl" }, LIM.date_listed.map(x => h("option", { value: x, selected: p.date_listed === x }, DATEL[x]))), dm = h("select", { id: "pdm" }, LIM.delivery.map(x => h("option", { value: x, selected: p.delivery_method === x }, DELIV[x])));

    const conds = LIM.conditions.map(c => h("label", { class: "opt", for: "c_" + c }, h("input", { type: "checkbox", id: "c_" + c, value: c, checked: p.condition.includes(c) }), h("div", {}, h("strong", {}, CONDITIONS[c]))));
    const fName = field(L("מה מחפשים?", "What are you looking for?"), name, L("שם החיפוש. משמש גם כמילת החיפוש בפייסבוק.", "The name of the search. It is also used as the search word on Facebook."), null);
    const fPrice = h("div", { class: "field" }, h("label", {}, L("טווח מחירים", "Price range")), h("div", { class: "two" }, h("div", {}, minp, h("div", { class: "hint" }, L("מחיר מינימום", "Minimum price"))), h("div", {}, maxp, h("div", { class: "hint" }, L("מחיר מקסימום", "Maximum price")))),
      h("div", { style: "margin-top:12px" }, h("label", { for: "pcur" }, L("מטבע", "Currency")), cur, h("div", { class: "hint" }, L(`הסכומים נשלחים לפייסבוק כפי שהם, בלי המרת מטבע. פייסבוק מפרשת אותם לפי מטבע האזור (${fb.currency}). אם תבחרו מטבע, מודעות שמחירן במטבע אחר יוסתרו.`, `Amounts are sent to Facebook as typed, with no currency conversion. Facebook reads them in the region currency (${fb.currency}). If you pick a currency, listings priced in another currency are hidden.`))));
    const fDist = field(L("מרחק מרבי מהעיר", "Maximum distance from the city"), dist, L("פייסבוק מחפשת סביב מרכז העיר שנבחרה (לא סביב הכתובת שלך) ובערכים הסטנדרטיים שלה בלבד. אי אפשר לאכוף מרחק מדויק יותר, ו״ללא הגבלה״ פירושו 500 ק״מ, המקסימום של פייסבוק.", "Facebook searches around the centre of the chosen city (not your address) and only in its standard steps. A more exact distance cannot be enforced, and “no limit” means 500 km, Facebook's maximum."), null);
    const summary = h("div", { class: "notice info", "aria-live": "polite" });
    const eff = () => ({ name: name.value.trim(), phrases: phr.value.split("\n").map(x => x.trim()).filter(Boolean) });
    function validate(show) {
      let ok = true;
      const nm = name.value.trim(); setError(fName, show && !nm ? L("כתבו מה מחפשים", "Enter what you are looking for") : null); if (!nm) ok = false;
      const a = minp.value === "" ? null : +minp.value, b = maxp.value === "" ? null : +maxp.value;
      let pe = null; if ((a != null && (isNaN(a) || a < 0)) || (b != null && (isNaN(b) || b < 0))) pe = L("מחיר חייב להיות מספר חיובי", "Price must be a positive number"); else if (a != null && b != null && a > b) pe = L("המחיר המקסימלי קטן מהמינימלי", "The maximum price is lower than the minimum");
      setError(fPrice, pe); if (pe) ok = false;
      return ok;
    }
    function drawSummary() {
      const e = eff(), a = minp.value, b = maxp.value, city = pcity.value.trim() || fb.search_city, cs = SYM[cur.value || fb.currency] || (cur.value || fb.currency) + " ", km = dist.value || fb.radius;
      const price = (a || b ? L(` במחיר ${a ? "מ-" + cs + a : ""}${a && b ? " " : ""}${b ? "עד " + cs + b : ""}`, ` priced ${a ? "from " + cs + a : ""}${a && b ? " " : ""}${b ? "up to " + cs + b : ""}`) : "") + (city ? L(`, עד ${km} ק״מ`, `, within ${km} km`) : "");
      const extra = e.phrases.filter(x => x !== e.name);
      // renaming keeps the extra phrases of the old search: say so, they stay hidden under "Advanced options" otherwise
      const stale = p.name && e.name && e.name !== p.name && extra.length ? h("div", { class: "notice warn small", id: "rename-phrases" }, L(`שיניתם את שם החיפוש, אבל הביטויים הנוספים הקודמים עדיין יחופשו: ${extra.map(x => "“" + x + "”").join(", ")}. אם אינם קשורים, מחקו אותם באפשרויות המתקדמות.`, `You renamed the search, but the previous extra phrases will still be searched: ${extra.map(x => "“" + x + "”").join(", ")}. If they are unrelated, remove them under Advanced options.`)) : null;
      summary.replaceChildren(h("div", {}, h("strong", {}, L("סיכום: ", "Summary: ")), e.name ? L(`נחפש “${e.name}”`, `We will search for “${e.name}”`) : L("נחפש…", "We will search for…"), extra.length ? L(` ועוד: ${extra.map(x => "“" + x + "”").join(", ")} (אפשרויות מתקדמות)`, ` and also: ${extra.map(x => "“" + x + "”").join(", ")} (advanced options)`) : null, city ? L(` ב${city}`, ` in ${city}`) : L(" (עוד לא נבחר מיקום)", " (no location chosen yet)"), price, L(". כל מודעה מקבלת דירוג חכם 1–5, ומודעות מתאימות יגיעו אליכם בהתראה.", ". Every listing gets an AI rating of 1–5, and matching listings will reach you as an alert.")), stale);
    }
    ["input"].forEach(ev => [name, minp, maxp, phr, pcity, dist, cur].forEach(i => i.addEventListener(ev === "input" && (i === dist || i === cur) ? "change" : ev, () => { drawSummary(); if (i === name || i === minp || i === maxp) validate(i === name ? name.value.trim() === "" ? false : true : true); })));
    drawSummary();
    const errbox = h("div", {});
    const body = h("div", {}, errbox, fName, fPrice, fDist, field(L("מה נחשב התאמה טובה? (לא חובה)", "What counts as a good match? (optional)"), desc, L("ככל שתתארו טוב יותר, הדירוג החכם ידייק יותר.", "The more precisely you describe it, the better the AI rating gets.")),
      h("details", { class: "more" }, h("summary", {}, L("אפשרויות מתקדמות", "Advanced options")), h("div", { class: "stack", style: "margin-top:12px" },
        field(L("ביטויי חיפוש נוספים", "Extra search phrases"), phr, L("שורה לכל ביטוי, בנוסף לשם החיפוש שלמעלה (שהוא תמיד ביטוי החיפוש הראשי).", "One per line, in addition to the search name above (which is always the main search phrase).")),
        h("div", { class: "two" }, field(L("מילים שחייבות להופיע", "Words that must appear"), kw, L("שורה לכל אפשרות (או).", "One option per line (or).")), field(L("מילים שפוסלות מודעה", "Words that rule a listing out"), akw, L("למשל: שבור, חלקים.", "e.g. broken, parts."))),
        field(L("התראה מיידית כשהמחיר עד (₪)", "Instant alert when the price is up to (₪)"), great, L("מודעה במחיר הזה או נמוך ממנו נשלחת מיד, גם אם הדירוג שלה לא הגבוה ביותר.", "A listing at or below this price is sent immediately, even if its rating is not the highest.")),
        h("div", { class: "field" }, h("label", {}, L("מצב הפריט", "Item condition")), h("div", { class: "stack-s" }, conds)),
        h("div", { class: "two" }, field(L("פורסם", "Posted"), dl), field(L("מסירה", "Delivery"), dm)),
        field(L("עיר שונה לחיפוש הזה", "A different city for this search"), pcity, L("ריק = המיקום הכללי.", "Empty = the general location.")),
        h("div", { class: "two" }, field(L("דירוג מינימלי להצגה", "Minimum rating to show"), prate), field(L("בדיקה כל (דקות)", "Check every (minutes)"), piv, L(`לפחות ${LIM.min_interval}.`, `At least ${LIM.min_interval}.`))),
        null)), summary);
    const dlg = openDialog(isNew ? L("חיפוש חדש", "New search") : L("עריכת חיפוש", "Edit search"), body, [
      h("button", { class: "primary", onclick: async e => {
        if (!validate(true)) { toast(L("יש שדות שצריך לתקן", "Some fields need fixing"), { err: true }); return; }
        const num = x => (x.value === "" ? null : +x.value), em = eff();
        const payload = { name: em.name, enabled: p.enabled, search_phrases: [em.name, ...em.phrases.filter(x => x !== em.name)], keywords: kw.value, antikeywords: akw.value, description: desc.value, min_price: num(minp), max_price: num(maxp),
          great_deal_price: num(great), search_city: pcity.value.trim(), radius: dist.value === "" ? null : +dist.value, rating: prate.value === "" ? null : +prate.value, search_interval_min: num(piv),
          condition: [...body.querySelectorAll("input[id^=c_]:checked")].map(i => i.value), date_listed: +dl.value, delivery_method: dm.value, currency: cur.value };
        await busy(e.currentTarget, L("שומר ובודק…", "Saving and checking…"), async () => {
          const x = isNew ? await api("POST", "products", payload) : await api("PUT", `products/${p.id}`, payload);
          if (x.ok) { closeDialog(); toast(x.data.warning ? L("נשמר. ", "Saved. ") + x.data.warning : L("החיפוש נשמר. הסריקה הבאה תשתמש בו.", "Search saved. The next scan will use it.")); PAGES.searches(main, alive); return; }
          const map = { name: fName, min_price: fPrice, max_price: fPrice };
          const rest = [];
          for (const er of x.data.errors || []) { const k = er.field.replace(/^products\.\d+\./, ""); if (map[k]) setError(map[k], er.message); else rest.push(er.message); }
          errbox.replaceChildren(rest.length ? notice("bad", L("צריך לתקן:", "Please fix:"), rest.join(" · ")) : null);
          toast(L("יש שדות שצריך לתקן", "Some fields need fixing"), { err: true });
        });
      } }, isNew ? L("הוספת חיפוש", "Add search") : L("שמירה", "Save")), h("button", { class: "quiet", onclick: closeDialog }, L("ביטול", "Cancel"))]);
    setTimeout(() => name.focus(), 50);
  }
};

/* ---------------------------------------------------------------- FACEBOOK */
const LANGS = { he: L("עברית", "Hebrew"), en: L("אנגלית", "English") };
PAGES.facebook = async function (main, alive) {
  let flowOpen = false;
  async function draw() {
    const r = await api("GET", "facebook");
    if (!alive()) return;
    if (!r.ok) { main.replaceChildren(notice("bad", L("לא הצלחנו לטעון", "Could not load"), errText(r.data))); return; }
    const f = r.data, c = f.connection, sess = f.session || {};
    const helperOpen = !!f.helper_open;
    const tone = c.severity === "ok" ? "ok" : c.severity === "info" ? "info" : c.severity;
    const needs = ["connect", "reconnect", "open_browser", "save_login"].includes(c.action) || helperOpen;
    const heroBtns = [];
    if (!helperOpen && needs) heroBtns.push(h("button", { class: "primary big", disabled: f.scraper_running, onclick: e => startFlow(e.currentTarget) }, c.action === "connect" ? L("התחברות לפייסבוק", "Connect to Facebook") : L("התחברות מחדש", "Reconnect")));
    if (!helperOpen && f.scraper_running && needs) heroBtns.push(h("div", { class: "small muted" }, L("המעקב פעיל, אז קודם נעצור אותו. זה יקרה אוטומטית כשתתחילו להתחבר.", "Monitoring is on, so we will stop it first. This happens automatically when you start connecting.")));
    if (!helperOpen && !needs && sess.exists) heroBtns.push(h("button", { onclick: e => checkNow(e.currentTarget) }, L("בדיקת חיבור עכשיו", "Check connection now")));
    const hero = h("section", { class: "card hero " + tone }, h("h2", {}, h("span", { class: "tone-dot" }), c.title), h("p", { class: "muted" }, c.detail), heroBtns.length ? h("div", { class: "row" }, heroBtns) : null);
    const flow = helperOpen || flowOpen ? flowCard(f) : null;
    const lastOk = f.last_ok ? `${fmtTime(f.last_ok)} (${ago(f.last_ok)})` : L("עוד לא התחברנו בהצלחה", "Not connected successfully yet");
    const lang = f.detected ? (f.detected.startsWith("he") ? L("עברית", "Hebrew") : f.detected.startsWith("en") ? L("אנגלית", "English") : f.detected) : L("עוד לא זוהתה", "Not detected yet");
    const facts = h("section", { class: "card" }, h("h3", {}, L("פרטים", "Details")), h("dl", { class: "kv" }, h("dt", {}, L("התחברות תקינה אחרונה", "Last healthy connection")), h("dd", {}, lastOk), h("dt", {}, L("שפת חשבון הפייסבוק", "Facebook account language")), h("dd", {}, lang), h("dt", {}, L("הסורק", "Scraper")), h("dd", {}, f.scraper_running ? L("פועל", "Running") : L("כבוי", "Off"))),
      h("p", { class: "small muted", style: "margin-top:12px" }, L("הסיסמה שלכם לא נשמרת בשום מקום. נשמר רק קובץ התחברות (cookies) מוגן, שנגיש לסורק בלבד.", "Your password is not stored anywhere. Only a protected login file (cookies) is kept, accessible to the scraper only.")));
    const trouble = h("details", { class: "card" }, h("summary", { style: "font-weight:700;cursor:pointer" }, L("מה עושים אם משהו לא עובד?", "What if something does not work?")), h("div", { class: "stack-s", style: "margin-top:12px" },
      h("p", {}, h("strong", {}, L("ההתחברות נכשלה: ", "Login failed: ")), L("ודאו שאתם מתחברים בחלון הדפדפן עצמו (לא בלשונית אחרת), ואז לוחצים “סיימתי להתחבר”.", "Make sure you log in inside the browser window itself (not another tab), then press “I\u2019ve finished logging in”.")),
      h("p", {}, h("strong", {}, L("פייסבוק מבקשת אימות או CAPTCHA: ", "Facebook asks for verification or a CAPTCHA: ")), L("פותרים אותו בחלון הדפדפן, בעצמכם. אנחנו לא עוקפים אימותים. אם זה קורה הרבה, מומלץ להקטין את תדירות החיפוש.", "Solve it yourself in the browser window. We never bypass verification. If this happens often, lower the search frequency.")),
      h("p", {}, h("strong", {}, L("מחוברים אבל אין מודעות: ", "Connected but no listings: ")), L("ודאו שיש מיקום וחיפוש פעיל. שפת החשבון נתמכת בעברית ובאנגלית; בשפה אחרת יש להחליף לאנגלית.", "Make sure a location and an active search exist. The account language is supported in English and Hebrew; for any other language, switch it to English.")),
      h("p", {}, h("strong", {}, L("ההתחברות פגה שוב ושוב: ", "The login keeps expiring: ")), L("קורה אם מתנתקים מפייסבוק מכל המכשירים או מחליפים סיסמה. מומלץ חשבון משני ייעודי.", "This happens if you log out of Facebook on all devices or change the password. A dedicated secondary account is recommended."))));
    const langSel = h("select", { id: "lang" }, [["auto", L("אוטומטי (מומלץ)", "Automatic (recommended)")], ["he", L("עברית", "Hebrew")], ["en", L("אנגלית (ארה״ב)", "English (US)")]].map(([v, l]) => h("option", { value: v, selected: f.language === v }, l)));
    const adv = h("details", { class: "card" }, h("summary", { style: "font-weight:700;cursor:pointer" }, L("אפשרויות מתקדמות", "Advanced options")), h("div", { class: "stack", style: "margin-top:12px" },
      field(L("שפת חשבון הפייסבוק", "Facebook account language"), langSel, L("הסורק קורא את דפי פייסבוק לפי השפה. “אוטומטי” מזהה אותה מהחשבון שלכם.", "The scraper reads Facebook pages according to the language. “Automatic” detects it from your account.")),
      h("button", { class: "sm", onclick: async e => { await busy(e.currentTarget, L("שומר ובודק…", "Saving and checking…"), async () => { const x = await api("PUT", "settings/facebook", { language: langSel.value }); toast(x.ok ? L("שמור", "Saved") : errText(x.data), { err: !x.ok }); }); } }, L("שמירת השפה", "Save language")),
      h("dl", { class: "kv" }, h("dt", {}, L("מצב טכני", "Technical state")), h("dd", { dir: "ltr" }, `${c.facts.state || "-"} · lang=${c.facts.lang || "-"} · scraper=${c.facts.scraper || "-"}`), h("dt", {}, L("קובץ התחברות", "Login file")), h("dd", {}, sess.exists ? L(`קיים (${sess.has_login_cookies ? "עם התחברות" : "בלי התחברות"}, הרשאות ${sess.mode || "?"})`, `exists (${sess.has_login_cookies ? "with a login" : "without a login"}, permissions ${sess.mode || "?"})`) : L("אין", "none"))),
      h("div", { class: "row" }, h("button", { class: "sm", disabled: !sess.exists || f.scraper_running || helperOpen, onclick: e => checkNow(e.currentTarget) }, L("בדיקת חיבור (טוענת עמוד אחד בפייסבוק)", "Check connection (loads one Facebook page)")),
        h("button", { class: "sm danger", disabled: !sess.exists || f.scraper_running || helperOpen, onclick: async () => { if (await confirmDialog(L("ניתוק מפייסבוק", "Disconnect from Facebook"), L("קובץ ההתחברות יימחק. כדי לחפש שוב תצטרכו להתחבר מחדש.", "The login file will be deleted. To search again you will need to log in again."), L("ניתוק ומחיקה", "Disconnect and delete"), true)) { const x = await api("POST", "facebook/logout"); toast(x.ok ? L("נותקתם מפייסבוק", "You are disconnected from Facebook") : errText(x.data), { err: !x.ok }); draw(); } } }, L("ניתוק ומחיקת ההתחברות", "Disconnect and delete the login")))));
    main.replaceChildren(h("div", { class: "stack" }, head(L("חיבור לפייסבוק", "Facebook connection")), hero, flow, facts, trouble, adv));
  }
  async function startFlow(btn) {
    await busy(btn, L("פותח חלון…", "Opening the window…"), async () => {
      const st = await api("GET", "status");
      if (st.ok && st.data.scraper.running) await api("POST", "scraper", { action: "stop" });
      let x = await api("POST", "facebook/open");
      for (let i = 0; i < 6 && !x.ok && x.status === 409; i++) { await new Promise(r => setTimeout(r, 1500)); x = await api("POST", "facebook/open"); }
      if (!x.ok) return toast(errText(x.data), { err: true });
      flowOpen = true; draw();
    });
  }
  function flowCard(f) {
    const ready = f.helper_ready;
    return h("section", { class: "card stack", "aria-labelledby": "flow-h" }, h("h2", { id: "flow-h" }, L("התחברות בשלושה צעדים", "Log in in three steps")),
      h("div", { class: "steps" },
        h("div", { class: "step" + (ready ? " done" : " cur") }, h("div", { class: "ico" }, ready ? icon("check") : "1"), h("div", {}, h("strong", {}, L("נפתח חלון דפדפן", "A browser window opened")), h("div", { class: "small muted" }, ready ? L("החלון מוכן.", "The window is ready.") : L("פותחים את הדפדפן… זה לוקח כמה שניות.", "Opening the browser… this takes a few seconds."))), h("span", {})),
        h("div", { class: "step" + (ready ? " cur" : "") }, h("div", { class: "ico" }, "2"), h("div", {}, h("strong", {}, L("מתחברים לפייסבוק בחלון", "Log in to Facebook in the window")), h("div", { class: "small muted" }, L("הקלידו שם את הפרטים שלכם, ופתרו אימות אם מתבקשים. החלון נפתח בלשונית חדשה.", "Type your details there, and solve verification if asked. The window opens in a new tab."))),
          ready ? h("a", { class: "btn primary sm", href: "/browser/", target: "_blank", rel: "noopener" }, icon("ext"), L("פתיחת החלון", "Open the window")) : h("span", {})),
        h("div", { class: "step" }, h("div", { class: "ico" }, "3"), h("div", {}, h("strong", {}, L("חוזרים לכאן ולוחצים “סיימתי”", "Come back here and press “I\u2019ve finished”")), h("div", { class: "small muted" }, L("נשמור את ההתחברות ונבדוק שהיא עובדת.", "We will save the login and check that it works."))),
          h("button", { class: "go primary sm", disabled: !ready, onclick: e => finish(e.currentTarget) }, L("סיימתי להתחבר", "I\u2019ve finished logging in")))),
      h("div", { class: "row" }, h("button", { class: "quiet", onclick: async () => { await api("POST", "facebook/close"); flowOpen = false; toast(L("החלון נסגר בלי לשמור", "The window was closed without saving")); draw(); } }, L("ביטול", "Cancel"))),
      h("p", { class: "small muted" }, L("החלון נסגר אוטומטית אחרי 20 דקות.", "The window closes automatically after 20 minutes.")));
  }
  async function finish(btn) {
    await busy(btn, L("שומר ובודק…", "Saving and checking…"), async () => {
      const x = await api("POST", "facebook/save");
      if (!x.ok) return toast(errText(x.data), { err: true });
      if (x.data.result && x.data.result.state === "saved") { flowOpen = false; toast(L("מחוברים לפייסבוק. ההתחברות נשמרה.", "Connected to Facebook. The login was saved.")); } else toast(L("לא זוהתה התחברות. התחברו בחלון הדפדפן ונסו שוב.", "No login was detected. Log in in the browser window and try again."), { err: true });
      draw();
    });
  }
  async function checkNow(btn) {
    if (!await confirmDialog(L("בדיקת חיבור", "Check connection"), L("הבדיקה טוענת עמוד אחד בפייסבוק עם ההתחברות השמורה, כדי לראות שהיא עדיין תקפה.", "The check loads one Facebook page with the saved login, to see that it is still valid."), L("בדיקה", "Check"))) return;
    await busy(btn, L("בודק…", "Checking…"), async () => { const x = await api("POST", "facebook/check"); toast(x.ok ? (x.data.state === "logged_in" ? L("החיבור תקין", "The connection is healthy") : L("החיבור לא תקין. ראו הסבר בדף", "The connection is not healthy. See the explanation on the page")) : errText(x.data), { err: !(x.ok && x.data.state === "logged_in") }); draw(); });
  }
  await draw();
  if (alive()) TIMER = setInterval(() => { if (!document.hidden && !$("#dlg").open) draw(); }, 2500);
};

/* ---------------------------------------------------------------- NOTIFICATIONS */
PAGES.notifications = async function (main, alive) {
  const [r, st] = await Promise.all([api("GET", "settings"), api("GET", "status")]);
  if (!alive()) return;
  if (!r.ok || !st.ok) { main.replaceChildren(notice("bad", L("לא הצלחנו לטעון", "Could not load"), errText(r.data))); return; }
  const n = r.data.settings.notifications, tg = st.data.telegram, ob = tg.outbox, tested = r.data.settings.ui.telegram_tested;
  const en = h("input", { type: "checkbox", id: "nen", checked: n.enabled });
  const chat = h("input", { id: "chat", value: n.chat_id, dir: "ltr", inputmode: "numeric", placeholder: L("למשל 123456789", "e.g. 123456789"), autocomplete: "off" });
  const fChat = field(L("זיהוי הצ'אט בטלגרם", "Telegram chat"), chat, L("המספר של הצ'אט שאליו נשלחות ההתראות.", "The number of the chat that alerts are sent to."));
  const detectOut = h("div", { class: "small", style: "margin-top:8px" });
  const radio = (id, val, strong, desc, checked) => h("label", { class: "opt", for: id }, h("input", { type: "radio", name: "imm", id, value: val, checked }), h("div", {}, h("strong", {}, strong), h("span", {}, desc)));
  const rc = h("input", { type: "checkbox", id: "rce", checked: n.recap_enabled }), rt = h("input", { type: "time", id: "rtime", value: n.recap_time, dir: "ltr" }), rwe = h("input", { type: "checkbox", id: "rwe", checked: n.recap_when_empty });
  const qe = h("input", { type: "checkbox", id: "qen", checked: n.quiet_enabled }), qf = h("input", { type: "time", id: "qf", value: n.quiet_from, dir: "ltr" }), qt = h("input", { type: "time", id: "qt", value: n.quiet_to, dir: "ltr" });
  const errs = h("div", {});
  async function save(btn) {
    const imm = main.querySelector("input[name=imm]:checked").value;
    await busy(btn, L("שומר…", "Saving…"), async () => {
      const x = await api("PUT", "settings/notifications", { enabled: en.checked, chat_id: chat.value.trim(), immediate_min_score: +imm, recap_enabled: rc.checked, recap_time: rt.value, recap_when_empty: rwe.checked, quiet_enabled: qe.checked, quiet_from: qf.value, quiet_to: qt.value });
      if (x.ok) { toast(L("ההגדרות נשמרו", "Settings saved")); errs.replaceChildren(); } else { errs.replaceChildren(notice("bad", L("צריך לתקן:", "Please fix:"), errText(x.data))); toast(L("לא נשמר", "Not saved"), { err: true }); }
    });
  }
  const statusLine = !tg.token_configured ? notice("bad", L("חסר טוקן לבוט", "Bot token missing"), L("הטוקן של הבוט מוגדר בהגדרות השרת (Portainer) ולא כאן. בלעדיו אי אפשר לשלוח התראות.", "The bot token is set in the server settings (Portainer), not here. Without it alerts cannot be sent."))
    : ob.last_error && n.enabled ? notice("warn", L("יש בעיה בשליחה", "There is a delivery problem"), L("ההודעות ממתינות ויישלחו כשהחיבור יחזור: ", "Messages are waiting and will be sent when the connection returns: ") + ob.last_error) : tested || n.enabled ? notice("ok", "", L("טלגרם מחובר", "Telegram is connected") + (n.enabled ? L(" וההתראות פעילות.", " and alerts are on.") : L(", אבל ההתראות כבויות.", ", but alerts are off."))) : notice("info", "", L("שלחו הודעת בדיקה כדי לוודא שהכול מחובר.", "Send a test message to make sure everything is connected."));
  main.replaceChildren(h("div", { class: "stack" }, head(L("התראות", "Notifications")), statusLine,
    h("section", { class: "card stack" }, h("h2", {}, L("טלגרם", "Telegram")),
      switchEl2(en, L("לשלוח לי התראות בטלגרם", "Send me alerts on Telegram")),
      fChat, h("div", { class: "row" }, h("button", { onclick: async e => { await busy(e.currentTarget, L("מחפש…", "Searching…"), async () => {
          const x = await api("POST", "notifications/detect");
          if (!x.ok) { detectOut.replaceChildren(h("span", { class: "err" }, errText(x.data))); return; }
          if (!x.data.chats.length) { detectOut.replaceChildren(h("span", {}, x.data.hint)); return; }
          if (x.data.chats.length === 1) { chat.value = x.data.chats[0].id; detectOut.replaceChildren(h("span", { class: "chip ok" }, L(`זוהה: ${x.data.chats[0].name || x.data.chats[0].id}`, `Detected: ${x.data.chats[0].name || x.data.chats[0].id}`))); return; }
          detectOut.replaceChildren(h("div", { class: "stack-s" }, h("div", {}, L("נמצאו כמה צ'אטים, בחרו:", "Several chats were found, choose one:")), x.data.chats.map(c => h("button", { class: "sm", onclick: () => { chat.value = c.id; detectOut.replaceChildren(h("span", { class: "chip ok" }, L(`נבחר: ${c.name || c.id}`, `Selected: ${c.name || c.id}`))); } }, L(`${c.name || "ללא שם"} (${c.id})`, `${c.name || "No name"} (${c.id})`)))));
        }); } }, L("זיהוי אוטומטי", "Detect automatically")),
        h("button", { onclick: async e => { await busy(e.currentTarget, L("שולח…", "Sending…"), async () => { const x = await api("POST", "notifications/test"); if (x.ok) toast(L("נשלחה הודעת בדיקה. בדקו בטלגרם", "A test message was sent. Check Telegram")); else toast(errText(x.data), { err: true }); }); } }, L("שליחת הודעת בדיקה", "Send a test message"))),
      detectOut, h("p", { class: "small muted" }, L("איך מוצאים את המספר? פתחו את הבוט בטלגרם, שלחו לו /start, ולחצו “זיהוי אוטומטי”.", "How do I find the number? Open the bot in Telegram, send it /start, and press “Detect automatically”."))),
    h("section", { class: "card stack" }, h("h2", {}, L("מתי להתריע מיד", "When to alert immediately")),
      radio("imm5", 5, L("רק על מציאות מצוינות", "Only for excellent finds"), L("דירוג חכם 5, או מחיר מתחת למחיר ה“מציאה” שהגדרתם בחיפוש. השאר מגיע בסיכום היומי.", "AI rating 5, or a price below the “great deal” price you set in the search. The rest arrives in the daily summary."), n.immediate_min_score === 5),
      radio("imm4", 4, L("גם על התאמות טובות", "Also for good matches"), L("דירוג 4 ומעלה. יותר התראות.", "Rating 4 and up. More alerts."), n.immediate_min_score === 4)),
    h("section", { class: "card stack" }, h("h2", {}, L("סיכום יומי", "Daily summary")), switchEl2(rc, L("לשלוח סיכום יומי של כל מה שנמצא", "Send a daily summary of everything found")), field(L("באיזו שעה?", "At what time?"), rt, L("לפי שעון ישראל.", "Israel time.")), switchEl2(rwe, L("לשלוח גם כשלא נמצא כלום", "Send even when nothing was found")),
      h("div", { class: "row" }, h("button", { onclick: async e => { await busy(e.currentTarget, L("שולח…", "Sending…"), async () => { const x = await api("POST", "summary/send"); if (x.ok && x.data.nothing) toast(x.data.message); else if (x.ok) toast(L(`הסיכום נשלח לטלגרם (${x.data.listings} מודעות היום)`, `Summary sent to Telegram (${x.data.listings} listings today)`)); else toast(errText(x.data), { err: true }); }); } }, L("שליחת סיכום יומי עכשיו", "Send today\u2019s summary now")), h("span", { class: "small muted" }, L("שולח את אותו סיכום כמו המתוזמן, מיד.", "Sends the same summary as the scheduled one, right now.")))),
    h("section", { class: "card stack" }, h("h2", {}, L("שעות שקט", "Quiet hours")), switchEl2(qe, L("לא להפריע לי בשעות מסוימות", "Do not disturb me during certain hours")), h("div", { class: "two" }, field(L("מ-", "From"), qf), field(L("עד", "To"), qt)), h("p", { class: "small muted" }, L("בשעות האלה התראות על מודעות מחכות ונשלחות בסיומן. הסיכום היומי והודעות מערכת חשובות נשלחים בכל שעה.", "During these hours listing alerts wait and are sent when they end. The daily summary and important system messages are sent at any time."))),
    errs, h("div", { class: "row" }, h("button", { class: "primary big", onclick: e => save(e.currentTarget) }, L("שמירה", "Save")))));
  function switchEl2(input, label) { return h("label", { class: "switch", for: input.id }, input, h("span", { class: "track" }), h("span", {}, label)); }
};

/* ---------------------------------------------------------------- SETTINGS */
PAGES.settings = async function (main, alive) {
  const [r, sy] = await Promise.all([api("GET", "settings"), api("GET", "system")]);
  if (!alive()) return;
  if (!r.ok || !sy.ok) { main.replaceChildren(notice("bad", L("לא הצלחנו לטעון", "Could not load"), errText(r.data))); return; }
  const S = r.data.settings, fb = S.facebook, LIM = r.data.limits, sys = sy.data;
  const iv = h("input", { id: "siv", type: "number", min: LIM.min_interval, max: 1440, value: fb.search_interval_min }), miv = h("input", { id: "smiv", type: "number", min: LIM.min_interval, max: 2880, value: fb.max_search_interval_min });
  const rate = h("select", { id: "srt" }, [3, 4, 5].map(n => h("option", { value: n, selected: n === fb.rating }, L(`${n} ומעלה`, `${n} and up`))));
  const cur = h("select", { id: "scur" }, ["ILS", "USD", "EUR", "GBP"].map(c => h("option", { value: c, selected: c === fb.currency }, c)));

  const fIv = field(L("בדיקה כל (דקות)", "Check every (minutes)"), iv, L(`לפחות ${LIM.min_interval}. בדיקה תכופה מגדילה את הסיכוי שפייסבוק תגביל את החשבון.`, `At least ${LIM.min_interval}. Checking too often raises the chance that Facebook restricts the account.`)), fMiv = field(L("עד (דקות)", "Up to (minutes)"), miv, L("הסורק מגריל זמן בין שני הערכים.", "The scraper picks a random time between the two values."));
  const errbox = h("div", {});
  const svc = sys.services.map(x => h("div", { class: "svc" }, h("span", { class: "chip " + (x.ok ? "ok" : "bad") }, x.ok ? L("תקין", "OK") : L("בעיה", "Problem")), h("strong", {}, x.name), h("span", { class: "muted small" }, x.detail)));
  main.replaceChildren(h("div", { class: "stack" }, head(L("הגדרות", "Settings")),
    h("section", { class: "card stack" }, h("h2", {}, L("שפה", "Language")),
      field(L("שפת הממשק", "Interface language"), h("select", { id: "uilang", onchange: async e => { const x = await api("POST", "language", { language: e.target.value }); if (x.ok) location.reload(); else toast(errText(x.data), { err: true }); } }, h("option", { value: "he", selected: LANG === "he" }, "עברית"), h("option", { value: "en", selected: LANG === "en" }, "English")), L("הממשק וההודעות יתחלפו מיד.", "The interface and messages switch immediately."))),
    h("section", { class: "card stack" }, h("h2", {}, L("ברירות מחדל לחיפוש", "Search defaults")), errbox,
      h("div", { class: "two" }, fIv, fMiv), h("div", { class: "two" }, field(L("דירוג מינימלי להצגה", "Minimum rating to show"), rate, L("מודעות מתחת לדירוג הזה לא נשמרות ולא נשלחות.", "Listings below this rating are not saved and not sent.")), field(L("מטבע האזור ב-Marketplace", "Marketplace region currency"), cur, L("המטבע שבו פייסבוק מפרשת מחירים באזור שלכם. לא מתבצעת המרה.", "The currency Facebook reads prices in for your region. No conversion is done."))),

      h("div", { class: "row" }, h("button", { class: "primary", onclick: async e => { await busy(e.currentTarget, L("שומר ובודק…", "Saving and checking…"), async () => {
        const x = await api("PUT", "settings/facebook", { search_interval_min: +iv.value, max_search_interval_min: +miv.value, rating: +rate.value, currency: cur.value });
        if (x.ok) { toast(L("שמור", "Saved")); errbox.replaceChildren(); setError(fIv, null); } else { errbox.replaceChildren(notice("bad", L("צריך לתקן:", "Please fix:"), errText(x.data))); toast(L("לא נשמר", "Not saved"), { err: true }); }
      }); } }, L("שמירה", "Save")))),
    h("section", { class: "card" }, h("h2", {}, L("מצב המערכת", "System status")), h("div", { style: "margin-top:12px" }, svc), h("p", { class: "small muted", style: "margin-top:12px" }, L(`תור הודעות: ${sys.outbox.pending} ממתינות · נשלחו ב-24 שעות: ${sys.outbox.sent_24h}`, `Message queue: ${sys.outbox.pending} waiting · sent in 24 hours: ${sys.outbox.sent_24h}`))),
    h("details", { class: "card" }, h("summary", { style: "font-weight:700;cursor:pointer" }, L(`שגיאות אחרונות (${sys.errors.length})`, `Recent errors (${sys.errors.length})`)), h("div", { style: "margin-top:12px" }, sys.errors.length ? sys.errors.slice().reverse().map(e => h("div", { class: "errline" }, h("strong", {}, e.src + " "), e.line)) : h("p", { class: "muted" }, L("אין שגיאות לאחרונה.", "No recent errors.")))),
    h("details", { class: "card" }, h("summary", { style: "font-weight:700;cursor:pointer" }, L("תצורה טכנית (לקריאה בלבד)", "Technical configuration (read-only)")), h("pre", { id: "cfg", style: "margin-top:12px" }, L("טוען…", "Loading…")), h("p", { class: "small muted" }, L("הקובץ נוצר אוטומטית מההגדרות. מפתחות וטוקנים מוסתרים.", "The file is generated automatically from the settings. Keys and tokens are hidden.")))));
  const cfgEl = $("#cfg"); $("#main details:last-of-type").addEventListener("toggle", async function () { if (this.open && cfgEl.textContent === L("טוען…", "Loading…")) { const c = await api("GET", "config"); cfgEl.textContent = c.ok ? c.data.text : L("לא זמין", "Not available"); } });
};

/* ---------------------------------------------------------------- init */
(async function init() {
  const me = await api("GET", "me");
  if (!me.ok) { $("#main").replaceChildren(notice("bad", L("שגיאת התחברות", "Login error"), L("רעננו את הדף והתחברו מחדש.", "Refresh the page and log in again."))); return; }
  CSRF = me.data.csrf;
  window.addEventListener("hashchange", route);
  $("#dlg").addEventListener("click", e => { if (e.target === e.currentTarget) closeDialog(); });
  route(); refreshAttention(); setInterval(() => { if (!document.hidden) refreshAttention(); }, 30000);
})();
