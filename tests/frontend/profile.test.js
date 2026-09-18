// The 我的球隊 rows on 個人資料 — one line per club, carrying that club's
// balance.
//
// This file exists because the card had no coverage at all while
// displaying money. The balance used to be fetched per club and passed
// in separately; it now rides on the club row itself (GET
// /players/{id}/clubs), which removed an N+1 and a second repaint.
//
// The case worth guarding is the one nothing else can see: getJsonSWR
// paints the *cached* list before the network answers, and a copy
// stored before the balance shipped has no such field. Number(undefined)
// is NaN, so the obvious version of this renders 應繳 $NaN on a money
// row — once per reader, on their first load after a deploy, which is
// exactly the kind of bug that reaches a phone with every static check
// green.
//
// Verified by mutation rather than by being green: with the guard
// replaced by `const known = true`, two of these fail. That check is
// worth repeating on anything added here, because it already caught one
// test in this file that passed either way.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const page = load("profile.html");
const { clubRowHtml } = page;

const club = (over) => ({ id: 1, name: "晴光館", role: "member", ...over });

test("owing money says how much is owed", () => {
  const html = clubRowHtml(club({ balance: "-2845" }));

  assert.match(html, /應繳 \$2845/);
  assert.match(html, /bal minus/);
});

test("being owed money reads as a balance, not a charge", () => {
  // Positive means the organizer owes the player — the opposite
  // direction, and it must not be shown as something to pay.
  const html = clubRowHtml(club({ balance: "470" }));

  assert.match(html, /餘額 \$470/);
  assert.match(html, /bal plus/);
  assert.ok(!/應繳/.test(html), "being owed is not a charge");
});

test("a settled club shows no figure at all", () => {
  const html = clubRowHtml(club({ balance: "0" }));

  assert.ok(!/應繳|餘額/.test(html), "zero is not worth a number on the row");
  assert.match(html, /晴光館/, "but the club is still listed");
});

test("a cached club row from before balances shipped shows no figure", () => {
  // The guard. Not the same as a zero balance: this reader's money is
  // unknown until the network copy lands, and saying nothing is the only
  // honest answer. Saying $NaN — or $0 — would both be lies.
  const html = clubRowHtml(club({}));

  assert.ok(!/NaN/.test(html), "never render NaN on a money row");
  assert.ok(!/應繳|餘額/.test(html), "unknown is not zero and not a figure");
  assert.match(html, /晴光館/, "the name is known, so it still shows");
});

test("a null balance shows no figure, though not because of the guard", () => {
  // Named honestly after a mutation run: Number(null) is 0, not NaN, so
  // this lands in the zero branch and still passes with the guard
  // removed. It pins the outcome, not the guard — the tests either side
  // of it are the ones that actually bite. Left in because the outcome
  // is worth holding, renamed because a test that looks like it guards
  // something it doesn't is its own kind of lie.
  const html = clubRowHtml(club({ balance: null }));

  assert.ok(!/NaN/.test(html));
  assert.ok(!/應繳|餘額/.test(html));
});

test("a balance that is not a number is refused rather than printed", () => {
  // Defensive: whatever arrives, the row must never put a non-figure
  // where a figure goes.
  const html = clubRowHtml(club({ balance: "not-a-number" }));

  assert.ok(!/NaN/.test(html));
  assert.ok(!/應繳|餘額/.test(html));
});

test("an organizer is marked as one, and still shows their balance", () => {
  const html = clubRowHtml(club({ role: "organizer", balance: "-2845" }));

  assert.match(html, /管理員/);
  assert.match(html, /應繳 \$2845/);
});

test("a member is not labelled", () => {
  const html = clubRowHtml(club({ role: "member", balance: "-100" }));

  assert.ok(!/管理員/.test(html));
});
