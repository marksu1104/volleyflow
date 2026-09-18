// Choosing a club before being shown one.
//
// Change C of the 2026-09-18 menu redesign, approved with A, B and D and
// then missed — the organizer found it, not the developer, which is the
// reason this check exists at all.
//
// The rule has two halves and they fail in opposite directions, neither
// of which raises an error:
//
//   - more than one club  -> ask first, or somebody with three clubs is
//                            dropped into whichever they looked at last
//   - exactly one club,
//     or ?club= given     -> do not ask, or every open costs a tap whose
//                            answer was never in doubt
//
// The ?club= half is not hypothetical. The money page's 前往這個球隊
// button writes the club and navigates here; without the parameter the
// picker would open and ask which club to look at, immediately after the
// reader picked one.
//
// Needs two clubs on the dev database. seed_dev.py builds one, so a
// second was created by hand (週三晚場（多隊測試）) and is deliberately
// left in place — without it this check cannot run at all.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";

const auth = {
  Authorization: `Bearer dev:${encodeURIComponent(WHO)}`,
  "Content-Type": "application/json",
};

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  if (!Array.isArray(clubs) || clubs.length < 2) {
    console.log("FAIL 這個檢查需要兩個以上的球隊，目前只有 " + (clubs.length || 0) + " 個。");
    process.exit(1);
  }

  const browser = await chromium.launch({ channel: "msedge" });
  const errors = [];
  try {
    // --- more than one club: ask ---
    let ctx = await browser.newContext({ viewport: { width: 390, height: 900 } });
    let page = await ctx.newPage();
    page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 140)}`);
    });

    await page.goto(`${BASE}/member.html?as=${encodeURIComponent(WHO)}`);
    let asked = true;
    try {
      await page.waitForSelector("[data-pick-club]", { timeout: 25000 });
    } catch (e) {
      asked = false;
    }
    report("有兩隊以上就先問要看哪一隊", asked ? [] : ["沒有出現選擇畫面"]);

    if (asked) {
      // The spinner has to actually be a circle. It rendered as a
      // zero-width sliver until 2026-09-19, because .spinner set width
      // and height but no `display`, and an <i> is inline — where both
      // are ignored. Measuring the box is the only way to catch that:
      // the element is present and carries the right class either way,
      // so every existence check passed while it looked broken.
      const box = await page
        .$eval("[id^=pick-when-] .spinner", (el) => {
          const r = el.getBoundingClientRect();
          return { w: Math.round(r.width), h: Math.round(r.height) };
        })
        .catch(() => null);
      report("轉圈是圓的，不是一條線", [
        ...(box ? [] : ["找不到轉圈"]),
        ...(box && box.w >= 10 && box.h >= 10
          ? []
          : [`尺寸不對，應該接近 15x15：${JSON.stringify(box)}`]),
      ]);

      // The sub-line is the reason for choosing rather than a decoration:
      // it must actually arrive, not sit on its spinner.
      await page.waitForTimeout(4000);
      const subs = await page.$$eval("[id^=pick-when-]", (es) =>
        es.map((e) => e.textContent.trim())
      );
      const spinning = await page.$$eval("[id^=pick-when-] .spinner", (s) => s.length);
      report("每一隊都說得出下一場是什麼時候", [
        ...(spinning === 0 ? [] : [`${spinning} 個還在轉圈`]),
        ...(subs.every((s) => s.length > 0) ? [] : [`有空白的：${JSON.stringify(subs)}`]),
      ]);

      const first = await page.$eval("[data-pick-club]", (b) => b.getAttribute("data-pick-club"));
      await page.click(`[data-pick-club="${first}"]`);
      await page.waitForSelector("#month-cal:not([hidden])", { timeout: 25000 });
      const landed = await page.evaluate(() => localStorage.getItem("vf_club"));
      report("點了哪一隊就看哪一隊", [
        ...(landed === first ? [] : [`點了 ${first}，記住的卻是 ${landed}`]),
      ]);

      // The bug this exists for: the decision branch never consulted the
      // remembered club, so every reload threw the picker up again even
      // though the answer was already in localStorage. Reported
      // 2026-09-19 as 「每次重整就一定要重選一次隊伍」. Same context on
      // purpose — a fresh one would have empty storage and could never
      // see it.
      await page.reload();
      await page.waitForFunction(
        () =>
          document.querySelector("[data-pick-club]") ||
          document.querySelector("#month-cal:not([hidden])"),
        null,
        { timeout: 25000 }
      );
      await page.waitForTimeout(400);
      const askedAgain = (await page.$$("[data-pick-club]")).length;
      report("選過之後重新整理就不再問", [
        ...(askedAgain === 0 ? [] : ["已經選過了，重整後又跳出選擇畫面"]),
      ]);
    }
    await ctx.close();

    // --- arriving with a club already chosen: do not ask ---
    ctx = await browser.newContext({ viewport: { width: 390, height: 900 } });
    page = await ctx.newPage();
    page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
    const named = String(clubs[0].id);
    await page.goto(`${BASE}/member.html?as=${encodeURIComponent(WHO)}&club=${named}`);
    // Wait for whichever screen wins, not for the calendar specifically.
    // Waiting on the calendar meant that when the picker wrongly
    // appeared — the exact regression this asserts — renderClubPicker had
    // hidden #month-cal, the wait timed out, and the thrown error killed
    // the run before report() could say what went wrong. The check
    // detected the bug and then crashed instead of describing it.
    await page.waitForFunction(
      () =>
        document.querySelector("[data-pick-club]") ||
        document.querySelector("#month-cal:not([hidden])"),
      null,
      { timeout: 25000 }
    );
    await page.waitForTimeout(500);
    const shown = (await page.$$("[data-pick-club]")).length;
    const url = page.url();
    report("帶著 ?club= 進來就不要再問", [
      ...(shown === 0 ? [] : ["已經選好球隊了，卻還是跳出選擇畫面"]),
      ...(url.includes("club=") ? [`參數沒有從網址清掉：${url}`] : []),
    ]);
    await ctx.close();

    report("沒有 console 錯誤", errors);
  } finally {
    await browser.close();
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n該問的時候問，不該問的時候不問。");
  process.exit(failed ? 1 : 0);
})();
