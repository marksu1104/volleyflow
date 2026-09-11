// Presses every clickable thing and measures how long until the screen
// admits it was pressed.
//
// smoke.js already asks whether a control does anything at all. This asks
// the harder question, the one reported as 「按了沒反應」and 「反應很慢」:
// does it answer *immediately*? A control may only wait on the network in
// silence for as long as it takes a person to notice — about 200ms — and
// after that it owes them one of three things:
//
//   * the change, drawn straight away (an optimistic action), or
//   * a busy state on the control itself (markBusy), or
//   * the page's 儲存中 marker (beginPendingWrite), for a change that is
//     already on screen and whose button no longer exists to mark.
//
// A control that sends no request and changes nothing was in the state it
// sets already — that is a correct no-op, not a finding.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/seed_dev.py
//     node tests/visual/feedback.js
//
// Destructive, like smoke.js: local seed data only, and re-seed after.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const AS = encodeURIComponent("蘇懂");
const PAGES = [
  "member.html",
  "organizer.html",
  "organizer-ledger.html",
  "organizer-settings.html",
  "profile.html",
  "organizer-members.html",
];

// Long enough not to be noticed, short enough to be the real bar. Below
// about 0.1s a change reads as instant and above about 0.2s as a wait,
// which is the whole distinction being tested.
const IMMEDIATE_MS = 200;

// Same reasoning as smoke.js: these throw away the data every later page
// needs, so pressing them ends the run rather than testing anything.
const SKIP = [/刪除/, /結算/, /登出/, /移出球隊/];

/** Everything on the page that counts as "it noticed the tap". */
const FEEDBACK = `(() => {
  const s = document.body.innerHTML;
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  const chip = document.getElementById("vf-pending");
  return {
    html: s.length + ":" + h,
    url: location.href,
    busy: document.querySelectorAll('[aria-busy="true"], .is-busy').length > 0,
    pending: !!(chip && chip.classList.contains("shown")),
  };
})()`;

async function settle(page) {
  await page
    .waitForFunction(() => document.querySelectorAll("button").length > 3, null, { timeout: 15000 })
    .catch(() => {});
  await page.waitForTimeout(1200);
}

/** Clicks one control and watches, frame by frame, for the first sign of
 * life. Polling rather than a MutationObserver because the busy state and
 * the 儲存中 marker are attribute and class changes on elements the
 * observer would have to be attached to in advance — and the repaint
 * replaces those elements. */
async function pressAndWatch(page, sel, index, traffic) {
  const before = await page.evaluate(FEEDBACK);
  const requestsBefore = traffic.requests;
  const dialogsBefore = traffic.dialogs;
  const started = Date.now();

  await page
    .evaluate(
      ([s, n]) => {
        const b = document.querySelectorAll(s)[n];
        if (b) b.click();
      },
      [sel, index]
    )
    .catch(() => {});

  let firstFeedback = null;
  for (let waited = 0; waited < 2500; waited += 25) {
    const now = await page.evaluate(FEEDBACK).catch(() => null);
    if (!now) {
      firstFeedback = Date.now() - started; // navigated away
      break;
    }
    const noticed =
      now.html !== before.html ||
      now.url !== before.url ||
      now.busy ||
      now.pending ||
      traffic.dialogs > dialogsBefore;
    if (noticed) {
      firstFeedback = Date.now() - started;
      break;
    }
    await page.waitForTimeout(25);
  }
  // Wait for the page to go quiet before pressing anything else. A fixed
  // pause is not enough: 移除 writes and then reloads the whole roster,
  // and pressing the next control while that is still in the air measures
  // a button that the reload is about to replace — which reported six
  // perfectly responsive 男/女 toggles as silent.
  await page.waitForTimeout(150);
  for (let waited = 0; waited < 6000 && traffic.inFlight > 0; waited += 50) {
    await page.waitForTimeout(50);
  }
  await page.waitForTimeout(250);

  return {
    firstFeedback,
    sentRequest: traffic.requests > requestsBefore,
    navigated: (await page.evaluate(() => location.href).catch(() => "gone")) !== before.url,
  };
}

async function auditPage(browser, path) {
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const traffic = { requests: 0, dialogs: 0, inFlight: 0 };
  const errors = [];
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 120)}`));
  page.on("request", (r) => {
    if (!r.url().includes(":8000")) return;
    traffic.requests += 1;
    traffic.inFlight += 1;
  });
  const finished = (r) => {
    if (r.url().includes(":8000")) traffic.inFlight = Math.max(0, traffic.inFlight - 1);
  };
  page.on("requestfinished", finished);
  page.on("requestfailed", finished);
  page.on("dialog", (d) => {
    traffic.dialogs += 1;
    d.accept("1");
  });

  await page.goto(`${BASE}/${path}?as=${AS}`, { waitUntil: "networkidle" });
  await settle(page);

  const rows = [];
  const sweep = async (only) => {
    const sel = only ? `${only} button` : "button";
    const total = await page.evaluate((s) => document.querySelectorAll(s).length, sel);
    for (let i = 0; i < total; i += 1) {
      const info = await page.evaluate(
        ([s, n]) => {
          const b = document.querySelectorAll(s)[n];
          if (!b) return null;
          const r = b.getBoundingClientRect();
          return {
            text: (b.textContent || "").trim().slice(0, 22),
            usable: r.height > 0 && r.width > 0 && !b.disabled,
          };
        },
        [sel, i]
      );
      if (!info || !info.usable) continue;
      if (SKIP.some((re) => re.test(info.text))) continue;

      const r = await pressAndWatch(page, sel, i, traffic);
      rows.push({ label: info.text || "(無標籤)", ...r });
      if (r.navigated) {
        await page.goto(`${BASE}/${path}?as=${AS}`, { waitUntil: "networkidle" });
        await settle(page);
      }
    }
  };

  await sweep(null);
  const hasSheet = await page.evaluate(() => {
    const b = document.getElementById("game-sheet-backdrop");
    if (!b) return false;
    if (b.hidden && typeof openGameSheet === "function") openGameSheet();
    return true;
  });
  if (hasSheet) {
    await page.waitForTimeout(600);
    await sweep("#game-detail");
  }

  await page.close();
  return { path, rows, errors: [...new Set(errors)] };
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  let problems = 0;
  for (const path of PAGES) {
    const r = await auditPage(browser, path).catch((e) => ({
      path,
      rows: [],
      errors: [`audit crashed: ${String(e).slice(0, 160)}`],
    }));

    // Silent for longer than a person will wait, having actually gone to
    // the network — the definition of a control that owes a loading state.
    const slow = r.rows.filter(
      (x) => x.sentRequest && (x.firstFeedback === null || x.firstFeedback > IMMEDIATE_MS)
    );
    const bad = slow.length || r.errors.length;
    if (bad) problems += 1;
    const instant = r.rows.filter((x) => x.firstFeedback !== null && x.firstFeedback <= IMMEDIATE_MS);
    console.log(
      `${bad ? "FAIL" : "ok  "} ${r.path} — 按了 ${r.rows.length} 個，` +
        `${instant.length} 個在 ${IMMEDIATE_MS}ms 內有反應`
    );
    for (const x of slow) {
      console.log(
        `       慢: ${x.label} — ${x.firstFeedback === null ? "完全沒反應" : x.firstFeedback + "ms 才有反應"}`
      );
    }
    for (const e of r.errors) console.log(`       ${e}`);
  }
  await browser.close();
  console.log(
    problems
      ? `\n${problems} 個頁面有按了沒有立即反應的控制項。`
      : "\n每一個按得到的東西都在 200ms 內給出反應：畫面直接改、按鈕轉圈，或出現「儲存中」。"
  );
  process.exit(problems ? 1 : 0);
})();
