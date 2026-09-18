// What the folder root does with what LINE sends it.
//
// index.html exists so the LIFF endpoint can point at the folder rather
// than at one page inside it, which is what lets each rich menu button
// name its own destination and land in a single document load.
//
// The thing worth guarding is a mechanism I got wrong first time round.
// Opening liff.line.me/{id}/report.html does NOT fetch
// <endpoint>/report.html. LINE loads the endpoint itself with
// ?liff.state=%2Freport.html appended and expects the LIFF SDK on that
// page to act on it. Measured against the live site before the fix:
//
//   200  liff.line.me/{id}/report.html
//   200  .../volleyflow/member.html?liff.state=%2Freport.html
//   404  .../volleyflow/member.html/report.html
//
// index.html deliberately does not load the LIFF SDK — it exists to be
// left as fast as possible — so nothing else will act on liff.state. An
// earlier version forwarded every parameter to member.html, which would
// have reproduced that 404 for all three menu buttons at once.
//
// Both branches matter and they fail differently:
//   - liff.state present  -> every rich menu button, silently 404
//   - liff.state absent   -> every invite link already shared in LINE
//
// The invite case is the quieter of the two. The token is the only thing
// naming the club (api/invites.py), so dropping it does not error — it
// lands the visitor on a club picker that cannot show them the club they
// were invited to.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const WHO = "周恆";

const CASES = [
  {
    name: "選單的問題回報落在回報頁",
    query: "?liff.state=%2Freport.html",
    wants: "/report.html",
  },
  {
    name: "選單的我的帳務落在帳務頁",
    query: "?liff.state=%2Fledger.html",
    wants: "/ledger.html",
  },
  {
    name: "liff.state 自己帶的參數不會掉",
    query: "?liff.state=%2Fmember.html%3Fopen%3Dledger",
    wants: "/member.html?open=ledger",
  },
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

      // The document chain, not the final URL. Each target page boots,
      // finds no LINE session in a plain browser and calls liff.login(),
      // so by the end the address bar is on access.line.me and says
      // nothing about whether the redirect worked. Reading the final URL
      // reported a working redirect as broken the first time this was
      // measured by hand.
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
        // A navigation interrupted by the next one is how a redirect
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

  console.log(
    failed ? `\n${failed} 項有問題。` : "\n根目錄把每一種進來的方式都送到該去的頁面。"
  );
  process.exit(failed ? 1 : 0);
})();
