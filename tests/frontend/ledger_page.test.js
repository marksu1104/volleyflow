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

test("a club that owes you is never netted against the ones you owe", () => {
  // The invariant. Owed $500 in one club and owing $300 and $200 in two
  // others is not "$0" — those are different people's money and cannot
  // be settled by subtraction. Two owing clubs rather than one, because
  // the headline only appears when it is actually adding up; with a
  // single debt it would be hidden and this would prove nothing.
  // The three figures are deliberately chosen so they cannot be
  // confused: the debts sum to 500, the credit is 900, and the net would
  // be 400. Only one of those can be right, and each wrong answer is
  // distinguishable from the others. An earlier version used a credit of
  // 500 against debts of 300 and 200 — the correct total and the credit
  // were then the same number, so the assertion passed either way.
  const out = totalFor([
    club({ id: 1, balance: "900" }),
    club({ id: 2, balance: "-300" }),
    club({ id: 3, balance: "-200" }),
  ]);

  assert.match(out.html, /\$500/, "the headline is the debts added up");
  assert.ok(!/\$900/.test(out.html), "the credit is not money you owe");
  assert.ok(!/\$400/.test(out.html), "and it must never be netted against them");
  assert.match(out.html, /2 隊/, "two clubs are owed money");
});

test("owing nothing anywhere hides the headline rather than showing zero", () => {
  const out = totalFor([club({ id: 1, balance: "0" }), club({ id: 2, balance: "470" })]);

  assert.equal(out.hidden, true);
});

test("hiding the headline clears it, instead of leaving a stale figure", () => {
  // Found the hard way: a test read back a headline left behind by the
  // previous case. getJsonSWR paints this page twice — cache, then
  // network — so a headline written on the first pass can be wrong on
  // the second, and a hidden node still holding a figure is one
  // accidental unhide away from showing money that is no longer true.
  // Nothing else in this file would notice if the clearing went away.
  totalFor([club({ id: 1, balance: "-300" }), club({ id: 2, balance: "-200" })]);
  const after = totalFor([club({ id: 1, balance: "-100" })]);

  assert.equal(after.hidden, true);
  assert.equal(after.html, "", "no stale figure left inside the hidden box");
});

test("one club owing does not get a headline repeating its own row", () => {
  // The figure would appear twice, stacked, saying nothing the row below
  // does not already say.
  const out = totalFor([club({ id: 1, balance: "-2845" }), club({ id: 2, balance: "0" })]);

  assert.equal(out.hidden, true);
});

test("clubs with no balance yet count as zero, not as NaN", () => {
  // The stale-cache and stale-server case. A missing field must not
  // poison the sum into NaN, which would render 全部應繳 $NaN.
  //
  // Two owing clubs, not one: the headline only appears when it is
  // actually adding up, so a single debt would hide it and this would
  // assert against an empty box.
  const out = totalFor([
    club({ id: 1 }),
    club({ id: 2, balance: "-100" }),
    club({ id: 3, balance: "-200" }),
  ]);

  assert.ok(!/NaN/.test(out.html));
  assert.match(out.html, /\$300/, "the unknown one contributes nothing, not NaN");
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
