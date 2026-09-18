// What the folder root does with what reaches it.
//
// index.html is a plain forward to member.html. It is not the LIFF
// endpoint and no longer has any part in LIFF routing: each rich menu
// destination has its own LIFF app pointing straight at its own page
// (member.html, ledger.html, report.html), so a menu tap never passes
// through here.
//
// This file used to assert three more cases, all about liff.state —
// that the root read the parameter and forwarded to the page it named.
// They were deleted on 2026-09-18 with the design they guarded, not
// because they were failing. Briefly, the LIFF endpoint was pointed at
// this folder and this page tried to resolve liff.state itself, which
// threw away the SDK handshake and put every menu button into an
// infinite login loop in the LINE app. The fix was not to resolve
// liff.state more carefully; it was to stop routing through one page at
// all.
//
// So: do not reinstate liff.state cases here. If one ever fails again
// it will be because somebody pointed a LIFF endpoint back at the
// folder root, and the check to write then is a different one.
//
// What remains are the two paths that still reach this page, and both
// still matter:
//
//   - an invite link already shared in LINE, if its recipient opens the
//     bare site rather than the liff.line.me link. The token is the only
//     thing naming the club (api/invites.py), so losing it does not
//     error — it lands them on a picker that cannot show them the club
//     they were invited to.
//   - anybody typing the folder root. They should get the app, not a
//     directory listing or a 404.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const WHO = "周恆";

const CASES = [
  {
    name: "邀請連結的 token 有跟著過去",
    query: "?invite=TESTTOKEN123",
    wants: "/member.html?invite=TESTTOKEN123",
  },
  {
    name: "什麼都沒帶就只是會員頁",
    query: "",
    wants: "/member.html",
  },
];

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  try {
    for (const c of CASES) {
      const context = await browser.newContext({ viewport: { width: 390, height: 900 } });
      const page = await context.newPage();

      // The document chain, not the final URL. member.html boots, finds
      // no LINE session in a plain browser and calls liff.login(), so by
      // the end the address bar is on access.line.me and says nothing
      // about whether the forward worked.
      const docs = [];
      page.on("response", (r) => {
        if (r.request().resourceType() === "document") {
          docs.push(r.url().replace(BASE, ""));
        }
      });

      const sep = c.query ? "&" : "?";
      try {
        await page.goto(`${BASE}/${c.query}${sep}as=${encodeURIComponent(WHO)}`, {
          waitUntil: "domcontentloaded",
        });
      } catch (e) {
        // A navigation interrupted by the next one is how a forward
        // looks from here; the chain below is the evidence either way.
      }
      await page.waitForTimeout(1500);

      const reached = docs.some((d) => d.startsWith(c.wants));
      report(c.name, [
        ...(reached ? [] : [`沒有走到 ${c.wants}`]),
        ...(reached ? [] : [`實際走過：${docs.join(" → ") || "（沒有任何頁面）"}`]),
      ]);
      await context.close();
    }
  } finally {
    await browser.close();
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n根目錄把進來的人都送到會員頁。");
  process.exit(failed ? 1 : 0);
})();
