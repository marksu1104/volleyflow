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

check("沒有元素超出畫面", (m) =>
  m.overflowing.length ? `超出邊界: ${m.overflowing.join(", ")}` : null);

check("所有輸入欄位至少 16px（不然 iOS 會放大）", (m) =>
  m.smallInputs.length ? `字級過小: ${m.smallInputs.join(", ")}` : null);

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
    return {
      rows: [...document.querySelectorAll(".att-row")].map((r) => ({
        cls: r.className, h: round(r.getBoundingClientRect().height),
      })),
      overflowing: [...document.querySelectorAll(".wrap *")]
        .filter((e) => e.getBoundingClientRect().right > wrap.right + 0.5)
        .map((e) => e.className || e.tagName),
      smallInputs: [...document.querySelectorAll("input, select, textarea")]
        .filter((e) => parseFloat(getComputedStyle(e).fontSize) < 16)
        .map((e) => e.className || e.tagName),
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
  console.log(`截圖: ${path.join(OUT, "game-detail.png")}`);
  process.exit(failed ? 1 : 0);
})();
