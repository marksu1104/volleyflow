// Every number on the 帳務 screen comes out of these two functions, and
// they are the closest the frontend gets to handling money. The rule
// they encode: a balance is signed from the player's point of view —
// negative means they owe the organizer, positive means the organizer
// owes them — and the screen must offer the action that matches.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const { splitLedger, moneyRowHtml, balanceOf } = load("organizer-ledger.html");

const row = (balance, seasonTotal, fee) => ({
  player_id: 1,
  balance: String(balance),
  season_total: String(seasonTotal),
  season_fee_charged: String(fee),
});

test("separates this season's money from what carried in", () => {
  // Charged 3055 this season, but 470 of credit came from before, so
  // 2585 is what's actually owed now.
  const split = splitLedger(row(-2585, -3055, -3055));
  assert.equal(split.balance, -2585);
  assert.equal(split.seasonTotal, -3055);
  assert.equal(split.otherTotal, 470);
});

test("a settled member has nothing on either side", () => {
  const split = splitLedger(row(0, 0, -5000));
  assert.equal(split.balance, 0);
  assert.equal(split.otherTotal, 0);
});

test("a credit stays a credit", () => {
  const split = splitLedger(row(705, 0, 0));
  assert.equal(split.balance, 705);
  assert.equal(split.otherTotal, 705);
});

test("someone with no entries at all reads as zero, not as broken", () => {
  // A member added a moment ago, or a drop-in not yet charged.
  const zero = balanceOf(42);
  assert.equal(zero.balance, "0");
  assert.equal(splitLedger(zero).balance, 0);
});

test("owing money offers to collect it, and shows why", () => {
  const html = moneyRowHtml(
    1,
    "林書妤",
    "female",
    splitLedger(row(-2585, -3055, -3055)),
    "本季季費",
    3055
  );
  assert.match(html, /應收 \$2585/);
  assert.match(html, /已收/);
  assert.match(html, /其他季別/, "the carried credit has to be visible, not just netted");
});

test("the 季費 page states this season's fee, not another season's debt", () => {
  // Reported 2026-09-17: a club running its 10~12月 season with a 1~3月
  // season already booked saw January's unpaid fee on October's 季費
  // page — added into 應收, and labelled 上季餘額 when it was next
  // season's. A season's fees land on the ledger the moment it is
  // created, so "the other season" is not necessarily a past one.
  // Here: 3055 owed for this season, 2000 for a season not on screen.
  const split = splitLedger(row(-5055, -3055, -3055), "season");

  assert.equal(split.due, -3055, "the row is about this season only");
  const html = moneyRowHtml(1, "林書妤", "female", split, "本季季費", 3055);
  assert.match(html, /應收 \$3055/);
  assert.doesNotMatch(html, /應收 \$5055/, "another season's debt is not collected here");
  assert.match(html, /其他季別/, "it is still visible as context");
  assert.doesNotMatch(html, /上季/, "the other season may well be a future one");
});

test("已收 collects the season's figure, not the club-wide one", () => {
  // The button records a payment tagged to the season on screen, so
  // collecting the club-wide total would book another season's money
  // against this one — the part of this bug that moved real money.
  const html = moneyRowHtml(
    1,
    "林書妤",
    "female",
    splitLedger(row(-5055, -3055, -3055), "season"),
    null,
    null
  );

  assert.match(html, /recordFullPayment\(this, 1, 3055\)/);
  assert.doesNotMatch(html, /recordFullPayment\(this, 1, 5055\)/);
});

test("a season with nothing owed in it reads as settled, debt elsewhere or not", () => {
  // What the 未收 count on the 季費 card is driven by. It used to be
  // club-wide while the progress bar above it was already season-scoped,
  // so one card could say 全部已收 and 3 人未收 at the same time.
  const split = splitLedger(row(-2000, 0, -3055), "season");

  assert.equal(split.due, 0);
  assert.match(moneyRowHtml(1, "林書妤", "female", split, null, null), /已結清/);
});

test("季末結算 still squares the whole club balance", () => {
  // The other half of the rule. Settling a season is the full
  // reckoning, and a balance carried between seasons is exactly what
  // has to be squared there — CLAUDE.md 2.4.
  const split = splitLedger(row(-5055, -3055, -3055), "club");

  assert.equal(split.due, -5055);
  assert.match(moneyRowHtml(1, "林書妤", "female", split, null, null), /應收 \$5055/);
});

test("being owed money offers a refund but doesn't insist", () => {
  // Leaving it on the balance is the default; paying it back is the
  // exception the organizer chooses. The label says 退款 rather than
  // 現金退款 because most of this money moves by LINE Pay.
  const html = moneyRowHtml(2, "翔", "male", splitLedger(row(705, 0, 0)), null, null);
  assert.match(html, /應退 \$705/);
  assert.match(html, /保留於餘額/);
  assert.match(html, /退款/);
  assert.doesNotMatch(html, /現金/);
});

test("a settled member is shown as settled, with nothing to press", () => {
  const html = moneyRowHtml(3, "Jason", "male", splitLedger(row(0, 0, -3055)), null, null);
  assert.match(html, /已結清/);
  assert.doesNotMatch(html, /已收<\/button>/);
});

test("names are escaped — they come from LINE profiles", () => {
  const html = moneyRowHtml(
    4,
    '<img src=x onerror=alert(1)>',
    null,
    splitLedger(row(0, 0, 0)),
    null,
    null
  );
  assert.doesNotMatch(html, /<img src=x/);
});

test("a guest's row names who to collect the cash from", () => {
  // The guest's fee is on the guest's own ledger, but they have no
  // account and pay nothing — without this the organizer sees
  // "小明 應收 $235" and no way to know who to ask.
  const { splitLedger, moneyRowHtml, applyBalances } = load("organizer-ledger.html");
  applyBalances([
    { player_id: 7, balance: "-235", season_total: "-235", season_fee_charged: "0", brought_by: "周安" },
  ]);

  const html = moneyRowHtml(7, "小明", "male", splitLedger(row(-235, -235, 0)), null, null);

  assert.match(html, /周安 報名/);
});

test("someone who signed themselves up gets no such tag", () => {
  const { splitLedger, moneyRowHtml, applyBalances } = load("organizer-ledger.html");
  applyBalances([
    { player_id: 8, balance: "-235", season_total: "-235", season_fee_charged: "0", brought_by: null },
  ]);

  const html = moneyRowHtml(8, "Jason", "male", splitLedger(row(-235, -235, 0)), null, null);

  assert.doesNotMatch(html, /帶</);
});
