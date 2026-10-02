// Every number on the 帳務 screen comes out of these two functions, and
// they are the closest the frontend gets to handling money. The rule
// they encode: a balance is signed from the player's point of view —
// negative means they owe the organizer, positive means the organizer
// owes them — and the screen must offer the action that matches.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const { splitLedger, moneyRowHtml, balanceOf } = load("organizer-ledger.html");

// `through` is the server's through_season: this season plus earlier
// ones. It equals the balance unless a later season is already booked.
const row = (balance, seasonTotal, fee, through = balance) => ({
  player_id: 1,
  balance: String(balance),
  season_total: String(seasonTotal),
  through_season: String(through),
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
  assert.match(html, /上季餘額扣除 <b>\$470/, "the carried credit has to be visible, not just netted");
});

test("the 季費 page leaves out a season booked for later", () => {
  // Reported 2026-09-17: a club running its 10~12月 season with a 1~3月
  // season already booked saw January's unpaid fee on October's 季費
  // page. A season's fees land on the ledger the moment it is created.
  // Here: 3055 owed for this season, 2000 for a season not yet started.
  const split = splitLedger(row(-5055, -3055, -3055, -3055));

  assert.equal(split.due, -3055);
  const html = moneyRowHtml(1, "林書妤", "female", split, "本季季費", 3055);
  assert.match(html, /應收 \$3055/);
  assert.doesNotMatch(html, /5055/, "a later season's fee is not collected here");
  assert.doesNotMatch(html, /上季|其他季別/, "and is not shown as context either");
});

test("已收 collects what is due up to this season, never a later season's fee", () => {
  // The button records a payment tagged to the season on screen, so
  // collecting a later season's fee would book that money against this one.
  const html = moneyRowHtml(
    1,
    "林書妤",
    "female",
    splitLedger(row(-5055, -3055, -3055, -3055)),
    null,
    null
  );

  assert.match(html, /recordFullPayment\(this, 1, 3055\)/);
});

test("what last season left unpaid is collected with this season's fee", () => {
  const split = splitLedger(row(-3520, -3055, -3055));

  const html = moneyRowHtml(1, "林書妤", "female", split, "本季季費", 3055);

  assert.match(html, /應收 \$3520/);
  assert.match(html, /上季未繳 <b>\$465/);
});

test("季末結算 squares this season and earlier ones, not a season booked later", () => {
  // Reported 2026-10-02: settling October with January booked showed
  // everyone owing January's fee, and 已收 would have recorded it as
  // October's money. 470 of credit from before, 2000 owed for later.
  const split = splitLedger(row(-4585, -3055, -3055, -2585));

  assert.equal(split.due, -2585);
  assert.match(moneyRowHtml(1, "林書妤", "female", split, null, null), /應收 \$2585/);
});

test("當天臨打 still collects only this season's money", () => {
  const split = splitLedger(row(-2000, 0, -3055), "season");

  assert.equal(split.due, 0);
  assert.match(moneyRowHtml(1, "林書妤", "female", split, null, null), /已結清/);
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
