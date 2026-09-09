// Measures the pages a phone actually renders. See README.md.
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");

const FRONTEND = path.join(__dirname, "..", "..", "frontend");
const OUT = path.join(__dirname, "shots");

const SEASON = {
  id: 1, capacity: 18, minimum_roster: 12, share_per_game: "205", ac_surcharge: "540",
  game_start_time: "18:30:00", game_end_time: "22:00:00", location: "啪排郎",
  change_deadline_days: 1,
  members: Array.from({ length: 18 }, (_, i) => ({
    id: i + 1, name: i === 0 ? "蘇懂" : "成員" + (i + 1),
    gender: i < 12 ? "male" : "female", linked: i % 3 !== 0,
  })),
};

const GAME = {
  id: 10, date: "2026-10-06", status: "scheduled", locked: false,
  air_conditioned: true, share: "235",
  absences: [{ id: 100, player_name: "成員2", covered_by: "Taco" }],
  confirmed_drop_ins: [
    { id: 200, player_id: 90, player_name: "Taco", gender: "male", covering: "成員2", linked: false },
    { id: 201, player_id: 91, player_name: "WenChiao +1 女", gender: "female", covering: null, linked: false },
  ],
  waitlist_entries: [{ id: 300, player_name: "測試", gender: "male" }],
};

const CHECKS = [];
const check = (name, fn) => CHECKS.push({ name, fn });

check("每一列都一樣高", (m) => {
  const heights = new Set(m.rows.map((r) => r.h));
  return heights.size === 1 ? null : `列高不一致: ${[...heights].join(", ")}`;
});

check("每一個分頁都有畫出東西", (m) => {
  const empty = m.tabs.filter((t) => t.height < 20).map((t) => t.key);
  return empty.length ? `分頁沒有內容: ${empty.join(", ")}` : null;
});

check("分頁按鈕夠大（手指點得到，至少 40px）", (m) => {
  const small = m.tabTargets.filter((h) => h < 40);
  return small.length ? `分頁高度過小: ${small.join(", ")}` : null;
});

check("沒有元素超出畫面", (m) =>
  m.overflowing.length ? `超出邊界: ${m.overflowing.join(", ")}` : null);

check("所有輸入欄位至少 16px（不然 iOS 會放大）", (m) =>
  m.smallInputs.length ? `字級過小: ${m.smallInputs.join(", ")}` : null);

check("可點擊的東西至少 32px 高（手指按得到）", (m) => {
  const small = m.tapTargets.filter((t) => t.h < 32);
  if (!small.length) return null;
  const worst = [...new Set(small.map((t) => `${t.what} ${t.h}px`))];
  return `目標過小: ${worst.join(", ")}`;
});

check("相鄰按鈕的感應區沒有重疊（不會誤觸）", (m) => {
  const clashes = [];
  for (const row of m.tapRows) {
    for (let i = 1; i < row.length; i += 1) {
      if (row[i].left < row[i - 1].right) {
        clashes.push(`${row[i - 1].what} / ${row[i].what}`);
      }
    }
  }
  return clashes.length ? `感應區重疊: ${[...new Set(clashes)].join(", ")}` : null;
});

(async () => {
  let measured;
  const browser = await chromium.launch({ channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 390, height: 900 }, deviceScaleFactor: 2 });

  // Written into frontend/ and opened from there rather than injected
  // with setContent: that page's base URL is about:blank, so its
  // relative — and file:// — script tags never load.
  const scratch = path.join(FRONTEND, "__visual-check.html");
  fs.writeFileSync(
    scratch,
    `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="shared.css">
     <div class="wrap" style="max-width:390px"><div id="out"></div></div>
     <script src="shared.js"></script>`,
    "utf-8"
  );
  try {
    await page.goto("file://" + scratch.split(path.sep).join("/"));
    await page.waitForFunction(() => typeof renderGameDetail === "function");
  await page.evaluate(
    ([season, game]) => {
      renderGameDetail(document.getElementById("out"), season, game, {
        viewerName: "蘇懂", clubMembers: [],
        onRecordAbsence() {}, onCancelAbsence() {}, onRemoveDropIn() {},
        onLeaveWaitlist() {}, onAssignSubstitute() {}, onCancelSubstitute() {},
        onAddDropIn() {},
      });
    },
    [SEASON, GAME]
  );

    measured = await page.evaluate(() => {
    const wrap = document.querySelector(".wrap").getBoundingClientRect();
    const round = (n) => Math.round(n * 100) / 100;
    const out = document.getElementById("out");

    // The three groups are tabs now, so only one panel is on screen at
    // a time — measuring them all at once would report the hidden ones
    // as zero-height and call the rows inconsistent. Each tab is shown
    // in turn and measured while it is actually visible, which also
    // proves every tab renders at all.
    const rows = [];
    const tabs = [];
    for (const key of ["attending", "absent", "queued"]) {
      showGameDetailTab(out, key);
      const panel = out.querySelector(`[data-gd-panel="${key}"]`);
      tabs.push({ key, visible: !panel.hidden, height: round(panel.getBoundingClientRect().height) });
      for (const r of panel.querySelectorAll(".att-row")) {
        rows.push({ tab: key, cls: r.className, h: round(r.getBoundingClientRect().height) });
      }
    }
    showGameDetailTab(out, "attending");

    return {
      rows,
      tabs,
      tabTargets: [...document.querySelectorAll(".gd-tab")].map((t) =>
        round(t.getBoundingClientRect().height)
      ),
      overflowing: [...document.querySelectorAll(".wrap *")]
        .filter((e) => !e.closest("[hidden]"))
        .filter((e) => e.getBoundingClientRect().right > wrap.right + 0.5)
        .map((e) => e.className || e.tagName),
      // input/select/textarea only: iOS zooms the page when a *text
      // field* under 16px takes focus. A small button doesn't do that —
      // its problem is being hard to hit, which is measured separately.
      smallInputs: [...document.querySelectorAll("input, select, textarea")]
        .filter((e) => parseFloat(getComputedStyle(e).fontSize) < 16)
        .map((e) => e.className || e.tagName),
      // Every tappable thing on screen, by height. Apple asks for 44pt,
      // Google for 48dp; under about 32 is a genuine miss on a phone.
      tapTargets: [...document.querySelectorAll("button, a")]
        .filter((e) => !e.closest("[hidden]") && e.getBoundingClientRect().height > 0)
        .map((e) => ({
          what: e.className || e.tagName,
          h: round(e.getBoundingClientRect().height),
          w: round(e.getBoundingClientRect().width),
        })),
      // Growing a hit area past its neighbour is worse than leaving it
      // small: 遞補 sits beside 移除, and a stray tap removes somebody.
      tapRows: [...document.querySelectorAll(".roster-note")].map((note) =>
        [...note.querySelectorAll("button")]
          .map((b) => {
            const r = b.getBoundingClientRect();
            return { what: b.className, left: round(r.left), right: round(r.right) };
          })
          .sort((a, b) => a.left - b.left)
      ),
    };
  });

    fs.mkdirSync(OUT, { recursive: true });
    await page.screenshot({ path: path.join(OUT, "game-detail.png"), fullPage: true });
  } finally {
    fs.rmSync(scratch, { force: true });
    await browser.close();
  }

  let failed = 0;
  for (const { name, fn } of CHECKS) {
    const problem = fn(measured);
    console.log((problem ? "FAIL " : "ok   ") + name + (problem ? " — " + problem : ""));
    if (problem) failed += 1;
  }
  console.log(`\n列高: ${measured.rows.map((r) => r.h).join(", ")}`);
  console.log(`分頁: ${measured.tabs.map((t) => `${t.key} ${t.height}px`).join(" · ")}`);
  console.log(`截圖: ${path.join(OUT, "game-detail.png")}`);
  process.exit(failed ? 1 : 0);
})();
