// ==UserScript==
// @name         Tweetus Deletus
// @namespace    app.tweetusdeletus
// @version      1.0.2
// @description  Delete your own X (Twitter) posts in bulk, slowly and safely, from your own browser. Free.
// @author       Tweetus Deletus
// @match        *://x.com/*
// @match        *://www.x.com/*
// @match        *://twitter.com/*
// @match        *://www.twitter.com/*
// @run-at       document-idle
// @grant        none
// @license      MIT
// @updateURL    https://raw.githubusercontent.com/juliank103/The-method/claude/pensive-goodall-9btu4p/userscript/tweetus-deletus.user.js
// @downloadURL  https://raw.githubusercontent.com/juliank103/The-method/claude/pensive-goodall-9btu4p/userscript/tweetus-deletus.user.js
// ==/UserScript==

/*
 * How it works
 * ------------
 * This runs in YOUR browser, on YOUR x.com tab, while YOU are logged in. It
 * does exactly what you'd do by hand: open a post's "..." menu, click Delete
 * (or Undo repost), and confirm — one post at a time, with long random gaps.
 * It never signs in for you, never sends your details anywhere, and never
 * starts on its own. You press Start, and you can press Stop at any moment.
 *
 * Because it clicks the real buttons slowly, it's gentle on X. But it is still
 * automating the site, which X's rules don't allow, so there's some risk to
 * the account. Go slow, start with a small test, and don't leave it hammering.
 *
 * It can break when X changes their website. If the buttons move, it will say
 * it couldn't find them rather than do anything wrong.
 */

(function () {
  "use strict";
  if (window.__tweetusDeletusLoaded) return;
  window.__tweetusDeletusLoaded = true;

  // ---------------------------------------------------------------- helpers
  // (pure functions, kept together so they can be tested on their own)

  const Helpers = {
    // Pull the numeric post id out of a permalink like /name/status/12345.
    tweetIdFromHref(href) {
      if (!href) return null;
      const m = /\/status\/(\d+)/.exec(href);
      return m ? m[1] : null;
    },

    // A paced gap in milliseconds: base seconds ± jitter, never below a floor.
    gapMs(baseSeconds, jitterSeconds, rnd) {
      const r = (rnd || Math.random)();
      const delta = (r * 2 - 1) * jitterSeconds; // -jitter .. +jitter
      return Math.max(2000, Math.round((baseSeconds + delta) * 1000));
    },

    // Case-insensitive whole-word-ish match used for "keep anything saying…".
    textHasAny(text, words) {
      const low = (text || "").toLowerCase();
      return words.some((w) => {
        w = w.trim().toLowerCase();
        return w && low.includes(w);
      });
    },

    // Decide what a post is from the menu items X shows for it.
    //   "delete"  -> your own post, safe to delete
    //   "unrepost"-> a repost of yours, undo it
    //   "skip"    -> someone else's; leave it alone
    classifyMenu(itemTexts) {
      const t = itemTexts.map((s) => (s || "").trim().toLowerCase());
      if (t.some((s) => s.startsWith("delete"))) return "delete";
      if (t.some((s) => s.includes("undo repost") || s.includes("undo retweet")))
        return "unrepost";
      return "skip";
    },

    // Is this post older than the cutoff? Undated posts are treated as not
    // older (so they're kept) — when unsure, don't delete.
    isOlderThan(postISODate, cutoffMs) {
      if (!cutoffMs) return true; // no date filter -> everything qualifies
      if (!postISODate) return false;
      const t = Date.parse(postISODate);
      if (isNaN(t)) return false;
      return t < cutoffMs;
    },
  };

  // Everything below touches the page and can't run under a plain test.
  if (typeof document === "undefined" || window.__tdHelpersOnly) {
    window.__tdTest = Helpers;
    return;
  }
  window.__tdTest = Helpers;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const H = Helpers;

  // ---------------------------------------------------------------- state
  const PACES = {
    slow:   { label: "Slow — safest",      base: 35, jitter: 15, restEvery: 20, restSec: 180 },
    normal: { label: "Normal",             base: 15, jitter: 7,  restEvery: 30, restSec: 90 },
  };

  const DEFAULTS = {
    pace: "slow",
    keepPinned: true,
    olderThanDays: "",   // "" = any age
    keepWords: "",       // comma-separated
  };

  const S = {
    running: false,
    stopRequested: false,
    deleted: 0,
    skipped: 0,
    errors: 0,
    sinceRest: 0,
    processed: new Set(),
    log: [],
    settings: loadSettings(),
  };

  function loadSettings() {
    try {
      return Object.assign({}, DEFAULTS,
        JSON.parse(localStorage.getItem("tweetus-deletus") || "{}"));
    } catch {
      return Object.assign({}, DEFAULTS);
    }
  }
  function saveSettings() {
    try {
      localStorage.setItem("tweetus-deletus", JSON.stringify(S.settings));
    } catch { /* private mode */ }
  }

  // ---------------------------------------------------------------- DOM bits
  // X's own stable test ids; if these ever change the script says so.
  const SEL = {
    tweet: 'article[data-testid="tweet"]',
    caret: '[data-testid="caret"]',
    menu: '[data-testid="Dropdown"]',
    menuItem: '[role="menuitem"]',
    confirm: '[data-testid="confirmationSheetConfirm"]',
    social: '[data-testid="socialContext"]',
  };

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function tweetId(article) {
    const link = article.querySelector('a[href*="/status/"] time');
    const href = link && link.parentElement ? link.parentElement.getAttribute("href") : null;
    return H.tweetIdFromHref(href);
  }

  function tweetDate(article) {
    const t = article.querySelector("time[datetime]");
    return t ? t.getAttribute("datetime") : null;
  }

  function tweetText(article) {
    const el = article.querySelector('[data-testid="tweetText"]');
    return el ? el.textContent : "";
  }

  function isPinned(article) {
    const s = article.querySelector(SEL.social);
    return !!(s && /pinned/i.test(s.textContent || ""));
  }

  // Find the first post we haven't dealt with yet that passes the filters.
  function nextCandidate() {
    const cutoffMs = S.settings.olderThanDays
      ? Date.now() - Number(S.settings.olderThanDays) * 86400000
      : 0;
    const keepWords = (S.settings.keepWords || "")
      .split(",").map((w) => w.trim()).filter(Boolean);

    for (const art of document.querySelectorAll(SEL.tweet)) {
      const id = tweetId(art);
      if (!id || S.processed.has(id)) continue;
      if (S.settings.keepPinned && isPinned(art)) { S.processed.add(id); continue; }
      if (!H.isOlderThan(tweetDate(art), cutoffMs)) { S.processed.add(id); continue; }
      if (keepWords.length && H.textHasAny(tweetText(art), keepWords)) {
        S.processed.add(id); S.skipped++; continue;
      }
      return { art, id };
    }
    return null;
  }

  function menuItemsText() {
    const menu = document.querySelector(SEL.menu);
    if (!menu) return [];
    return [...menu.querySelectorAll(SEL.menuItem)].map((m) => m.textContent);
  }

  function clickMenuItem(predicate) {
    const menu = document.querySelector(SEL.menu);
    if (!menu) return false;
    for (const item of menu.querySelectorAll(SEL.menuItem)) {
      if (predicate((item.textContent || "").trim().toLowerCase())) {
        item.click();
        return true;
      }
    }
    return false;
  }

  async function closeAnyMenu() {
    if (document.querySelector(SEL.menu)) {
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await sleep(300);
    }
  }

  // Delete (or undo-repost) one post. Returns "deleted" | "skip" | "error".
  async function handleOne(art) {
    art.scrollIntoView({ block: "center" });
    await sleep(500);

    const caret = art.querySelector(SEL.caret);
    if (!caret || !visible(caret)) return "error-nocaret";
    caret.click();

    // Wait for the menu.
    for (let i = 0; i < 20 && !document.querySelector(SEL.menu); i++) await sleep(100);
    if (!document.querySelector(SEL.menu)) return "error-nomenu";

    const kind = H.classifyMenu(menuItemsText());
    if (kind === "skip") { await closeAnyMenu(); return "skip"; }

    const picked = kind === "delete"
      ? clickMenuItem((s) => s.startsWith("delete"))
      : clickMenuItem((s) => s.includes("undo repost") || s.includes("undo retweet"));
    if (!picked) { await closeAnyMenu(); return "error-noitem"; }

    // A confirm sheet usually appears; click it if so.
    await sleep(500);
    for (let i = 0; i < 15 && !document.querySelector(SEL.confirm); i++) await sleep(100);
    const confirm = document.querySelector(SEL.confirm);
    if (confirm) { confirm.click(); await sleep(600); }

    return "deleted";
  }

  async function loadMore() {
    const before = document.querySelectorAll(SEL.tweet).length;
    window.scrollBy(0, Math.round(window.innerHeight * 0.9));
    for (let i = 0; i < 20; i++) {
      await sleep(300);
      if (document.querySelectorAll(SEL.tweet).length > before) return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- the loop
  async function run() {
    // window.__tdPace is only ever set by the offline test harness to shorten
    // the waits; in normal use it is undefined and the real paces are used.
    const pace = window.__tdPace || PACES[S.settings.pace] || PACES.slow;
    let emptyScrolls = 0;

    while (!S.stopRequested) {
      const cand = nextCandidate();

      if (!cand) {
        setStatus("Loading more posts…");
        const got = await loadMore();
        emptyScrolls = got ? 0 : emptyScrolls + 1;
        if (emptyScrolls >= 4) { setStatus("Reached the end. All done."); break; }
        continue;
      }

      S.processed.add(cand.id);
      setStatus("Deleting a post…");
      let result = "error";
      try { result = await handleOne(cand.art); }
      catch (e) { result = "error-exception"; }

      if (result === "deleted") {
        S.deleted++;
        S.sinceRest++;
        S.log.push({ id: cand.id, at: new Date().toISOString() });
      } else if (result === "skip") {
        S.skipped++;
      } else {
        S.errors++;
        await closeAnyMenu();
        if (S.errors <= 3 || S.errors % 10 === 0) {
          setStatus("Couldn't delete one (" + result + "). Skipping it.");
        }
        // If we can't find X's buttons at all, X probably changed; back off.
        if (/nocaret|nomenu|noitem/.test(result) && S.errors >= 8) {
          setStatus("Can't find X's Delete buttons — X may have changed. Stopping.");
          break;
        }
      }
      render();

      if (S.stopRequested) break;

      // A longer rest every so often, to stay gentle.
      if (S.sinceRest >= pace.restEvery) {
        S.sinceRest = 0;
        for (let left = pace.restSec; left > 0 && !S.stopRequested; left--) {
          setStatus("Taking a short break (" + left + "s) so X stays happy…");
          await sleep(1000);
        }
        if (S.stopRequested) break;
      }

      const wait = H.gapMs(pace.base, pace.jitter);
      for (let left = Math.round(wait / 1000); left > 0 && !S.stopRequested; left--) {
        setStatus("Waiting " + left + "s before the next one…");
        await sleep(1000);
      }
    }

    S.running = false;
    S.stopRequested = false;
    if (!/All done|changed/.test(statusEl.textContent)) setStatus("Stopped.");
    render();
  }

  // ---------------------------------------------------------------- panel UI
  let panel, statusEl, countEl, startBtn;

  // Full look of the panel. Injected as a constructed stylesheet, which the
  // page's security policy (CSP) can't block — unlike an inline <style> tag.
  const PANEL_CSS = `
    #tweetus-deletus-panel{position:fixed;top:14px;right:14px;z-index:2147483647;
      width:300px;background:#18161f;color:#ecebf3;border:1px solid #2a2734;border-radius:14px;
      box-shadow:0 8px 30px rgba(0,0,0,.35);font:14px/1.45 system-ui,sans-serif;overflow:hidden}
    #tweetus-deletus-panel *{box-sizing:border-box}
    #tweetus-deletus-panel .tdp-head{background:#6d4aff;color:#fff;padding:11px 14px;font-weight:700;display:flex;
      align-items:center;justify-content:space-between}
    #tweetus-deletus-panel .tdp-head button{background:rgba(255,255,255,.2);border:0;color:#fff;border-radius:6px;
      cursor:pointer;font:inherit;padding:2px 8px}
    #tweetus-deletus-panel .tdp-body{padding:14px;display:flex;flex-direction:column;gap:11px}
    #tweetus-deletus-panel .tdp-row{display:flex;align-items:center;gap:8px;justify-content:space-between}
    #tweetus-deletus-panel .tdp-row label{font-weight:500}
    #tweetus-deletus-panel select,#tweetus-deletus-panel input[type=number],
    #tweetus-deletus-panel input[type=text]{font:inherit;padding:5px 8px;border-radius:7px;
      border:1px solid #3a3647;background:#232030;color:#ecebf3}
    #tweetus-deletus-panel input[type=text]{width:100%}
    #tweetus-deletus-panel .tdp-num{width:72px}
    #tweetus-deletus-panel .tdp-hint{font-size:12px;opacity:.7}
    #tweetus-deletus-panel .tdp-start{background:#6d4aff;color:#fff;border:0;border-radius:10px;padding:11px;
      font:inherit;font-weight:700;cursor:pointer;width:100%}
    #tweetus-deletus-panel .tdp-start.stop{background:#d93a40}
    #tweetus-deletus-panel .tdp-start:disabled{opacity:.5;cursor:not-allowed}
    #tweetus-deletus-panel .tdp-count{font-weight:700;font-size:15px}
    #tweetus-deletus-panel .tdp-status{font-size:12.5px;min-height:2.6em;opacity:.85}
    #tweetus-deletus-panel .tdp-link{background:none;border:0;color:#b9a9ff;cursor:pointer;font:inherit;
      text-decoration:underline;padding:0}
    #tweetus-deletus-panel .tdp-warn{font-size:11.5px;opacity:.65}`;

  function injectStyles() {
    // GM_addStyle (from Tampermonkey) adds styles from the extension, past the
    // page's security policy. Then a constructed stylesheet, then a plain
    // <style>. The panel's own inline styles below are the final safety net.
    try {
      if (typeof GM_addStyle === "function") { GM_addStyle(PANEL_CSS); return; }
    } catch (e) { /* fall through */ }
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(PANEL_CSS);
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
      return;
    } catch (e) { /* older browser: fall back to a <style> element */ }
    try {
      const el = document.createElement("style");
      el.textContent = PANEL_CSS;
      (document.head || document.documentElement).appendChild(el);
    } catch (e) { /* the inline container styles below still show the panel */ }
  }

  // Tiny DOM builder. We never use innerHTML, because X enforces Trusted
  // Types, which makes innerHTML throw — that would stop the panel appearing.
  function h(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "html") { /* intentionally unsupported */ }
      else if (k in n) { try { n[k] = v; } catch { n.setAttribute(k, v); } }
      else n.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }

  let paceSel, pinnedBox, olderBox, wordsBox, bodyEl, settingsEl, skipEl, errEl;

  function buildPanel() {
    injectStyles();
    panel = h("div", { id: "tweetus-deletus-panel" });
    // The essentials are also set straight on the element, so the panel shows
    // even if the page blocks every stylesheet.
    Object.assign(panel.style, {
      position: "fixed", top: "14px", right: "14px", zIndex: "2147483647",
      width: "300px", background: "#18161f", color: "#ecebf3",
      border: "1px solid #2a2734", borderRadius: "14px",
      boxShadow: "0 8px 30px rgba(0,0,0,.35)", overflow: "hidden",
      font: "14px/1.45 system-ui, sans-serif",
    });

    paceSel = h("select", { id: "tdp-pace" });
    for (const [key, p] of Object.entries(PACES))
      paceSel.append(h("option", { value: key, text: p.label }));
    paceSel.value = S.settings.pace;
    paceSel.onchange = (e) => { S.settings.pace = e.target.value; saveSettings(); };

    pinnedBox = h("input", { type: "checkbox", checked: S.settings.keepPinned });
    pinnedBox.onchange = (e) => { S.settings.keepPinned = e.target.checked; saveSettings(); };
    olderBox = h("input", { type: "number", min: "0", class: "tdp-num", value: S.settings.olderThanDays });
    olderBox.onchange = (e) => { S.settings.olderThanDays = e.target.value; saveSettings(); };
    wordsBox = h("input", { type: "text", value: S.settings.keepWords,
      placeholder: "Keep anything saying… (comma separated)" });
    wordsBox.onchange = (e) => { S.settings.keepWords = e.target.value; saveSettings(); };

    countEl = h("span", { id: "tdp-deleted", text: "0" });
    skipEl = h("span", { id: "tdp-skip", text: "0" });
    errEl = h("span", { id: "tdp-err", text: "0" });
    statusEl = h("div", { id: "tdp-status", class: "tdp-status", text: "Ready when you are." });
    startBtn = h("button", { id: "tdp-start", class: "tdp-start", text: "Start", onclick: onStartStop });
    const saveBtn = h("button", { class: "tdp-link", text: "Download list of deleted", onclick: downloadLog });
    const minBtn = h("button", { id: "tdp-min", title: "Hide", text: "–",
      onclick: () => { bodyEl.style.display = bodyEl.style.display === "none" ? "" : "none"; } });

    settingsEl = h("div", {},
      h("div", { class: "tdp-row" }, h("label", { text: "Speed" }), paceSel),
      h("div", { class: "tdp-row" }, h("label", {}, pinnedBox, " Keep my pinned post")),
      h("div", { class: "tdp-row" }, h("label", { text: "Only older than" }),
        h("span", {}, olderBox, " days")),
      h("div", {}, wordsBox),
      h("div", { class: "tdp-hint" }, "Go to your profile first. On the Posts tab it deletes posts & reposts; on Replies it deletes replies."));

    bodyEl = h("div", { class: "tdp-body" },
      settingsEl,
      h("div", { class: "tdp-count" }, countEl, " deleted · ", skipEl, " kept · ", errEl, " errors"),
      statusEl,
      startBtn,
      h("div", { class: "tdp-row" }, saveBtn),
      h("div", { class: "tdp-warn" }, "Deleting is permanent. This automates X, which breaks their rules, so go slow and start small. It only ever deletes posts you can delete yourself."));

    panel.append(
      h("div", { class: "tdp-head" }, h("span", { text: "🪄 Tweetus Deletus" }), minBtn),
      bodyEl);
    document.body.appendChild(panel);
  }

  // Pages that are NOT someone's profile. Anything else of the form /name or
  // /name/<tab> is treated as a profile.
  const NON_PROFILE = new Set(["home", "explore", "notifications", "messages",
    "i", "settings", "compose", "search", "bookmarks", "lists", "jobs",
    "communities", "premium", "hashtag", "tos", "privacy", "login", "logout",
    "account", "intent", "share", "download"]);
  const PROFILE_TABS = /^(with_replies|media|likes|highlights|articles|superfollows|affiliates)$/;

  function onProfile() {
    const seg = location.pathname.replace(/\/+$/, "").split("/").filter(Boolean);
    if (seg.length === 1) return !NON_PROFILE.has(seg[0].toLowerCase());
    if (seg.length === 2) return PROFILE_TABS.test(seg[1].toLowerCase());
    return false;
  }

  let awaitingConfirm = false;
  let confirmTimer = null;

  function resetConfirm() {
    awaitingConfirm = false;
    if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null; }
    render();
  }

  // We do our own two-tap confirm instead of window.confirm(), because X
  // blocks pop-up dialogs from scripts (so the native one silently does
  // nothing).
  function onStartStop() {
    if (S.running) { S.stopRequested = true; setStatus("Stopping…"); return; }

    if (!onProfile()) {
      resetConfirm();
      setStatus("Go to your OWN profile page first — click your picture or @name, "
        + "then press Start. (This page is " + location.pathname + ".)");
      return;
    }

    if (!awaitingConfirm) {
      awaitingConfirm = true;
      startBtn.textContent = "Yes — start deleting";
      startBtn.classList.add("stop");
      setStatus("This permanently deletes the posts shown on this tab. "
        + "Press the button once more to begin. (Resets in 15s.)");
      confirmTimer = setTimeout(resetConfirm, 15000);
      return;
    }

    awaitingConfirm = false;
    if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null; }
    S.running = true; S.stopRequested = false; S.sinceRest = 0;
    setStatus("Starting…");
    render();
    run();
  }

  function downloadLog() {
    if (!S.log.length) { setStatus("Nothing deleted yet."); return; }
    const lines = ["deleted_post_id,when",
      ...S.log.map((r) => r.id + "," + r.at)];
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "tweetus-deletus-deleted.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function setStatus(msg) { if (statusEl) statusEl.textContent = msg; }
  function render() {
    if (!panel) return;
    countEl.textContent = S.deleted;
    skipEl.textContent = S.skipped;
    errEl.textContent = S.errors;
    startBtn.textContent = S.running ? "Stop" : (awaitingConfirm ? "Yes — start deleting" : "Start");
    startBtn.classList.toggle("stop", S.running || awaitingConfirm);
    settingsEl.style.opacity = S.running ? ".5" : "1";
  }

  // Wait for the page to be ready, then show the panel.
  function init() {
    if (!document.body) { setTimeout(init, 300); return; }
    buildPanel();
    render();
    // X is a single-page app and can wipe things on navigation; if our panel
    // ever disappears, put it back.
    setInterval(() => {
      if (!document.getElementById("tweetus-deletus-panel") && !S.running) {
        try { buildPanel(); render(); } catch (e) { /* try again next tick */ }
      }
    }, 3000);
  }
  init();
})();
