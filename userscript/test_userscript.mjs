// Offline tests for the free userscript. No network, no real X.
//   node userscript/test_userscript.mjs
// Needs Playwright's chromium (already present in this repo's dev setup).
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = fs.readFileSync(path.join(HERE, "tweetus-deletus.user.js"), "utf8");

let failures = 0;
const ok = (name, cond) => { console.log((cond ? "ok   " : "FAIL ") + name); if (!cond) failures++; };

// ---- 1. pure helper logic (no browser) ----
{
  const win = { __tdHelpersOnly: true };
  new Function("window", SCRIPT)(win);
  const H = win.__tdTest;
  ok("id from href", H.tweetIdFromHref("/jane/status/123") === "123");
  ok("id from non-status", H.tweetIdFromHref("/jane") === null);
  ok("classify own post", H.classifyMenu(["Delete", "Pin"]) === "delete");
  ok("classify 'Delete post'", H.classifyMenu(["Delete post"]) === "delete");
  ok("classify repost", H.classifyMenu(["Undo repost", "Share"]) === "unrepost");
  ok("classify foreign -> skip", H.classifyMenu(["Follow", "Block"]) === "skip");
  ok("keepwords hit", H.textHasAny("Our WEDDING day", ["wedding"]) === true);
  ok("keepwords miss", H.textHasAny("plain tweet", ["job"]) === false);
  ok("older than cutoff", H.isOlderThan("2019-01-01T00:00:00Z", Date.parse("2020-01-01")) === true);
  ok("newer than cutoff", H.isOlderThan(new Date().toISOString(), Date.now() - 86400000) === false);
  ok("undated kept when a cutoff is set", H.isOlderThan(null, Date.now()) === false);
  ok("no cutoff deletes any age", H.isOlderThan(null, 0) === true);
  let lo = 1e9, hi = 0;
  for (let i = 0; i < 5000; i++) { const g = H.gapMs(35, 15); lo = Math.min(lo, g); hi = Math.max(hi, g); }
  ok("gap stays in [20s,50s]", lo >= 20000 && hi <= 50000);
  ok("gap never below 2s", H.gapMs(0.1, 10, () => 0) >= 2000);
}

// ---- 2. real DOM flow in headless chromium against a mock profile ----
let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  // Fall back to a globally-installed playwright (resolve via NODE_PATH/npm root).
  try {
    const { createRequire } = await import("node:module");
    const { execSync } = await import("node:child_process");
    const req = createRequire(import.meta.url);
    const roots = [process.env.NODE_PATH, execSync("npm root -g").toString().trim()].filter(Boolean);
    const found = roots.map((r) => path.join(r, "playwright")).find((p) => fs.existsSync(p));
    if (found) ({ chromium } = req(found));  // require() resolves playwright's own deps
  } catch { /* still not found */ }
}
if (!chromium) {
  console.log("\n(skipping browser test: playwright not installed)");
  process.exit(failures ? 1 : 0);
}

const day = 86400000, now = Date.now();
// id, text, ageDays, pinned, isRepost
const DATA = [
  ["101", "pinned important", 400, true, false],
  ["102", "old crypto take", 500, false, false],
  ["103", "our wedding post", 600, false, false],
  ["104", "a recent thought", 2, false, false],
  ["105", "RT friend", 800, false, true],
  ["106", "another old one", 700, false, false],
];
function pageHtml() {
  const arts = DATA.map(([id, text, age, pinned, repost]) => {
    const dt = new Date(now - age * day).toISOString();
    const menu = repost ? ["Undo repost", "Copy link"] : ["Delete", "Pin to your profile"];
    return `<article data-testid="tweet" data-id="${id}">
      ${pinned ? '<div data-testid="socialContext">Pinned</div>' : ""}
      <div data-testid="tweetText">${text}</div>
      <a href="/testuser/status/${id}"><time datetime="${dt}">t</time></a>
      <button data-testid="caret" onclick='openMenu("${id}", ${JSON.stringify(menu)})'>...</button>
    </article>`;
  }).join("");
  return `<!doctype html><meta charset=utf8><body><h1>testuser</h1><div id=feed>${arts}</div><div id=layers></div>
  <script>
    window.openMenu=function(id,items){close_();var m=document.createElement('div');
      m.setAttribute('data-testid','Dropdown');m.id='live-menu';
      items.forEach(function(t){var it=document.createElement('div');it.setAttribute('role','menuitem');
        it.textContent=t;it.onclick=function(){pick(id,t)};m.appendChild(it)});
      document.getElementById('layers').appendChild(m)};
    function close_(){var e=document.getElementById('live-menu');if(e)e.remove()}
    document.addEventListener('keydown',function(e){if(e.key==='Escape')close_()});
    function pick(id,t){close_();if(t==='Delete'||t==='Undo repost'){
      var s=document.createElement('div');var b=document.createElement('button');
      b.setAttribute('data-testid','confirmationSheetConfirm');b.textContent='Confirm';
      b.onclick=function(){var a=document.querySelector('article[data-id="'+id+'"]');if(a)a.remove();
        s.remove();window.__deleted=(window.__deleted||[]);window.__deleted.push(id)};
      s.appendChild(b);document.getElementById('layers').appendChild(s)}}
  </script></body>`;
}

const server = http.createServer((req, res) => {
  // Serve the same mock page at any path, so location.pathname can vary.
  res.setHeader("Content-Type", "text/html");
  res.end(pageHtml());
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;

const browser = await chromium.launch();
const problems = [];
try {
  const p = await browser.newPage();
  p.on("pageerror", (e) => problems.push(e.message));
  p.on("dialog", (d) => d.accept());
  await p.goto(`http://127.0.0.1:${port}/testuser`);
  await p.evaluate(() => {
    localStorage.setItem("tweetus-deletus", JSON.stringify(
      { pace: "normal", keepPinned: true, olderThanDays: "365", keepWords: "wedding" }));
    window.__tdPace = { base: 0.05, jitter: 0, restEvery: 9999, restSec: 0 };
  });
  await p.addScriptTag({ content: SCRIPT });
  await p.waitForSelector("#tweetus-deletus-panel");
  await p.click("#tdp-start");                       // arms the two-tap confirm
  ok("Start arms a confirm step", /once more/i.test(await p.$eval("#tdp-status", (e) => e.textContent)));
  await p.click("#tdp-start");                       // confirms -> begins
  await p.waitForFunction(
    () => /All done|Stopped|changed/.test(document.querySelector("#tdp-status").textContent),
    null, { timeout: 70000 });
  const deleted = (await p.evaluate(() => window.__deleted || [])).sort();
  const remaining = (await p.$$eval("article[data-testid=tweet]", (a) => a.map((x) => x.dataset.id))).sort();
  const err = await p.$eval("#tdp-err", (e) => e.textContent);
  ok("deleted the two old posts and undid the old repost",
    JSON.stringify(deleted) === JSON.stringify(["102", "105", "106"]));
  ok("kept pinned + keyword + recent",
    JSON.stringify(remaining) === JSON.stringify(["101", "103", "104"]));
  ok("no errors", err === "0");
  ok("no page exceptions", problems.length === 0);

  // Profile gate: on a non-profile page, Start must refuse; on a profile it
  // must arm. (The user's bug was Start doing nothing on their profile.)
  const gate = await browser.newPage();
  gate.on("dialog", (d) => d.accept());
  await gate.goto(`http://127.0.0.1:${port}/home`);
  await gate.addScriptTag({ content: SCRIPT });
  await gate.waitForSelector("#tdp-start");
  await gate.click("#tdp-start");
  ok("Start refuses on a non-profile page (/home)",
    /open your profile/i.test(await gate.$eval("#tdp-status", (e) => e.textContent)));
  const prof = await browser.newPage();
  prof.on("dialog", (d) => d.accept());
  await prof.goto(`http://127.0.0.1:${port}/hanibanhi`);
  await prof.addScriptTag({ content: SCRIPT });
  await prof.waitForSelector("#tdp-start");
  await prof.click("#tdp-start");
  ok("Start arms on a real profile path (/hanibanhi)",
    /once more/i.test(await prof.$eval("#tdp-status", (e) => e.textContent)));
  await prof.goto(`http://127.0.0.1:${port}/hanibanhi/with_replies`);
  await prof.addScriptTag({ content: SCRIPT });
  await prof.waitForSelector("#tdp-start");
  await prof.click("#tdp-start");
  ok("Start arms on the Replies tab too",
    /once more/i.test(await prof.$eval("#tdp-status", (e) => e.textContent)));

  // The panel must still appear under enforced Trusted Types + a CSP that
  // blocks inline <style> — which is what x.com does and what hid the panel
  // before. addScriptTag runs in the page's main world, so this is strict.
  const tt = await browser.newPage();
  const ttErrs = [];
  tt.on("pageerror", (e) => ttErrs.push(e.message));
  await tt.setContent(
    `<!doctype html><meta http-equiv="Content-Security-Policy" ` +
    `content="require-trusted-types-for 'script'; style-src 'self'"><body><h1>x</h1></body>`);
  // evaluate() runs the code in the page's main world (unlike addScriptTag it
  // doesn't use the TT-blocked script.text sink), so the script's own DOM
  // building is what's exercised against Trusted Types.
  await tt.evaluate(SCRIPT).catch((e) => ttErrs.push(String(e)));
  const built = await tt.waitForSelector("#tweetus-deletus-panel", { timeout: 5000 }).then(() => true).catch(() => false);
  ok("panel appears under Trusted Types + strict CSP", built);
  ok("Start button is present there", await tt.$("#tdp-start") !== null);
  ok("no Trusted-Types exception", !ttErrs.some((m) => /trusted|innerHTML/i.test(m)));
} finally {
  await browser.close();
  server.close();
}

console.log(failures ? `\n${failures} FAILED` : "\nall userscript tests passed");
process.exit(failures ? 1 : 0);
