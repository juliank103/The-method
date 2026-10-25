// ==UserScript==
// @name         Tweetus Deletus
// @namespace    app.tweetusdeletus
// @version      1.0.0
// @description  Delete your own X (Twitter) posts in bulk, slowly and safely, from your own browser. Free.
// @author       Tweetus Deletus
// @match        https://x.com/*
// @match        https://twitter.com/*
// @run-at       document-idle
// @grant        none
// @license      MIT
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

  function buildPanel() {
    panel = document.createElement("div");
    panel.id = "tweetus-deletus-panel";
    panel.innerHTML = `
      <style>
        #tweetus-deletus-panel{position:fixed;top:14px;right:14px;z-index:2147483647;
          width:300px;background:#fff;color:#17151f;border:1px solid #e5e2ee;border-radius:14px;
          box-shadow:0 8px 30px rgba(0,0,0,.18);font:14px/1.45 system-ui,sans-serif;overflow:hidden}
        @media (prefers-color-scheme:dark){#tweetus-deletus-panel{background:#18161f;color:#ecebf3;border-color:#2a2734}}
        #tweetus-deletus-panel *{box-sizing:border-box}
        .tdp-head{background:#6d4aff;color:#fff;padding:11px 14px;font-weight:700;display:flex;
          align-items:center;justify-content:space-between}
        .tdp-head button{background:rgba(255,255,255,.2);border:0;color:#fff;border-radius:6px;
          cursor:pointer;font:inherit;padding:2px 8px}
        .tdp-body{padding:14px;display:flex;flex-direction:column;gap:11px}
        .tdp-row{display:flex;align-items:center;gap:8px;justify-content:space-between}
        .tdp-row label{font-weight:500}
        #tweetus-deletus-panel select,#tweetus-deletus-panel input[type=number],
        #tweetus-deletus-panel input[type=text]{font:inherit;padding:5px 8px;border-radius:7px;
          border:1px solid #cfcadd;background:#fff;color:#17151f}
        @media (prefers-color-scheme:dark){#tweetus-deletus-panel select,
          #tweetus-deletus-panel input{background:#232030;color:#ecebf3;border-color:#3a3647}}
        #tweetus-deletus-panel input[type=text]{width:100%}
        .tdp-hint{font-size:12px;opacity:.7}
        .tdp-start{background:#6d4aff;color:#fff;border:0;border-radius:10px;padding:11px;
          font:inherit;font-weight:700;cursor:pointer;width:100%}
        .tdp-start.stop{background:#d93a40}
        .tdp-start:disabled{opacity:.5;cursor:not-allowed}
        .tdp-count{font-weight:700;font-size:15px}
        .tdp-status{font-size:12.5px;min-height:2.6em;opacity:.85}
        .tdp-link{background:none;border:0;color:#6d4aff;cursor:pointer;font:inherit;
          text-decoration:underline;padding:0}
        .tdp-warn{font-size:11.5px;opacity:.65}
      </style>
      <div class="tdp-head"><span>🪄 Tweetus Deletus</span>
        <button id="tdp-min" title="Hide">–</button></div>
      <div class="tdp-body" id="tdp-body">
        <div id="tdp-settings">
          <div class="tdp-row"><label>Speed</label>
            <select id="tdp-pace"></select></div>
          <div class="tdp-row"><label><input type="checkbox" id="tdp-pinned"> Keep my pinned post</label></div>
          <div class="tdp-row"><label>Only older than</label>
            <span><input type="number" id="tdp-older" min="0" style="width:70px"> days</span></div>
          <div><input type="text" id="tdp-words" placeholder="Keep anything saying… (comma separated)"></div>
          <div class="tdp-hint">Go to your profile first. On the <b>Posts</b> tab it deletes
            posts &amp; reposts; on <b>Replies</b> it deletes replies.</div>
        </div>
        <div class="tdp-count"><span id="tdp-deleted">0</span> deleted
          · <span id="tdp-skip">0</span> kept · <span id="tdp-err">0</span> errors</div>
        <div class="tdp-status" id="tdp-status">Ready when you are.</div>
        <button class="tdp-start" id="tdp-start">Start</button>
        <div class="tdp-row">
          <button class="tdp-link" id="tdp-save">Download list of deleted</button>
        </div>
        <div class="tdp-warn">Deleting is permanent. This automates X, which breaks their
          rules, so go slow and start small. It only ever deletes posts you can delete yourself.</div>
      </div>`;
    document.body.appendChild(panel);

    statusEl = panel.querySelector("#tdp-status");
    countEl = panel.querySelector("#tdp-deleted");
    startBtn = panel.querySelector("#tdp-start");

    const paceSel = panel.querySelector("#tdp-pace");
    for (const [key, p] of Object.entries(PACES)) {
      const o = document.createElement("option");
      o.value = key; o.textContent = p.label; paceSel.appendChild(o);
    }
    paceSel.value = S.settings.pace;
    panel.querySelector("#tdp-pinned").checked = S.settings.keepPinned;
    panel.querySelector("#tdp-older").value = S.settings.olderThanDays;
    panel.querySelector("#tdp-words").value = S.settings.keepWords;

    paceSel.onchange = (e) => { S.settings.pace = e.target.value; saveSettings(); };
    panel.querySelector("#tdp-pinned").onchange = (e) => { S.settings.keepPinned = e.target.checked; saveSettings(); };
    panel.querySelector("#tdp-older").onchange = (e) => { S.settings.olderThanDays = e.target.value; saveSettings(); };
    panel.querySelector("#tdp-words").onchange = (e) => { S.settings.keepWords = e.target.value; saveSettings(); };
    startBtn.onclick = onStartStop;
    panel.querySelector("#tdp-save").onclick = downloadLog;
    panel.querySelector("#tdp-min").onclick = () => {
      const b = panel.querySelector("#tdp-body");
      b.style.display = b.style.display === "none" ? "" : "none";
    };
  }

  function onStartStop() {
    if (S.running) { S.stopRequested = true; setStatus("Stopping…"); return; }
    const where = location.pathname;
    const looksLikeProfile = /^\/[A-Za-z0-9_]+(\/with_replies)?\/?$/.test(where);
    if (!looksLikeProfile) {
      setStatus("Open YOUR profile first (click your picture), then press Start.");
      return;
    }
    if (!confirm("Delete the posts shown on this profile tab?\n\n" +
        "This can't be undone. It goes slowly — leave this tab open and in front. " +
        "You can press Stop any time.")) return;
    S.running = true; S.stopRequested = false; S.sinceRest = 0;
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
    panel.querySelector("#tdp-deleted").textContent = S.deleted;
    panel.querySelector("#tdp-skip").textContent = S.skipped;
    panel.querySelector("#tdp-err").textContent = S.errors;
    startBtn.textContent = S.running ? "Stop" : "Start";
    startBtn.classList.toggle("stop", S.running);
    panel.querySelector("#tdp-settings").style.opacity = S.running ? ".5" : "1";
  }

  // Wait for the page to be ready, then show the panel.
  function init() {
    if (!document.body) { setTimeout(init, 300); return; }
    buildPanel();
    render();
  }
  init();
})();
