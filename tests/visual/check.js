// Measures the pages a phone actually renders. See README.md.
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");

const FRONTEND = path.join(__dirname, "..", "..", "frontend");
const OUT = path.join(__dirname, "shots");

const SEASON = {
  id: 1, capacity: 18, minimum_roster: 12, share_per_game: "205", ac_surcharge: "540",
  game_start_time: "18:30:00", game_end_time: "22:00:00", location: "晴光館",
  change_deadline_days: 1,
  members: Array.from({ length: 18 }, (_, i) => ({
    id: i + 1, name: i === 0 ? "周恆" : "成員" + (i + 1),
    gender: i < 12 ? "male" : "female", linked: i % 3 !== 0,
  })),
};

const GAME = {
  id: 10, date: "2026-10-06", status: "scheduled", locked: false,
  air_conditioned: true, share: "235",
  absences: [
    { id: 100, player_name: "成員2", covered_by: "Momo" },
    // The other branch of the absent row's controls: nobody covering, so
    // it draws 找代打 + 銷假 rather than 改代打 + 取消, and the note is
    // 缺額 rather than somebody's name. A second row here also gives
    // 每一列都一樣高 something to compare *within* this tab, which one
    // row alone never could.
    { id: 101, player_name: "成員3", covered_by: null },
  ],
  confirmed_drop_ins: [
    { id: 200, player_id: 90, player_name: "Momo", gender: "male", covering: "成員2", linked: false },
    // The longest name on the sheet, carrying both notes at once — 臨打
    // plus who brought them. That pairing is the row most likely to grow
    // taller than its neighbours, and without brought_by_name in this
    // fixture the second note never renders and 每一列都一樣高 measures
    // nothing.
    { id: 201, player_id: 91, player_name: "WenChiao +1 女", gender: "female", covering: null, linked: false, brought_by_name: "周恆" },
  ],
  waitlist_entries: [{ id: 300, player_name: "測試", gender: "male" }],
};

const CHECKS = [];
const check = (name, fn) => CHECKS.push({ name, fn });

check("每一列都一樣高", (m) => {
  const heights = new Set(m.rows.map((r) => r.h));
  return heights.size === 1 ? null : `列高不一致: ${[...heights].join(", ")}`;
});

check("名單列的文字沒有被裁掉", (m) =>
  m.clipped.length ? `被容器切掉: ${m.clipped.join("、")}` : null);

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

check("代打選單沒有元素被擠出面板", (m) => {
  if (!m.picker) return "選單沒有打開，這項沒有量到";
  return m.picker.overflowing.length
    ? `超出面板: ${[...new Set(m.picker.overflowing)].join(", ")}`
    : null;
});

check("代打選單的輸入欄位至少 16px", (m) => {
  if (!m.picker) return null;
  return m.picker.smallInputs.length
    ? `字級過小: ${[...new Set(m.picker.smallInputs)].join(", ")}`
    : null;
});

check("等待中的按鈕會停用、標籤讓位給轉圈", (m) => {
  if (!m.busy) return "沒有量到";
  const problems = [];
  if (!m.busy.disabled) problems.push("按鈕沒有停用");
  if (!m.busy.spinner) problems.push("沒有轉圈");
  if (m.busy.labelVisible) problems.push("標籤沒有讓位");
  if (Math.abs(m.busy.widthChange) > 1) problems.push(`寬度跳動 ${m.busy.widthChange}px`);
  return problems.length ? problems.join("、") : null;
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

check("月曆換月份不會變形", (m) => {
  if (!m.calendar) return "沒有量到";
  const problems = [];
  const spilling = m.calendar.filter((s) => s.overflow > 0);
  if (spilling.length) {
    problems.push(`欄位擠出容器: ${spilling.map((s) => `${s.title} +${s.overflow}px`).join(", ")}`);
  }
  const shapes = [...new Set(m.calendar.map((s) => `${s.height}px ${s.cell}`))];
  if (shapes.length > 1) problems.push(`月份之間大小不一: ${shapes.join(" / ")}`);
  return problems.length ? problems.join("；") : null;
});

check("標題列的球隊名、品牌、頭像不會疊在一起", (m) => {
  if (!m.appBar) return "沒有量到";
  const { chip, brand, avatar, bar } = m.appBar;
  const problems = [];
  if (chip.right > brand.left + 0.5) {
    problems.push(`球隊名壓到品牌 ${Math.round(chip.right - brand.left)}px`);
  }
  if (brand.right > avatar.left + 0.5) {
    problems.push(`品牌壓到頭像 ${Math.round(brand.right - avatar.left)}px`);
  }
  if (avatar.right > bar.right + 0.5) {
    problems.push(`頭像超出標題列 ${Math.round(avatar.right - bar.right)}px`);
  }
  return problems.length ? problems.join("、") : null;
});

// How close a button's label is to breaking, not whether it has broken.
//
// Reported 2026-09-19 from an Android narrower than the organizer's
// iPhone: 申請當固定成員 broke after 申請當固定成 while 申請臨打 beside
// it stayed on one line. Nothing else here catches that — the button
// grows to fit a second line, so it never overflows and never trips
// 沒有元素超出畫面.
//
// The first version of this rule asked `lines > 1` and was measured at
// 390, which is worthless: the long labels fill 0.77 of their box at 390
// and 0.87 at 360, so they stay on one line with or without
// white-space:nowrap, and the rule would have passed no matter what.
// Measured at 320 they fill exactly 1.00 — no slack at all. A font a
// shade wider than desktop Chromium's, or a phone with text scaling on,
// is all it takes. That is why it broke on Android and not on an iPhone.
//
// So the useful question is the ratio, and the threshold is about slack
// rather than about wrapping: below 0.92 a label survives a font that
// renders ~8% wider; at 1.00 it is already over on some real device.
const FILL_LIMIT = 0.92;
check(`按鈕文字在最窄手機上留有餘裕（文字寬 ÷ 可用寬 < ${FILL_LIMIT}）`, (m) => {
  // Never silently pass: an empty list means the measure found nothing
  // and the rule is guarding air. Same failure mode the app-bar note
  // below warns about.
  if (!m.buttons || !m.buttons.length) return "沒有量到任何按鈕";
  const tight = m.buttons.filter((b) => b.fill >= FILL_LIMIT);
  return tight.length
    ? `太擠（請縮短文字）: ${tight
        .map((b) => `${b.label} ${b.fill.toFixed(2)}`)
        .join("、")}`
    : null;
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
        viewerName: "周恆", clubMembers: [],
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
    const clipped = [];
    const tabs = [];
    for (const key of ["attending", "absent", "queued"]) {
      showGameDetailTab(out, key);
      const panel = out.querySelector(`[data-gd-panel="${key}"]`);
      tabs.push({ key, visible: !panel.hidden, height: round(panel.getBoundingClientRect().height) });
      for (const r of panel.querySelectorAll(".att-row")) {
        rows.push({ tab: key, cls: r.className, h: round(r.getBoundingClientRect().height) });
        // Text cut off *inside* a row. Neither 每一列都一樣高 nor
        // 沒有元素超出畫面 can see this: a flex child shrinks to fit, so
        // the row keeps its height and nothing passes the screen edge —
        // the characters are simply clipped. Found 2026-09-23 by eye on
        // a screenshot, with both of those checks green, while 周恆 報名
        // was rendering as half a glyph wedged against 移除. The inline
        // .att-note reports clientWidth 0, so measure the box that does
        // the clipping rather than the note itself.
        for (const e of r.querySelectorAll("*")) {
          // .att-who is the one thing allowed to be shortened — a long
          // name ellipsises on purpose, so the reader can see it was
          // cut. Everything else cut here is cut mid-glyph with nothing
          // to say so, which is the bug. Deliberately not exempting
          // "anything with text-overflow: ellipsis": adding that
          // property to a box would then silence this check rather than
          // make the content fit.
          if (e.classList.contains("att-who")) continue;
          if (e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 1) {
            clipped.push(
              `${key} ${e.className || e.tagName} 少了 ${Math.round(e.scrollWidth - e.clientWidth)}px`
            );
          }
        }
      }
    }
    // The substitute picker, opened. It is hidden by default, so
    // nothing above would ever have measured it — and the 性別 select
    // was overflowing the panel on a phone, which is exactly the class
    // of bug this file exists for.
    showGameDetailTab(out, "absent");
    const opener = out.querySelector("[data-toggle-sub]");
    let picker = null;
    if (opener) {
      opener.click();
      const panel = out.querySelector(".picker");
      if (panel) {
        const box = panel.getBoundingClientRect();
        picker = {
          overflowing: [...panel.querySelectorAll("*")]
            .filter((e) => e.getBoundingClientRect().right > box.right + 0.5)
            .map((e) => e.className || e.tagName),
          smallInputs: [...panel.querySelectorAll("input, select")]
            .filter((e) => parseFloat(getComputedStyle(e).fontSize) < 16)
            .map((e) => e.className || e.tagName),
        };
      }
    }
    showGameDetailTab(out, "attending");

    // A control mid-request. Measured rather than trusted: the label has
    // to give way to the spinner, the button has to stop taking taps,
    // and the box must not resize — a button that changes width while
    // you are waiting moves everything beside it.
    const probe = document.querySelector("button.mini-action") ||
      document.querySelector("button");
    let busy = null;
    if (probe) {
      const before = probe.getBoundingClientRect().width;
      const release = markBusy(probe);
      // markBusy waits before showing anything; force it for the measure.
      probe.classList.add("is-busy");
      const style = getComputedStyle(probe);
      busy = {
        disabled: probe.disabled === true,
        spinner: getComputedStyle(probe, "::after").content !== "none",
        labelVisible: style.color !== "rgba(0, 0, 0, 0)" && style.color !== "transparent",
        widthChange: Math.round(probe.getBoundingClientRect().width - before),
      };
      release();
      probe.classList.remove("is-busy");
    }

    return {
      busy,
      picker,
      rows,
      clipped,
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

    // The calendar, paged across a year. Its cells used to take their
    // width from an aspect ratio rather than from the column they sat
    // in, so seven of them came to 355px inside a 332px grid and
    // Saturday hung off the card — except in a month opening on a
    // Sunday, where everything fitted and shrank instead. Paging
    // between the two reshaped it: 「切換月份可能會變形」, 2026-09-16.
    measured.calendar = await page.evaluate(() => {
      const games = Array.from({ length: 13 }, (_, i) => {
        const d = new Date(2026, 8, 1 + i * 7);
        const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        return { id: i + 1, date: iso, status: "scheduled" };
      });
      const box = document.createElement("div");
      box.className = "mcal";
      document.querySelector(".wrap").appendChild(box);
      renderMonthCalendar(box, games, () => {}, { viewKey: "check" });

      const round = (n) => Math.round(n);
      const shapes = [];
      for (let i = 0; i < 12; i += 1) {
        const grid = box.querySelectorAll(".mcal-grid")[1];
        const day = grid.querySelector(".mcal-cell:not(.empty)").getBoundingClientRect();
        shapes.push({
          title: box.querySelector(".mcal-title").textContent,
          height: round(box.getBoundingClientRect().height),
          cell: `${round(day.width)}x${round(day.height)}`,
          overflow: grid.scrollWidth - grid.clientWidth,
        });
        box.querySelector('.mcal-nav[data-dir="1"]').click();
      }
      box.remove();
      return shapes;
    });

    // The app bar, built here rather than found. Nothing else in this
    // file renders one: the scratch page is a bare .wrap with a single
    // #out div, so querySelector(".app-bar") is null and a rule written
    // against it would report "沒有量到" forever while the bug shipped.
    // That is not hypothetical — three separate checks hid a stranded
    // ledger sheet this same day by preparing state the real reader
    // never has.
    //
    // The name is the one that actually collided on a phone. The bar is
    // grid-template-columns: 1fr auto 1fr, and a 1fr track is
    // minmax(auto, 1fr), so it refuses to shrink below its content —
    // min-width:0 on the *item* cannot help, because it is the *track*
    // that will not give. Capping the club name only moves the width at
    // which it breaks, so the check measures overlap rather than length.
    measured.appBar = await page.evaluate(() => {
      const bar = document.createElement("div");
      bar.className = "app-bar";
      bar.innerHTML =
        '<span class="app-bar-left">' +
        '<button type="button" class="club-chip">' +
        '<i class="club-dot">測</i>' +
        '<span class="club-chip-name">測試球隊（seed）</span>' +
        '<span class="caret">▾</span>' +
        "</button></span>" +
        '<button type="button" class="brand">VolleyFlow</button>' +
        '<span class="app-bar-right">' +
        '<button type="button" class="avatar">周</button>' +
        "</span>";
      document.querySelector(".wrap").prepend(bar);

      const round = (n) => Math.round(n * 100) / 100;
      const box = (el) => {
        const b = el.getBoundingClientRect();
        return { left: round(b.left), right: round(b.right), w: round(b.width) };
      };
      const measurement = {
        chip: box(bar.querySelector(".club-chip")),
        brand: box(bar.querySelector(".brand")),
        avatar: box(bar.querySelector(".avatar")),
        bar: box(bar),
      };
      bar.remove();
      return measurement;
    });

    // Long labels in the layout that actually breaks them: two .btn
    // sharing a .btn-row inside an .empty-state, which is how the invite
    // screen and the season wizard lay their buttons out. Built here
    // rather than found, for the same reason as the app bar above — the
    // scratch page carries no .btn at all, so a rule written against
    // querySelectorAll(".btn") would report nothing forever.
    //
    // Measured at 320 rather than this page's 390: at 390 these labels
    // fill 0.77 of their box and at 360 only 0.87, so nothing shows there
    // however long they get. 320 is the narrowest phone still worth
    // supporting, and it is where the fill ratio reaches 1.00.
    //
    // white-space is forced to nowrap for the measure, and that direction
    // matters: measuring with it set to *normal* inverts the rule. A
    // Range over wrapped text reports the widest line, not the text's
    // natural width, so at 320 a label that breaks measures 86px against
    // 96px of box (0.90 — passing) while the same label kept on one line
    // measures 101 against 101 (1.00 — failing). The worse the layout,
    // the healthier the number looked. Written the wrong way round first
    // and caught only because the rule passed when it was expected to
    // fail.
    //
    // Forced explicitly rather than relying on .btn already carrying
    // nowrap: if that declaration is ever removed, this measure must not
    // quietly start reporting wrapped widths again.
    //
    // Text width via Range rather than clientHeight ÷ lineHeight: .btn
    // sets no line-height, so getComputedStyle returns "normal" and
    // parseFloat gives NaN.
    //
    // The pairs are real: each is a .btn-row that exists in the app, with
    // the longest label of the moment beside the one it actually sits
    // next to. Shortening a label is a fix; deleting it from this list is
    // not.
    await page.setViewportSize({ width: 320, height: 900 });
    measured.buttons = await page.evaluate(() => {
      const pairs = [
        ["固定成員", "臨打成員"],
        ["上一步", "下一步"],
        ["連結加入", "建立球隊"],
      ];
      const host = document.createElement("div");
      host.className = "empty-state";
      host.style.textAlign = "left";
      host.innerHTML = pairs
        .map(
          ([a, b]) =>
            '<div class="btn-row" style="margin-top:12px">' +
            `<button type="button" class="btn btn-primary">${a}</button>` +
            `<button type="button" class="btn btn-quiet">${b}</button>` +
            "</div>"
        )
        .join("");
      document.querySelector(".wrap").prepend(host);

      // children.length === 0 keeps this to plain-text buttons: a control
      // holding several spans (.todo-line) would measure its spans, not
      // its text.
      const out = [...host.querySelectorAll("button")]
        .filter((b) => b.children.length === 0)
        .map((b) => {
          b.style.whiteSpace = "nowrap";
          const range = document.createRange();
          range.selectNodeContents(b);
          const cs = getComputedStyle(b);
          const avail =
            b.clientWidth -
            parseFloat(cs.paddingLeft) -
            parseFloat(cs.paddingRight);
          // On one forced line this is the width the label actually
          // needs. No line count here: under nowrap it is always 1, and
          // reporting it would invite the same inverted reasoning again.
          const textW = range.getBoundingClientRect().width;
          return {
            label: b.textContent,
            fill: avail > 0 ? textW / avail : 99,
          };
        });
      host.remove();
      return out;
    });
    await page.setViewportSize({ width: 390, height: 900 });

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
