"use strict";
(() => {
  const TOKEN = document.querySelector('meta[name="tweetus-token"]').content;
  const $ = (id) => document.getElementById(id);
  const PAGE = 50;

  // ---------- tiny DOM helpers (text only: never innerHTML with data) ----------
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v == null || v === false) continue;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
        else if (k === "dataset") Object.assign(n.dataset, v);
        else if (v === true) n.setAttribute(k, "");
        else n.setAttribute(k, v);
      }
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  const ICONS = {
    posts: "M4 5h16v11H9l-5 4z",
    replies: "M10 7 5 12l5 5M5 12h9a5 5 0 0 1 5 5v1",
    reposts: "M4 10V8a2 2 0 0 1 2-2h12l-3-3M20 14v2a2 2 0 0 1-2 2H6l3 3",
    likes: "M12 20s-7-4.35-7-10a4 4 0 0 1 7-2.65A4 4 0 0 1 19 10c0 5.65-7 10-7 10z",
    bookmarks: "M6 4h12v16l-6-4-6 4z",
    dms: "M3 6h18v12H3zM3 7l9 6 9-6",
    following: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M16 11h6",
    mutes: "M4 9h4l5-4v14l-5-4H4zM17 9l4 6M21 9l-4 6",
    blocks: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM5.6 5.6l12.8 12.8",
    lists: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
    check: "M5 12.5l4.5 4.5L19 7",
  };
  function icon(name) {
    const s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", ICONS[name]);
    s.append(p);
    return s;
  }

  // ---------- vocabulary ----------
  const TYPES = [
    { id: "posts", label: "Posts", desc: "Your posts and quote posts", archive: true },
    { id: "replies", label: "Replies", desc: "Your replies to others", archive: true },
    { id: "reposts", label: "Reposts", desc: "Undo your reposts", archive: true },
    { id: "likes", label: "Likes", desc: "Unlike posts", archive: true },
    { id: "bookmarks", label: "Bookmarks", desc: "Remove bookmarks", archive: false },
    { id: "dms", label: "Messages", desc: "DMs you sent", archive: true },
    { id: "following", label: "Following", desc: "Unfollow accounts", archive: false },
    { id: "mutes", label: "Mutes", desc: "Unmute accounts", archive: false },
    { id: "blocks", label: "Blocks", desc: "Unblock accounts", archive: false },
    { id: "lists", label: "Lists", desc: "Lists you made", archive: true },
  ];
  const AUTO_TYPES = ["posts", "replies", "reposts", "likes", "bookmarks", "dms"];
  const KIND = { post: "Post", reply: "Reply", quote: "Quote", repost: "Repost", like: "Like",
    bookmark: "Bookmark", dm: "Message", follow: "Follow", mute: "Mute", block: "Block", list: "List" };
  const PLURAL = { post: "posts", reply: "replies", quote: "quote posts", repost: "reposts",
    like: "likes", bookmark: "bookmarks", dm: "messages", follow: "follows", mute: "mutes",
    block: "blocks", list: "lists" };
  const SINGULAR = { post: "post", reply: "reply", quote: "quote post", repost: "repost",
    like: "like", bookmark: "bookmark", dm: "message", follow: "follow", mute: "mute",
    block: "block", list: "list" };
  const TWEETLIKE = new Set(["post", "reply", "quote", "repost", "like", "bookmark"]);
  const PEOPLE = new Set(["follow", "mute", "block"]);

  // ---------- state ----------
  const S = {
    status: null,
    types: new Set(["posts", "replies"]),
    source: "live",
    media: "any",
    scan: null,            // { id, items, stats }
    excluded: new Set(),   // "kind:id" of unchecked results
    page: 0, q: "", kind: "",
    hist: { page: 0, q: "", total: 0 },
    job: null,
    pollTimer: null,
    justConnected: false,
    uploading: false,
  };

  // ---------- formatting ----------
  const fmt = (n) => Number(n || 0).toLocaleString();
  const plural = (n, kind) => `${fmt(n)} ${n === 1 ? SINGULAR[kind] || kind : PLURAL[kind] || kind}`;
  const items = (n) => `${fmt(n)} ${n === 1 ? "item" : "items"}`;
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  function fmtMonth(iso) {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short" });
  }
  function relTime(epochSec) {
    if (!epochSec) return "";
    const diff = (epochSec * 1000 - Date.now()) / 1000;
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    const abs = Math.abs(diff);
    if (abs < 45) return diff < 0 ? "just now" : "in a moment";
    if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
    return rtf.format(Math.round(diff / 86400), "day");
  }
  function duration(sec) {
    if (sec < 60) return "under a minute";
    if (sec < 3600) return `${Math.round(sec / 60)} min`;
    const h = sec / 3600;
    return `${h < 10 ? h.toFixed(1) : Math.round(h)} hours`;
  }
  function clock(epochSec) {
    return new Date(epochSec * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  const splitList = (s) => (s || "").split(/[,\n]/).map((x) => x.trim()).filter(Boolean);

  // ---------- server ----------
  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: { "X-Tweetus-Token": TOKEN } };
    if (opts.json !== undefined) {
      init.body = JSON.stringify(opts.json);
      init.headers["Content-Type"] = "application/json";
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch {
      throw new Error("Can't reach Tweetus Deletus. Is it still running?");
    }
    const ct = res.headers.get("Content-Type") || "";
    if (ct.includes("application/json")) {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Something went wrong (${res.status}).`);
      return data;
    }
    if (!res.ok) throw new Error(`Something went wrong (${res.status}).`);
    return res;
  }

  // ---------- banners, toast, modal ----------
  function banner(kind, msg, opts = {}) {
    const id = opts.id || "";
    if (id) clearBanner(id);
    const b = el("div", { class: `banner ${kind}`, role: kind === "error" ? "alert" : "status", dataset: { id, kind } },
      el("span", { class: "grow", text: msg }),
      opts.action ? el("button", { type: "button", class: "btn ghost small", text: opts.action.label, onclick: opts.action.run }) : null,
      opts.sticky ? null : el("button", { type: "button", class: "link-btn", text: "Dismiss", onclick: () => b.remove() }));
    $("banners").append(b);
    if (kind === "error") window.scrollTo({ top: 0, behavior: "smooth" });
    return b;
  }
  function clearBanner(id) {
    for (const b of $("banners").querySelectorAll(`[data-id="${id}"]`)) b.remove();
  }
  function clearErrors() {
    for (const b of $("banners").querySelectorAll('[data-kind="error"]')) b.remove();
  }

  let toastTimer;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
  }

  let modalState = null;
  function openModal({ title, body, phrase, ok, danger = true, onOk }) {
    modalState = { phrase, onOk, returnFocus: document.activeElement };
    $("modal-title").textContent = title;
    $("modal-body").textContent = body || "";
    const okBtn = $("modal-ok");
    okBtn.textContent = ok || "OK";
    okBtn.className = `btn ${danger ? "danger" : "primary"}`;
    const input = $("modal-input");
    input.value = "";
    $("modal-label").hidden = input.hidden = !phrase;
    if (phrase) $("modal-label").querySelector("b").textContent = phrase;
    okBtn.disabled = !!phrase;
    $("modal").hidden = false;
    (phrase ? input : okBtn).focus();
  }
  function closeModal() {
    $("modal").hidden = true;
    const rf = modalState && modalState.returnFocus;
    modalState = null;
    if (rf && rf.focus) rf.focus();
  }
  function modalOk() {
    if (!modalState || $("modal-ok").disabled) return;
    const fn = modalState.onOk;
    closeModal();
    fn();
  }

  // ---------- views ----------
  const TABS = ["delete", "auto", "history", "settings"];
  function showView(name) {
    for (const v of ["loading", "setup", ...TABS]) $(`view-${v}`).hidden = v !== name;
    for (const b of $("tabs").querySelectorAll("button")) {
      if (b.dataset.tab === name) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    }
  }
  function go(tab) {
    if (!TABS.includes(tab)) tab = "delete";
    showView(tab);
    if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
    if (tab === "history") loadHistory();
    if (tab === "settings") renderSettings();
    if (tab === "auto") renderAuto();
    window.scrollTo(0, 0);
  }
  function ready() {
    const st = S.status;
    return st.demo || (st.configured && st.connected);
  }
  function route() {
    $("tabs").hidden = !ready();
    if (!ready()) {
      renderSetup();
      showView("setup");
      return;
    }
    go((location.hash || "").slice(1));
  }

  async function refreshStatus() {
    S.status = await api("/api/status");
    renderAccountChip();
    renderArchive();
    renderKeepHint();
    renderStatusBanners();
    return S.status;
  }

  function renderStatusBanners() {
    const st = S.status;
    if (st.demo) {
      banner("warn", "Demo mode: this is a pretend account and nothing real is deleted. Start without --demo to use your own.", { id: "demo", sticky: true });
    }
    clearBanner("me");
    if (!st.demo && st.connected && !st.me && st.me_error) {
      banner("warn", `Couldn't load your X profile: ${st.me_error}`, {
        id: "me", action: { label: "Reconnect", run: () => { location.href = "/login"; } } });
    }
  }

  function renderAccountChip() {
    const chip = $("account-chip");
    chip.replaceChildren();
    const st = S.status;
    const job = S.job && S.job.state === "running" ? S.job : null;
    if (job) {
      const label = job.kind === "scan" ? "Searching…"
        : job.kind === "auto" ? "Auto-delete running"
        : `Deleting ${fmt(job.done)}/${fmt(job.total)}`;
      chip.append(el("button", { type: "button", class: "pill", onclick: () => go(job.kind === "auto" ? "auto" : "delete") },
        el("span", { class: "dot" }), label));
    }
    if (st.demo) chip.append(el("span", { class: "pill demo", text: "Demo" }));
    if (st.me) {
      const who = el("span", { class: "who" });
      if (st.me.profile_image_url) who.append(el("img", { class: "avatar", src: st.me.profile_image_url, alt: "" }));
      who.append(`@${st.me.username}`);
      chip.append(who);
    }
  }

  // ---------- setup ----------
  function renderSetup() {
    const st = S.status;
    $("setup-callback").textContent = st.redirect_uri;
    if (!$("setup-client-id").value) $("setup-client-id").value = st.client_id || "";
    $("step-1").classList.toggle("done", st.configured);
    $("step-2").classList.toggle("done", st.configured);
    $("step-3").classList.toggle("locked", !st.configured);
    const btn = $("connect-btn");
    btn.setAttribute("aria-disabled", String(!st.configured));
    if (st.config_error) banner("error", st.config_error, { id: "config" });
  }

  async function saveSetup(ev) {
    ev.preventDefault();
    const client_id = $("setup-client-id").value.trim();
    const secret = $("setup-client-secret").value.trim();
    const json = { client_id };
    if (secret) json.client_secret = secret;
    try {
      await api("/api/setup", { method: "POST", json });
      clearErrors();
      toast("Saved. Now connect your account.");
      await refreshStatus();
      renderSetup();
      $("connect-btn").focus();
    } catch (e) {
      banner("error", e.message);
    }
  }

  // ---------- step 1: types ----------
  function buildTypeGrid() {
    const grid = $("type-grid");
    for (const t of TYPES) {
      const input = el("input", { type: "checkbox", value: t.id, "aria-label": t.label });
      const tile = el("label", { class: "type-tile", dataset: { type: t.id } },
        input,
        el("span", { class: "tick" }, icon("check")),
        icon(t.id),
        el("b", { text: t.label }),
        el("span", { text: t.desc }),
        el("span", { class: "src-note", text: "Read from X", hidden: true }));
      input.addEventListener("change", () => {
        if (input.checked) S.types.add(t.id); else S.types.delete(t.id);
        syncTypes();
        saveForm();
      });
      grid.append(tile);
    }
  }
  function syncTypes() {
    for (const tile of $("type-grid").children) {
      const t = TYPES.find((x) => x.id === tile.dataset.type);
      const on = S.types.has(t.id);
      tile.classList.toggle("on", on);
      tile.querySelector("input").checked = on;
      tile.querySelector(".src-note").hidden = !(S.source === "archive" && !t.archive);
    }
    $("scan-btn").disabled = S.types.size === 0;
  }
  function quickPick(which) {
    if (which === "posts") S.types = new Set(["posts", "replies"]);
    else if (which === "all") S.types = new Set(TYPES.map((t) => t.id));
    else S.types = new Set();
    syncTypes();
    saveForm();
  }

  // ---------- step 2: source & archive ----------
  function setSource(v) {
    S.source = v;
    for (const r of document.querySelectorAll('input[name="source"]')) r.checked = r.value === v;
    $("archive-box").hidden = v !== "archive";
    syncTypes();
  }

  function renderArchive() {
    if (S.uploading) return; // don't yank the progress bar mid-upload
    const a = S.status && S.status.archive;
    $("archive-uploading").hidden = true;
    $("archive-drop").hidden = !!a;
    const box = $("archive-loaded");
    box.hidden = !a;
    box.replaceChildren();
    if (!a) return;
    const c = a.counts || {};
    const parts = [];
    if (c.posts) parts.push(`${fmt(c.posts)} posts`);
    if (c.likes) parts.push(`${fmt(c.likes)} likes`);
    if (c.dms) parts.push(`${fmt(c.dms)} messages you sent`);
    if (c.lists) parts.push(`${fmt(c.lists)} lists`);
    const owner = a.account && a.account.username ? `Archive for @${a.account.username}` : a.filename || "Your archive";
    const meta = el("div", { class: "meta" },
      el("b", { text: owner }),
      el("div", { class: "counts", text: parts.join(" · ") || "No posts or likes found" }),
      a.oldest && a.newest ? el("div", { class: "hint", text: `Posts from ${fmtMonth(a.oldest)} to ${fmtMonth(a.newest)}. Anything newer isn't in it.` }) : null);
    const me = S.status.me;
    if (me && a.account && a.account.id && a.account.id !== me.id && !S.status.demo) {
      meta.append(el("div", { class: "banner warn", text: `This archive belongs to @${a.account.username}, but you're connected as @${me.username}. Only your own things can be deleted.` }));
    }
    box.append(el("div", { class: "file-icon", text: "ZIP" }), meta,
      el("div", { class: "row" },
        el("button", { type: "button", class: "btn ghost small", text: "Replace", onclick: () => $("archive-file").click() }),
        el("button", { type: "button", class: "btn ghost small", text: "Remove", onclick: removeArchive })));
  }

  function uploadArchive(file) {
    if (!file) return;
    if (!/\.zip$/i.test(file.name)) {
      banner("error", "That isn't a .zip file. Upload the archive exactly as X sent it to you.");
      return;
    }
    clearErrors();
    S.uploading = true;
    $("archive-drop").hidden = true;
    $("archive-loaded").hidden = true;
    $("archive-uploading").hidden = false;
    const label = $("archive-up-label");
    const bar = $("archive-up-bar");
    label.textContent = `Uploading ${file.name}…`;
    bar.style.width = "0%";
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/archive");
    xhr.setRequestHeader("X-Tweetus-Token", TOKEN);
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.style.width = `${pct}%`;
      label.textContent = pct < 100 ? `Uploading ${file.name}… ${pct}%` : "Reading your archive…";
    };
    xhr.onload = () => {
      S.uploading = false;
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status === 200) {
        S.status.archive = data.archive;
        renderArchive();
        toast("Archive ready.");
      } else {
        renderArchive();
        banner("error", data.error || `Upload failed (${xhr.status}).`);
      }
    };
    xhr.onerror = () => {
      S.uploading = false;
      renderArchive();
      banner("error", "The upload failed. Is Tweetus Deletus still running?");
    };
    xhr.send(file);
  }

  function removeArchive() {
    openModal({
      title: "Remove this archive?",
      body: "This only removes the copy Tweetus Deletus keeps. Nothing on X is affected.",
      ok: "Remove", onOk: async () => {
        try {
          await api("/api/archive", { method: "DELETE" });
          S.status.archive = null;
          renderArchive();
        } catch (e) { banner("error", e.message); }
      } });
  }

  // ---------- steps 3 & 4: filters ----------
  function syncWhen() {
    const v = $("f-when").value;
    $("f-when-older").hidden = v !== "older";
    $("f-when-before").hidden = v !== "before";
    $("f-when-between").hidden = v !== "between";
  }
  function setMedia(v) {
    S.media = v;
    for (const b of $("f-media").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.v === v));
  }

  const FORM_FIELDS = ["f-when", "f-older", "f-before", "f-after2", "f-before2", "f-words", "f-words-mode",
    "f-max-likes", "f-max-reposts", "f-hashtags", "f-min-likes", "f-regex", "k-words", "k-ids", "k-people"];
  const FORM_CHECKS = ["f-links", "k-pinned"];

  function saveForm() {
    const s = { types: [...S.types], source: S.source, media: S.media, v: {}, c: {} };
    for (const id of FORM_FIELDS) s.v[id] = $(id).value;
    for (const id of FORM_CHECKS) s.c[id] = $(id).checked;
    try { localStorage.setItem("td-form", JSON.stringify(s)); } catch { /* private mode */ }
  }
  function restoreForm() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem("td-form") || "null"); } catch { /* ignore */ }
    if (s) {
      if (Array.isArray(s.types)) S.types = new Set(s.types.filter((t) => TYPES.some((x) => x.id === t)));
      for (const id of FORM_FIELDS) if (s.v && typeof s.v[id] === "string") $(id).value = s.v[id];
      for (const id of FORM_CHECKS) if (s.c && typeof s.c[id] === "boolean") $(id).checked = s.c[id];
      if (s.media) S.media = s.media;
      if (s.source) S.source = s.source;
    }
    if (!["any", "older", "before", "between"].includes($("f-when").value)) $("f-when").value = "any";
    setMedia(S.media);
    setSource(S.source === "archive" ? "archive" : "live");
    syncWhen();
    syncTypes();
  }

  function localMidnight(dateStr) {
    const d = new Date(`${dateStr}T00:00:00`);
    if (isNaN(d)) throw new Error("That date doesn't look right.");
    return d;
  }

  function collectFilters() {
    const f = {};
    const when = $("f-when").value;
    if (when === "older") {
      if (!$("f-older").value || Number($("f-older").value) < 1) throw new Error("Enter how many days old, e.g. 30.");
      f.older_than_days = $("f-older").value;
    } else if (when === "before") {
      if (!$("f-before").value) throw new Error("Pick the date to delete things before.");
      f.before = localMidnight($("f-before").value).toISOString();
    } else if (when === "between") {
      const a = $("f-after2").value, b = $("f-before2").value;
      if (!a || !b) throw new Error("Pick both dates.");
      const start = localMidnight(a), end = localMidnight(b);
      if (start > end) throw new Error("The first date should come before the second.");
      f.after = new Date(start.getTime() - 1).toISOString();
      end.setDate(end.getDate() + 1); // include the whole last day
      f.before = end.toISOString();
    }
    f.keywords = splitList($("f-words").value);
    f.keywords_all = $("f-words-mode").value === "all";
    f.max_likes = $("f-max-likes").value;
    f.max_reposts = $("f-max-reposts").value;
    f.min_likes = $("f-min-likes").value;
    f.media = S.media;
    f.links_only = $("f-links").checked;
    f.hashtags = splitList($("f-hashtags").value);
    f.regex = $("f-regex").value.trim();
    f.keep_pinned = $("k-pinned").checked;
    f.keep_keywords = splitList($("k-words").value);
    f.keep_ids = ($("k-ids").value || "").split(/\s+/).filter(Boolean);
    f.keep_handles = splitList($("k-people").value);
    return f;
  }

  function renderKeepHint() {
    const n = S.status ? S.status.keep_count : 0;
    const h = $("keep-count-hint");
    h.hidden = !n;
    h.textContent = n ? `Plus ${items(n)} on your always-keep list (see Settings).` : "";
  }

  // ---------- delete flow stages ----------
  const STAGES = { form: "delete-form", scanning: "scan-progress", results: "results", deleting: "delete-progress", done: "delete-done" };
  function stage(name) {
    for (const [k, id] of Object.entries(STAGES)) $(id).hidden = k !== name;
  }

  async function startScan() {
    if (!S.types.size) { toast("Pick at least one thing to delete."); return; }
    let filters;
    try { filters = collectFilters(); } catch (e) { banner("error", e.message); return; }
    if (S.source === "archive" && !S.status.archive) {
      banner("error", "Upload your X archive first, or choose “My account on X”.");
      return;
    }
    clearErrors();
    saveForm();
    try {
      const { job } = await api("/api/scan", { method: "POST", json: { types: [...S.types], source: S.source, filters } });
      attachJob(job);
    } catch (e) {
      banner("error", e.message);
    }
  }

  // ---------- jobs ----------
  function attachJob(job) {
    S.job = job;
    renderJob(job);
    if (job.state === "running") startPolling();
    else onJobFinished(job); // finished before we even asked
  }
  function startPolling() {
    if (!S.pollTimer) S.pollTimer = setInterval(poll, 800);
  }
  function stopPolling() {
    clearInterval(S.pollTimer);
    S.pollTimer = null;
  }
  async function poll() {
    let job;
    try { ({ job } = await api("/api/job")); } catch { return; }
    if (!job) { stopPolling(); return; }
    const wasRunning = S.job && S.job.id === job.id && S.job.state === "running";
    S.job = job;
    renderJob(job);
    if (job.state !== "running") {
      stopPolling();
      if (wasRunning) onJobFinished(job);
    }
  }
  // Auto-delete runs on the server's own schedule; check in now and then so
  // the page notices runs it didn't start.
  async function idleRefresh() {
    if (S.pollTimer || !S.status || !ready() || !$("modal").hidden) return;
    try { await refreshStatus(); } catch { return; }
    const job = S.status.job;
    const tracking = S.job && S.job.id === (job && job.id) && S.job.state === "running";
    if (job && job.state === "running" && !tracking) attachJob(job);
    if (!$("view-auto").hidden) renderAutoStatus();
  }

  async function cancelJob() {
    try { await api("/api/job/cancel", { method: "POST" }); } catch (e) { banner("error", e.message); }
  }

  function renderJob(job) {
    renderAccountChip();
    if (job.kind === "scan" && job.state === "running") {
      stage("scanning");
      $("sp-note").textContent = job.note || "Starting…";
      $("sp-count").textContent = job.done ? `Checked ${fmt(job.done)} so far · ${fmt((job.stats || {}).matched)} match` : "";
    } else if (job.kind === "delete" && job.state === "running") {
      stage("deleting");
      renderDeleting(job);
    }
    if (job.kind === "auto") renderAutoStatus();
  }

  async function onJobFinished(job) {
    if (job.kind === "scan") {
      if (job.state === "done") {
        await loadResults(job.id);
      } else {
        stage("form");
        if (job.state === "error") banner("error", job.error);
        else toast("Stopped.");
      }
    } else if (job.kind === "delete") {
      renderDone(job);
      stage("done");
      if (job.state === "error") banner("error", job.error);
      refreshStatus().catch(() => {});
    } else if (job.kind === "auto") {
      await refreshStatus().catch(() => {});
      renderAutoStatus();
      const st = job.stats || {};
      if (job.state === "done") toast(`Auto-delete finished: ${items(st.deleted || 0)} deleted.`);
      else if (job.state === "error") banner("error", `Auto-delete stopped: ${job.error}`);
    }
  }

  // ---------- results ----------
  async function loadResults(id) {
    try {
      const data = await api(`/api/scan/results?id=${encodeURIComponent(id)}`);
      S.scan = { id, items: data.items, stats: data.stats || {} };
      S.excluded = new Set();
      S.page = 0; S.q = ""; S.kind = "";
      $("results-search").value = "";
      $("remember-unchecked").checked = false;
      stage("results");
      renderResults();
      window.scrollTo(0, 0);
    } catch (e) {
      stage("form");
      banner("error", e.message);
    }
  }

  const key = (it) => `${it.kind}:${it.id}`;
  function filtered() {
    const q = S.q.toLowerCase();
    return S.scan.items.filter((it) => (!S.kind || it.kind === S.kind)
      && (!q || (it.text || "").toLowerCase().includes(q) || (it.handle || "").toLowerCase().includes(q)));
  }
  function selectedCount() {
    return S.scan.items.length - S.excluded.size;
  }

  function renderResults() {
    const all = S.scan.items;
    const stats = S.scan.stats;
    const counts = {};
    for (const it of all) counts[it.kind] = (counts[it.kind] || 0) + 1;

    $("results-title").textContent = all.length ? `${items(all.length)} found` : "Nothing matched";
    $("results-chips").replaceChildren(...Object.entries(counts).map(([k, n]) => el("span", { class: "pill", text: plural(n, k) })));
    const sub = [];
    if (stats.protected) sub.push(`${fmt(stats.protected)} kept safe by your rules`);
    if (stats.already) sub.push(`${fmt(stats.already)} already deleted earlier, so skipped`);
    if (stats.undated) sub.push(`${fmt(stats.undated)} skipped because the archive has no date for them`);
    $("results-sub").textContent = sub.join(" · ");

    const kindSel = $("results-kind");
    const kinds = Object.keys(counts);
    kindSel.replaceChildren(el("option", { value: "", text: "All types" }),
      ...kinds.map((k) => el("option", { value: k, text: PLURAL[k][0].toUpperCase() + PLURAL[k].slice(1) })));
    kindSel.value = S.kind;
    kindSel.hidden = kinds.length < 2;
    $("results-toolbar").hidden = all.length === 0;
    $("confirm-card").hidden = all.length === 0;
    renderResultPage();
  }

  function renderResultPage() {
    const list = $("item-list");
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    S.page = Math.min(S.page, pages - 1);
    const slice = rows.slice(S.page * PAGE, S.page * PAGE + PAGE);
    if (!S.scan.items.length) {
      list.replaceChildren(el("li", { class: "empty" },
        el("p", { text: "Nothing matched these filters." }),
        el("button", { type: "button", class: "btn ghost", text: "Change filters", onclick: backToForm })));
    } else if (!rows.length) {
      list.replaceChildren(el("li", { class: "empty", text: "Nothing in these results matches your search." }));
    } else {
      list.replaceChildren(...slice.map(resultRow));
    }
    renderPager($("pager"), S.page, pages, rows.length, (p) => { S.page = p; renderResultPage(); $("results").scrollIntoView({ behavior: "smooth" }); });
    syncDeleteButton();
  }

  function itemBody(it) {
    const top = el("div", { class: "item-top" },
      el("span", { class: `badge ${it.kind}`, text: KIND[it.kind] || it.kind }),
      it.date ? el("span", { text: fmtDate(it.date) }) : null);
    if (TWEETLIKE.has(it.kind) && (it.likes || it.reposts || it.replies)) {
      top.append(el("span", { class: "metrics", title: "Likes · reposts · replies" },
        el("span", { text: `♥ ${fmt(it.likes)}` }), el("span", { text: `↻ ${fmt(it.reposts)}` }),
        el("span", { text: `💬 ${fmt(it.replies)}` })));
    }
    if (it.media && it.media.length) top.append(el("span", { title: it.media.join(", "), text: it.media.includes("video") ? "🎬" : "🖼" }));
    if (it.link) top.append(el("span", { title: "Has a link", text: "🔗" }));
    let text;
    if (PEOPLE.has(it.kind)) text = [it.handle, it.text].filter(Boolean).join(" · ");
    else text = it.text || "(no text)";
    return [top, el("p", { class: "text", text })];
  }

  function resultRow(it) {
    const k = key(it);
    const on = !S.excluded.has(k);
    const cb = el("input", { type: "checkbox", "aria-label": `Delete this ${SINGULAR[it.kind] || "item"}` });
    cb.checked = on;
    const li = el("li", { class: `item${on ? "" : " off"}` }, cb,
      el("div", { class: "item-main" }, ...itemBody(it)),
      it.url ? el("a", { class: "open-link", href: it.url, target: "_blank", rel: "noopener noreferrer", title: "Open on X", text: "Open ↗" }) : el("span"));
    cb.addEventListener("change", () => {
      if (cb.checked) S.excluded.delete(k); else S.excluded.add(k);
      li.classList.toggle("off", !cb.checked);
      syncDeleteButton();
    });
    return li;
  }

  function renderPager(box, page, pages, total, onPage) {
    box.replaceChildren();
    if (pages <= 1) return;
    box.append(
      el("button", { type: "button", class: "btn ghost small", text: "‹ Newer", disabled: page === 0, onclick: () => onPage(page - 1) }),
      el("span", { text: `Page ${fmt(page + 1)} of ${fmt(pages)} · ${fmt(total)} total` }),
      el("button", { type: "button", class: "btn ghost small", text: "Older ›", disabled: page >= pages - 1, onclick: () => onPage(page + 1) }));
  }

  function syncDeleteButton() {
    const n = selectedCount();
    const btn = $("delete-btn");
    btn.textContent = n ? `Delete ${items(n)}` : "Nothing selected";
    btn.disabled = n === 0;
    const parts = [];
    if (S.excluded.size) parts.push(`${fmt(S.excluded.size)} unchecked will be left alone.`);
    if (!S.status.demo && n) parts.push(`Uses about ${fmt(n)} X API requests.`);
    parts.push("A copy of everything is saved in History.");
    $("delete-hint").textContent = parts.join(" ");
  }

  function selectAll(on) {
    for (const it of filtered()) {
      if (on) S.excluded.delete(key(it)); else S.excluded.add(key(it));
    }
    renderResultPage();
  }

  function backToForm() {
    S.scan = null;
    stage("form");
    window.scrollTo(0, 0);
  }

  function confirmDelete() {
    const n = selectedCount();
    if (!n) return;
    const counts = {};
    for (const it of S.scan.items) if (!S.excluded.has(key(it))) counts[it.kind] = (counts[it.kind] || 0) + 1;
    const what = Object.entries(counts).map(([k, c]) => plural(c, k)).join(", ");
    openModal({
      title: `Delete ${items(n)}?`,
      body: `That's ${what}. This can't be undone on X. A copy of each one is saved in History on this computer.`,
      phrase: n > 20 ? "DELETE" : null,
      ok: "Delete forever",
      onOk: doDelete,
    });
  }

  async function doDelete() {
    clearErrors();
    try {
      const { job } = await api("/api/delete", { method: "POST",
        json: { scan_id: S.scan.id, exclude: [...S.excluded], remember: $("remember-unchecked").checked } });
      if ($("remember-unchecked").checked && S.excluded.size) toast(`${items(S.excluded.size)} added to your always-keep list.`);
      attachJob(job);
    } catch (e) {
      banner("error", e.message);
    }
  }

  function renderLog(ul, log) {
    ul.replaceChildren(...[...(log || [])].reverse().map((e) =>
      el("li", {}, el("time", { text: clock(e.t) }), e.msg)));
  }

  function renderDeleting(job) {
    const total = job.total || 0;
    const done = job.done || 0;
    const st = job.stats || {};
    $("dp-title").textContent = total ? `Deleting ${items(total)}…` : "Starting…";
    $("dp-bar").style.width = total ? `${(done / total) * 100}%` : "0%";
    $("dp-count").textContent = `${fmt(done)} of ${fmt(total)} done${st.errors ? ` · ${fmt(st.errors)} failed` : ""}`;
    const elapsed = Date.now() / 1000 - job.started;
    $("dp-eta").textContent = done >= 3 && done < total ? `About ${duration(((total - done) * elapsed) / done)} left` : "";
    const note = $("dp-note");
    note.textContent = job.note || "";
    note.classList.toggle("wait", /rate.limit|waiting/i.test(job.note || ""));
    renderLog($("dp-log"), job.log);
  }

  function renderDone(job) {
    const st = job.stats || {};
    const deleted = st.deleted || 0;
    let title, mark;
    if (job.state === "cancelled") { title = `Stopped. ${items(deleted)} deleted.`; mark = "⏸️"; }
    else if (job.state === "error") { title = `Stopped early. ${items(deleted)} deleted.`; mark = "⚠️"; }
    else if (deleted) { title = `Poof! ${items(deleted)} deleted.`; mark = "✨"; }
    else { title = "Done."; mark = "✨"; }
    $("dd-title").textContent = title;
    document.querySelector("#delete-done .done-mark").textContent = mark;
    const parts = Object.entries(st.by_kind || {}).map(([k, n]) => plural(n, k));
    if (st.already_gone) parts.push(`${fmt(st.already_gone)} were already gone`);
    if (st.errors) parts.push(`${fmt(st.errors)} couldn't be deleted (see Activity)`);
    $("dd-sub").textContent = parts.join(" · ");
    renderLog($("dd-log"), job.log);
    $("dd-log-wrap").hidden = !(job.log || []).length;
  }

  // ---------- auto-delete ----------
  function buildAutoTypes() {
    const box = $("a-types");
    for (const id of AUTO_TYPES) {
      const t = TYPES.find((x) => x.id === id);
      box.append(el("label", { class: "toggle-pill" }, el("input", { type: "checkbox", value: id }), t.label.toLowerCase()));
    }
  }
  function renderAuto() {
    const a = S.status.auto;
    $("a-enabled").checked = !!a.enabled;
    for (const cb of $("a-types").querySelectorAll("input")) cb.checked = (a.types || []).includes(cb.value);
    $("a-older").value = a.older_than_days;
    const sel = $("a-interval");
    const iv = String(Number(a.interval_hours));
    if (![...sel.options].some((o) => o.value === iv)) sel.append(el("option", { value: iv, text: `${iv} hours` }));
    sel.value = iv;
    $("a-pinned").checked = a.keep_pinned !== false;
    $("a-max-likes").value = a.max_likes == null ? "" : a.max_likes;
    $("a-words").value = (a.keep_keywords || []).join(", ");
    renderAutoStatus();
  }
  function collectAuto() {
    return {
      enabled: $("a-enabled").checked,
      types: [...$("a-types").querySelectorAll("input:checked")].map((c) => c.value),
      older_than_days: $("a-older").value,
      interval_hours: $("a-interval").value,
      keep_pinned: $("a-pinned").checked,
      max_likes: $("a-max-likes").value,
      keep_keywords: splitList($("a-words").value),
    };
  }
  async function saveAuto(fromSwitch) {
    const rule = collectAuto();
    const wasOn = S.status.auto.enabled;
    const save = async () => {
      try {
        const res = await api("/api/auto", { method: "POST", json: rule });
        S.status.auto = res.auto;
        renderAuto();
        clearErrors();
        toast(rule.enabled ? "Saved. Auto-delete is on." : "Saved. Auto-delete is off.");
      } catch (e) {
        $("a-enabled").checked = wasOn;
        banner("error", e.message);
      }
    };
    if (rule.enabled && !wasOn) {
      const what = rule.types.map((t) => TYPES.find((x) => x.id === t).label.toLowerCase()).join(", ") || "nothing";
      openModal({
        title: "Turn on auto-delete?",
        body: `Every ${$("a-interval").selectedOptions[0].textContent}, your ${what} older than ${rule.older_than_days} days will be deleted for good. The first run starts right away.`,
        ok: "Turn on", onOk: save,
      });
      if (fromSwitch) $("a-enabled").checked = false; // until confirmed
      return;
    }
    await save();
  }
  async function runAutoNow() {
    try {
      const { job } = await api("/api/auto/run", { method: "POST" });
      if (job) { attachJob(job); toast("Auto-delete is running."); }
    } catch (e) { banner("error", e.message); }
  }
  function renderAutoStatus() {
    if (!S.status) return;
    const a = S.status.auto;
    const box = $("a-status");
    box.replaceChildren();
    const job = S.job;
    if (job && job.kind === "auto" && job.state === "running") {
      box.append(el("b", { text: "Running now…" }), el("span", { class: "muted", text: job.note || "" }));
      return;
    }
    if (!a.enabled) box.append(el("b", { text: "Auto-delete is off." }));
    else if (!a.next_run || a.next_run - Date.now() / 1000 < 60) box.append(el("b", { text: "On. Next check any moment now." }));
    else box.append(el("b", { text: `On. Next check ${relTime(a.next_run)}.` }));
    const r = a.last_result;
    if (r && r.at) {
      box.append(el("span", { class: "muted", text: r.ok
        ? `Last run ${relTime(r.at)}: ${items(r.deleted || 0)} deleted${r.errors ? `, ${fmt(r.errors)} failed` : ""}.`
        : `Last run ${relTime(r.at)} didn't finish: ${r.message || "unknown error"}` }));
    }
  }

  // ---------- history ----------
  async function loadHistory() {
    const { page, q } = S.hist;
    try {
      const data = await api(`/api/history?offset=${page * PAGE}&limit=${PAGE}&q=${encodeURIComponent(q)}`);
      S.hist.total = data.total;
      const list = $("h-list");
      if (!data.rows.length) {
        list.replaceChildren(el("li", { class: "empty", text: q ? "Nothing in your history matches that." : "Nothing deleted yet. Everything you delete will be listed here." }));
      } else {
        list.replaceChildren(...data.rows.map(historyRow));
      }
      renderPager($("h-pager"), page, Math.max(1, Math.ceil(data.total / PAGE)), data.total,
        (p) => { S.hist.page = p; loadHistory(); });
    } catch (e) {
      banner("error", e.message);
    }
  }
  function historyRow(r) {
    const it = { kind: r.kind, date: r.created_at, text: r.text, handle: r.handle, likes: r.likes,
      reposts: r.reposts, replies: r.replies, media: r.media_types, link: r.has_link };
    const [top, text] = itemBody(it);
    top.append(el("span", { class: "hint", text: `deleted ${fmtDate(r.logged_at)}` }));
    if (r.outcome === "already_gone") top.append(el("span", { class: "badge", text: "was already gone" }));
    return el("li", { class: "item no-check" }, el("div", { class: "item-main" }, top, text));
  }
  async function download(format) {
    try {
      const res = await api(`/api/history/export?format=${format}`);
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const m = /filename="([^"]+)"/.exec(cd);
      const a = el("a", { href: URL.createObjectURL(blob), download: m ? m[1] : `history.${format}` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) {
      banner("error", e.message);
    }
  }

  // ---------- settings ----------
  async function renderSettings() {
    const st = S.status;
    const acct = $("s-account");
    acct.replaceChildren();
    if (st.demo) {
      acct.append(el("div", { class: "meta" }, el("b", { text: "Demo account" }), el("div", { class: "muted", text: "@you_demo · pretend data, nothing real is deleted" })));
    } else if (st.connected) {
      const me = st.me || {};
      if (me.profile_image_url) acct.append(el("img", { class: "avatar", src: me.profile_image_url, alt: "" }));
      acct.append(el("div", { class: "meta" }, el("b", { text: me.name || "Connected" }), el("div", { class: "muted", text: me.username ? `@${me.username}` : "" })),
        el("a", { class: "btn ghost small", href: "/login", text: "Reconnect" }),
        el("button", { type: "button", class: "btn ghost small", text: "Disconnect", onclick: disconnect }));
    } else {
      acct.append(el("div", { class: "meta muted", text: "Not connected." }), el("a", { class: "btn primary small", href: "/login", text: "Connect with X" }));
    }
    $("s-client-id").value = st.client_id || "";
    $("s-client-secret").value = "";
    $("s-client-secret").placeholder = st.has_secret ? "Saved (leave blank to keep it)" : "Only for “Web App” type apps";
    $("s-callback").textContent = st.redirect_uri;
    $("s-interval").value = st.min_interval;
    for (const n of $("s-app-form").querySelectorAll("input, button")) n.disabled = st.demo;
    $("s-data-dir").textContent = st.data_dir;
    $("s-version").textContent = `Tweetus Deletus ${st.version}`;
    loadKeep();
  }
  async function saveApp(ev) {
    ev.preventDefault();
    const json = { client_id: $("s-client-id").value.trim(), min_interval: $("s-interval").value };
    const secret = $("s-client-secret").value.trim();
    if (secret) json.client_secret = secret;
    const changed = json.client_id !== S.status.client_id;
    try {
      await api("/api/setup", { method: "POST", json });
      clearErrors();
      await refreshStatus();
      toast(changed ? "Saved. Connect your account to the new app." : "Saved.");
      if (changed) route(); else renderSettings();
    } catch (e) { banner("error", e.message); }
  }
  function disconnect() {
    openModal({
      title: "Disconnect your X account?",
      body: "Tweetus Deletus will forget your sign-in. Your history and settings stay.",
      ok: "Disconnect", onOk: async () => {
        try {
          await api("/api/logout", { method: "POST" });
          await refreshStatus();
          route();
        } catch (e) { banner("error", e.message); }
      } });
  }
  async function loadKeep() {
    try {
      const { keep } = await api("/api/keep");
      renderKeep(keep);
    } catch (e) { banner("error", e.message); }
  }
  function renderKeep(keep) {
    const ul = $("s-keep-list");
    if (!keep.length) {
      ul.replaceChildren(el("li", { class: "muted", text: "Nothing yet. Uncheck items in your results and tick “Always keep the items I unchecked”, or paste links here." }));
      return;
    }
    ul.replaceChildren(...[...keep].reverse().map((k) => el("li", {},
      el("code", { text: k.id }),
      el("span", { class: "grow", text: k.text || "" }),
      el("button", { type: "button", class: "link-btn", text: "Remove", onclick: async () => {
        try {
          const res = await api("/api/keep", { method: "POST", json: { remove: [k.id] } });
          renderKeep(res.keep);
          S.status.keep_count = res.keep.length;
          renderKeepHint();
        } catch (e) { banner("error", e.message); }
      } }))));
  }
  async function addKeep(ev) {
    ev.preventDefault();
    const v = $("s-keep-input").value.trim();
    if (!v) return;
    try {
      const res = await api("/api/keep", { method: "POST", json: { add: splitList(v.replace(/\s+/g, ",")) } });
      if (res.unrecognized && res.unrecognized.length) toast("That doesn't look like a post link or ID.");
      else toast(res.added ? "Added." : "Already on the list.");
      $("s-keep-input").value = "";
      renderKeep(res.keep);
      S.status.keep_count = res.keep.length;
      renderKeepHint();
    } catch (e) { banner("error", e.message); }
  }

  // ---------- misc ----------
  async function copyText(id) {
    const text = $(id).textContent;
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied.");
    } catch {
      const r = document.createRange();
      r.selectNodeContents($(id));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
      toast("Press Ctrl+C (or ⌘C) to copy.");
    }
  }

  function handleUrlParams() {
    const p = new URLSearchParams(location.search);
    if (p.get("connected")) S.justConnected = true;
    if (p.get("error")) banner("error", p.get("error"));
    if ([...p.keys()].length) history.replaceState(null, "", location.pathname + location.hash);
  }

  // ---------- wiring ----------
  function wire() {
    for (const b of $("tabs").querySelectorAll("button")) b.addEventListener("click", () => go(b.dataset.tab));
    for (const b of document.querySelectorAll("[data-goto]")) b.addEventListener("click", () => go(b.dataset.goto));
    for (const b of document.querySelectorAll("[data-copy]")) b.addEventListener("click", () => copyText(b.dataset.copy));
    for (const b of document.querySelectorAll("[data-quick]")) b.addEventListener("click", () => quickPick(b.dataset.quick));

    $("setup-form").addEventListener("submit", saveSetup);
    $("connect-btn").addEventListener("click", (e) => {
      if (!S.status.configured) { e.preventDefault(); toast("Save your Client ID first."); $("setup-client-id").focus(); }
    });

    for (const r of document.querySelectorAll('input[name="source"]')) {
      r.addEventListener("change", () => { setSource(r.value); saveForm(); });
    }
    const drop = $("archive-drop");
    drop.addEventListener("click", (e) => { if (e.target === drop || !e.target.closest("label")) $("archive-file").click(); });
    drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("archive-file").click(); } });
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("drag");
      uploadArchive(e.dataTransfer.files[0]);
    });
    $("archive-file").addEventListener("change", (e) => { uploadArchive(e.target.files[0]); e.target.value = ""; });

    $("f-when").addEventListener("change", () => { syncWhen(); saveForm(); });
    for (const b of $("f-media").querySelectorAll("button")) b.addEventListener("click", () => { setMedia(b.dataset.v); saveForm(); });
    for (const id of [...FORM_FIELDS, ...FORM_CHECKS]) $(id).addEventListener("change", saveForm);

    $("scan-btn").addEventListener("click", startScan);
    $("sp-stop").addEventListener("click", cancelJob);
    $("dp-stop").addEventListener("click", cancelJob);
    $("results-back").addEventListener("click", backToForm);
    let searchTimer;
    $("results-search").addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { S.q = e.target.value; S.page = 0; renderResultPage(); }, 150);
    });
    $("results-kind").addEventListener("change", (e) => { S.kind = e.target.value; S.page = 0; renderResultPage(); });
    $("sel-all").addEventListener("click", () => selectAll(true));
    $("sel-none").addEventListener("click", () => selectAll(false));
    $("delete-btn").addEventListener("click", confirmDelete);
    $("dd-again").addEventListener("click", backToForm);

    $("a-enabled").addEventListener("change", () => saveAuto(true));
    $("a-save").addEventListener("click", () => saveAuto(false));
    $("a-run").addEventListener("click", runAutoNow);

    let histTimer;
    $("h-search").addEventListener("input", (e) => {
      clearTimeout(histTimer);
      histTimer = setTimeout(() => { S.hist.q = e.target.value; S.hist.page = 0; loadHistory(); }, 200);
    });
    $("h-csv").addEventListener("click", () => download("csv"));
    $("h-json").addEventListener("click", () => download("json"));

    $("s-app-form").addEventListener("submit", saveApp);
    $("s-keep-form").addEventListener("submit", addKeep);

    $("modal-input").addEventListener("input", (e) => {
      $("modal-ok").disabled = e.target.value.trim().toUpperCase() !== (modalState && modalState.phrase);
    });
    $("modal-input").addEventListener("keydown", (e) => { if (e.key === "Enter") modalOk(); });
    $("modal-ok").addEventListener("click", modalOk);
    $("modal-cancel").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal").hidden) closeModal(); });

    window.addEventListener("hashchange", () => { if (S.status && ready()) go(location.hash.slice(1)); });
    setInterval(idleRefresh, 10000);
  }

  async function init() {
    buildTypeGrid();
    buildAutoTypes();
    restoreForm();
    wire();
    handleUrlParams();
    try {
      await refreshStatus();
    } catch (e) {
      $("view-loading").replaceChildren(el("p", { class: "muted", text: e.message }));
      return;
    }
    route();
    if (S.justConnected && S.status.me) toast(`Connected as @${S.status.me.username}`);
    const job = S.status.job;
    if (job) {
      S.job = job;
      if (job.state === "running") {
        attachJob(job);
      } else if (job.kind === "scan" && job.state === "done" && job.id === S.status.last_scan_id && ready()) {
        await loadResults(job.id); // came back after a refresh
      }
    }
  }

  init();
})();
