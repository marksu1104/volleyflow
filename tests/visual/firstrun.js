// What the app looks like to somebody who has just opened it, on a
// database with nothing in it.
//
// This is the state every real user meets once and nobody ever sees
// again, which is exactly why it rots. It went unnoticed until the
// database was emptied for launch (2026-09-13): 帳務 and 名單 both said
// 「這個球隊尚未開季」 and offered to copy an invite link — for a club
// that did not exist. Every other check here runs against seed data, so
// none of them could have caught it.
//
// What it asserts, per page:
//   * it says there is no club yet, and not something else;
//   * it is not still saying 載入中 — an empty state that never resolves
//     is indistinguishable from a broken page;
//   * nothing threw, and nothing 500'd;
//   * no control is offered that acts on a club, because there isn't one.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/reset_db.py --yes    # empties the dev branch
//     node tests/visual/firstrun.js
//     uv run python scripts/seed_dev.py          # put the data back
//
// It needs an EMPTY database, unlike every other check here, and it says
// so rather than quietly passing: a seeded database makes every page
// show a club, and the assertions below would pass for the wrong reason.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const AS = encodeURIComponent("全新使用者");
const PAGES = [
  "member.html",
  "organizer.html",
  "organizer-ledger.html",
  "organizer-members.html",
  "organizer-settings.html",
];

// Controls that only mean something once a club exists. The app bar and
// the nav are not among them: those are how you leave.
const NEEDS_A_CLUB = [/複製/, /建立第一季/, /結算/, /新增/, /產生場次/, /下一步/];

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });

  for (const path of PAGES) {
    const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
    const problems = [];
    page.on("pageerror", (e) => problems.push(`JS: ${String(e).slice(0, 120)}`));
    page.on("response", (r) => {
      if (r.url().includes(":8000") && r.status() >= 500) {
        problems.push(`${r.status()} ${r.url().replace(/^.*:8000/, "")}`);
      }
    });

    await page.goto(`${BASE}/${path}?as=${AS}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(4500);

    const seen = await page.evaluate(() => {
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      return {
        text: document.body.innerText.replace(/\s+/g, " ").trim(),
        controls: [...document.querySelectorAll("button, .empty-state a")]
          .filter(visible)
          .map((b) => b.textContent.trim()),
      };
    });

    if (!/尚未加入任何球隊/.test(seen.text)) {
      problems.push(`沒有說「尚未加入任何球隊」，而是說: ${seen.text.slice(0, 90)}`);
    }
    if (/載入中/.test(seen.text)) problems.push("還卡在載入中");
    const offered = seen.controls.filter((c) => NEEDS_A_CLUB.some((re) => re.test(c)));
    if (offered.length) problems.push(`還提供了需要球隊才有意義的控制項: ${offered.join(", ")}`);

    report(path, problems);
    await page.close();
  }

  await browser.close();
  console.log(
    failed
      ? `\n${failed} 個頁面在空資料庫下說錯話。`
      : "\n空資料庫下每一頁都說得出「還沒有球隊」，而且只給得出離開的路。"
  );
  process.exit(failed ? 1 : 0);
})();
