// The all-clubs money page — ledger.html.
//
// Two things here are worth pinning without a browser, and one of them
// is a rule about money rather than about markup.
//
// renderOwedTotal must never show a *net* figure. Balances do not move
// between clubs (docs/billing-rules.md, "Ledger"), so offsetting a club
// that owes you against a club you owe would put a number on screen
// describing money that does not exist. The headline is the sum of what
// is owed, and nothing else.
//
// clubRowHtml has the same guard as profile.html's: a cached club list
// stored before balances shipped carries no such field, and
// Number(undefined) is NaN. That is not hypothetical here — the first
// browser run of this page hit exactly that state, because the dev API
// had not been restarted and was still serving rows without a balance.
// The row correctly showed nothing rather than 應繳 $NaN.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const page = load("ledger.html");
const { clubRowHtml, renderOwedTotal } = page;

const club = (over) => ({ id: 1, name: "晴光館", role: "member", ...over });

// --- the headline: what you owe, never a net ---------------------------

/** renderOwedTotal writes into #owed-total, so the assertions read the
 * stub element back rather than a return value.
 *
 * Via the harness's `elements` map rather than `page.document`: the
 * harness puts `document` on globalThis and returns only
 * { elements, makeSelect, makeElement }, so page.document is undefined.
 * The map is keyed by id and memoised — getElementById hands back the
 * same stub every time — so this really is the element the function
 * just wrote to, not a fresh one reading its defaults back. */
function totalFor(clubs) {
  renderOwedTotal(clubs);
  const box = page.elements["owed-total"];
  return { hidden: box.hidden, html: box.innerHTML || "" };
}

test("owing in two clubs adds up to what is owed, and says how many", () => {
  const out = totalFor([
    club({ id: 1, balance: "-2845" }),
    club({ id: 2, balance: "-345" }),
  ]);

  assert.equal(out.hidden, false);
  assert.match(out.html, /\$3190/);
  assert.match(out.html, /2 隊/);
});

test("a club that owes you is never netted against one you owe", () => {
  // The invariant. Owed $500 in one club and owing $300 in another is
  // not "$200 owed to you" — those are different people's money, and it
  // can never be settled by subtraction.
  const out = totalFor([
    club({ id: 1, balance: "500" }),
    club({ id: 2, balance: "-300" }),
  ]);

  assert.match(out.html, /\$300/, "the headline is what you owe");
  assert.ok(!/\$200/.test(out.html), "never a net figure");
  assert.ok(!/2 隊/.test(out.html), "only one club is owed money");
});

test("owing nothing anywhere hides the headline rather than showing zero", () => {
  const out = totalFor([club({ id: 1, balance: "0" }), club({ id: 2, balance: "470" })]);

  assert.equal(out.hidden, true);
});

test("clubs with no balance yet count as zero, not as NaN", () => {
  // The stale-cache and stale-server case. A missing field must not
  // poison the sum into NaN, which would render 全部應繳 $NaN.
  const out = totalFor([club({ id: 1 }), club({ id: 2, balance: "-100" })]);

  assert.ok(!/NaN/.test(out.html));
  assert.match(out.html, /\$100/);
});

// --- the rows ----------------------------------------------------------

test("owing money says how much, and being owed reads as a balance", () => {
  assert.match(clubRowHtml(club({ balance: "-2845" })), /應繳 \$2845/);
  assert.match(clubRowHtml(club({ balance: "470" })), /餘額 \$470/);
});

test("a settled club says so, rather than showing nothing", () => {
  // Different from profile.html on purpose: this is the money page, so
  // "settled" is an answer worth giving rather than an absence.
  assert.match(clubRowHtml(club({ balance: "0" })), /已結清/);
});

test("a club whose balance has not arrived shows no figure at all", () => {
  const html = clubRowHtml(club({}));

  assert.ok(!/NaN/.test(html), "never render NaN on a money row");
  assert.ok(!/應繳|餘額|已結清/.test(html), "unknown is not zero and not settled");
  assert.match(html, /晴光館/, "the name is known, so it still shows");
});

test("every row is tappable, because the detail is behind the tap", () => {
  const html = clubRowHtml(club({ balance: "-100" }));

  assert.match(html, /<button/, "a row opens that club's breakdown");
  assert.match(html, /openDetail\(1\)/);
});

test("an organizer is marked as one", () => {
  assert.match(clubRowHtml(club({ role: "organizer" })), /管理員/);
  assert.ok(!/管理員/.test(clubRowHtml(club({ role: "member" }))));
});
