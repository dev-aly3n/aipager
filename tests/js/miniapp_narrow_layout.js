// Narrow-phone layout check for the Mini App's Sessions view, in a real
// headless Chromium (playwright-core), every language at 320/360/390 px.
//   node miniapp_narrow_layout.js <config.json>
// The page is served by request interception only (no socket, no network):
// "/" is the page GET / serves, /api/* the bodies in the config, /fonts/*
// the bundled font files, and a stub stands in for Telegram's WebApp script.
// Each run measures, from the outside, what a person would see go wrong:
//   - the page scrolls sideways (documentElement wider than the viewport);
//   - text clipped inside its own box (scrollWidth past clientWidth on an
//     element that hides its overflow), except the few one-line ellipsis
//     fields whose full text is shown elsewhere (CFG.ellipsis_ok), and
//     never when the text is the page's own words (each language's
//     never_cut); a line squeezed shorter than its text counts too;
//   - a word broken across two lines;
//   - a piece of a tile, tab button, beacon or the header row drawn past
//     that container's edge (cut off, or spilling out).
// Prints one JSON line: {runs: [{lang, width, scheme, problems}], errors}.
// SHOTS_DIR set: a full-page PNG per run, named <lang>-<width>-<scheme>.png.
const fs = require("fs");
const path = require("path");
const CFG = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

function loadPlaywright() {
  const tries = [process.env.AIPAGER_PLAYWRIGHT_CORE, "playwright-core", "playwright"];
  for (const t of tries) {
    if (!t) continue;
    try { return require(t); } catch (e) { /* next */ }
  }
  return null;
}

const DARK = {
  "bg-color": "#17212b", "secondary-bg-color": "#232e3c", "section-bg-color": "#17212b",
  "text-color": "#f5f5f5", "hint-color": "#708499", "button-color": "#5288c1",
  "button-text-color": "#ffffff", "destructive-text-color": "#ec3942",
  "section-separator-color": "#111921",
};
const LIGHT = {
  "bg-color": "#ffffff", "secondary-bg-color": "#f1f1f1", "section-bg-color": "#ffffff",
  "text-color": "#000000", "hint-color": "#999999", "button-color": "#2481cc",
  "button-text-color": "#ffffff", "destructive-text-color": "#cc2929",
  "section-separator-color": "#e7e7e7",
};

function stub(scheme) {
  const vars = scheme === "dark" ? DARK : LIGHT;
  return `(function(){var r=document.documentElement;var v=${JSON.stringify(vars)};
  for (var k in v) { r.style.setProperty("--tg-theme-" + k, v[k]); }
  window.Telegram = {WebApp: {initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  initDataUnsafe: {}, version: "8.0", platform: "android", colorScheme: ${JSON.stringify(scheme)},
  themeParams: {}, isExpanded: true, viewportHeight: 800, ready(){}, expand(){}, close(){},
  onEvent(){}, offEvent(){}, isVersionAtLeast(){ return true; }, setHeaderColor(){},
  setBackgroundColor(){}, enableVerticalSwipes(){}, disableVerticalSwipes(){},
  showConfirm(m, cb){ cb && cb(true); }, showAlert(m, cb){ cb && cb(); },
  BackButton: {show(){}, hide(){}, onClick(){}, offClick(){}},
  MainButton: {show(){}, hide(){}, setText(){}, onClick(){}, offClick(){}, setParams(){}},
  HapticFeedback: {impactOccurred(){}, notificationOccurred(){}, selectionChanged(){}}}};})();`;
}

// Runs in the page: every problem a person would see, as short strings.
function measure([ellipsisOk, neverCut]) {
  const out = [];
  const cut = [];
  const vw = window.innerWidth;
  const de = document.documentElement;
  if (de.scrollWidth > vw + 0.5) out.push("page scrolls sideways: " + de.scrollWidth + " > " + vw);
  const view = document.getElementById("view-grid");
  const scopes = [document.querySelector(".app-head"), document.getElementById("tabbar"), view];
  const name = (el) => {
    const c = typeof el.className === "string" ? el.className.trim().split(/\s+/)[0] : "";
    return (el.id ? "#" + el.id : "") + (c ? "." + c : el.tagName.toLowerCase());
  };
  const visible = (el) => {
    if (el.closest("[hidden]")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  for (const scope of scopes) {
    if (!scope) continue;
    for (const el of scope.querySelectorAll("*")) {
      // Not "visible": a line squeezed to no height is exactly what to catch.
      if (el.closest("[hidden]") || !el.checkVisibility() || el.closest("svg")) continue;
      const cs = getComputedStyle(el);
      const texts = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
      const own = el.textContent.trim();
      const hidesY = cs.overflowY === "hidden" || cs.overflowY === "clip";
      // A pixel or two is a tall font's ascent (CJK), not a lost line.
      if (texts.length && hidesY && el.scrollHeight > el.clientHeight + 3) {
        out.push("text taller than its box " + name(el) + " " + JSON.stringify(own) +
                 " " + el.scrollHeight + ">" + el.clientHeight);
      }
      // Text wider than its own box: cut when the box hides it, spilling
      // out of it (and then cut by the tile) when it does not. The page's
      // own words (neverCut) are never an allowed ellipsis.
      const overflows = cs.display !== "inline" && texts.length &&
        el.scrollWidth > el.clientWidth + 0.5;
      if (overflows && !neverCut.includes(own) && ellipsisOk.some((sel) => el.matches(sel))) {
        cut.push(name(el) + " " + JSON.stringify(el.textContent.trim()) + " " +
                 el.clientWidth + "/" + el.scrollWidth);
      } else if (overflows) {
        out.push("text wider than its box " + name(el) + " " + JSON.stringify(el.textContent.trim()) +
                 " " + el.scrollWidth + ">" + el.clientWidth);
      }
      // A word broken across two lines (a column too narrow for it). Han
      // text breaks between characters by design; a tab label may hyphenate
      // (ru "Использо-вание"), except the one a count sits beside.
      const badged = el.matches(".tabbar-btn:has(> .badge:not([hidden])) .tabbar-label");
      if ((scope === view || badged) && cs.whiteSpace !== "nowrap") {
        for (const t of texts) {
          const re = /\S+/g;
          let m;
          while ((m = re.exec(t.textContent))) {
            if (/[\u3000-\u9fff]/.test(m[0])) continue;
            const r = document.createRange();
            r.setStart(t, m.index);
            r.setEnd(t, m.index + m[0].length);
            const tops = new Set([...r.getClientRects()].map((q) => Math.round(q.top)));
            if (tops.size > 1) out.push("word broken " + name(el) + " " + JSON.stringify(m[0]));
          }
        }
      }
    }
  }
  // Every piece of a container stays inside it.
  const boxes = document.querySelectorAll(
    "#view-grid .tile, .tabbar-btn, #view-grid .beacon, .app-head, #view-grid .pulse-row");
  for (const box of boxes) {
    if (!visible(box)) continue;
    const b = box.getBoundingClientRect();
    for (const el of box.querySelectorAll("*")) {
      if (!visible(el) || el.closest("svg") && el.tagName.toLowerCase() !== "svg") continue;
      if (getComputedStyle(el).position === "absolute") continue;   // decoration
      const r = el.getBoundingClientRect();
      // An inline box may stand taller than its line (a CJK font's ascent):
      // only a block is held to the container's top and bottom.
      const inline = getComputedStyle(el).display === "inline";
      if (r.left < b.left - 0.5 || r.right > b.right + 0.5 ||
          (!inline && (r.top < b.top - 0.5 || r.bottom > b.bottom + 0.5))) {
        out.push("outside " + name(box) + ": " + name(el) + " " +
                 JSON.stringify(el.textContent.trim().slice(0, 40)) +
                 ` [${Math.round(r.left)},${Math.round(r.right)}] in [${Math.round(b.left)},${Math.round(b.right)}]`);
      }
    }
  }
  // Neighbours in a row never draw over each other (a pill over the ring).
  const rows = document.querySelectorAll(
    "#view-grid .tile-top, #view-grid .tile-side, #view-grid .beacon-top, #view-grid .beacon-body, " +
    "#view-grid .beacon-actions, #view-grid .pulse-row, .tabbar, .tabbar-btn, .app-head");
  for (const row of rows) {
    if (!visible(row)) continue;
    const kids = [...row.children].filter((k) => visible(k) &&
      getComputedStyle(k).position !== "absolute");
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i].getBoundingClientRect(), c = kids[j].getBoundingClientRect();
        const w = Math.min(a.right, c.right) - Math.max(a.left, c.left);
        const h = Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top);
        if (w > 0.5 && h > 0.5) {
          out.push("overlap in " + name(row) + ": " + name(kids[i]) + " / " + name(kids[j]));
        }
      }
    }
    // A child hanging past a row that shrank below its content.
    const b = row.getBoundingClientRect();
    for (const k of kids) {
      const r = k.getBoundingClientRect();
      if (r.left < b.left - 0.5 || r.right > b.right + 0.5) {
        out.push("past " + name(row) + ": " + name(k) + " " + JSON.stringify(k.textContent.trim().slice(0, 40)));
      }
    }
  }
  // The ring's number sits inside the ring's hole.
  for (const lamp of document.querySelectorAll("#view-grid .lamp")) {
    const numEl = lamp.querySelector(".lamp-num");
    if (!numEl || !visible(numEl)) continue;
    const ring = parseFloat(getComputedStyle(lamp).getPropertyValue("--ring")) || 0;
    const hole = lamp.getBoundingClientRect().width - 2 * ring;
    const w = numEl.getBoundingClientRect().width;
    if (w > hole + 0.5) out.push("ring number wider than its ring: " + JSON.stringify(numEl.textContent) + " " + w.toFixed(1) + ">" + hole);
  }
  return { problems: out, cut };
}

(async () => {
  const pw = loadPlaywright();
  if (!pw) { console.log(JSON.stringify({ skip: "playwright-core not found" })); return; }
  const result = { runs: [], errors: [] };
  const launch = { args: ["--no-sandbox", "--disable-gpu"] };
  if (process.env.AIPAGER_CHROMIUM) launch.executablePath = process.env.AIPAGER_CHROMIUM;
  let browser;
  try { browser = await pw.chromium.launch(launch); } catch (e) {
    console.log(JSON.stringify({ skip: "chromium would not start: " + String(e).slice(0, 300) }));
    return;
  }
  const shots = process.env.SHOTS_DIR || "";
  try {
    for (const L of CFG.langs) {
      for (const scheme of CFG.schemes) {
        const ctx = await browser.newContext({ viewport: { width: CFG.widths[0], height: 900 },
                                              deviceScaleFactor: shots ? 2 : 1 });
        const page = await ctx.newPage();
        page.on("pageerror", (e) => result.errors.push(L.code + ": " + String(e)));
        await page.route("**/*", (route) => {
          const u = new URL(route.request().url());
          if (u.host !== "miniapp.test") return route.abort();
          const p = u.pathname;
          if (p === "/") return route.fulfill({ contentType: "text/html; charset=utf-8", body: L.page });
          if (p === "/telegram-web-app.js")
            return route.fulfill({ contentType: "application/javascript", body: stub(scheme) });
          if (p.startsWith("/fonts/")) {
            const f = path.join(CFG.fonts_dir, path.basename(p));
            if (!fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
            return route.fulfill({ contentType: "font/woff2", body: fs.readFileSync(f) });
          }
          if (p.startsWith("/api/i18n/")) return route.fulfill({ json: L.catalog || {} });
          if (p === "/api/sessions") return route.fulfill({ json: Object.assign({}, CFG.sessions, L.ui ? { ui: L.ui } : {}) });
          if (p === "/api/chats") return route.fulfill({ json: Object.assign({ chats: [], current: "" }, L.ui ? { ui: L.ui } : {}) });
          return route.fulfill({ json: {} });
        });
        await page.goto("http://miniapp.test/", { waitUntil: "domcontentloaded" });
        await page.waitForSelector("#sessions .tile-busy", { timeout: 20000 });
        await page.waitForFunction((c) => document.documentElement.lang === c, L.code, { timeout: 20000 });
        await page.waitForFunction(() => !document.getElementById("maintab-usage").hidden, null, { timeout: 20000 });
        for (const width of CFG.widths) {
          await page.setViewportSize({ width, height: 900 });
          await page.evaluate(() => document.fonts.ready);
          // No enter animation mid-measure.
          await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
          await page.waitForTimeout(CFG.settle_ms || 400);
          const m = await page.evaluate(measure, [CFG.ellipsis_ok, L.never_cut || []]);
          result.runs.push({ lang: L.code, width, scheme, problems: m.problems, cut: m.cut });
          if (shots) {
            await page.screenshot({ path: path.join(shots, `${L.code}-${width}-${scheme}.png`), fullPage: true });
          }
        }
        await ctx.close();
      }
    }
  } catch (e) {
    result.errors.push("driver: " + (e && e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(result));
})();
