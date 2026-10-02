// The member's money screen answers "where do I stand", not "list every
// entry". Counts come from the season — how many games you actually
// signed up for, how many you took leave from — and amounts come from
// the ledger. The one rule that must never break: the lines add up to
// the headline, or the screen is lying about money.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const page = load("member.html");
const { summaryRows } = page;

/** summaryRows takes everything as arguments on purpose, so these cases
 * can be stated without a page around them. */
function rowsFor({ entries, balance, games = [], name = "周安", starts = {} }) {
  return summaryRows(
    { balance: String(balance), entries, season_starts: starts },
    { id: 1, games },
    name
  );
}

const game = (over) => ({
  id: 1,
  absences: [],
  confirmed_drop_ins: [],
  waitlist_entries: [],
  ...over,
});

test("a plain member sees the fee and nothing else", () => {
  const rows = rowsFor({
    balance: -3055,
    games: [game({}), game({ id: 2 })],
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
    ],
  });

  assert.deepEqual(
    rows.map((r) => [r.label, r.detail, r.amount]),
    [["本季季費", "2 場", -3055]]
  );
});

test("leave is counted, and says how much of it was covered", () => {
  // The refund only happens when somebody covers, so "took leave 3
  // times" and "was refunded twice" are different facts and both matter.
  const rows = rowsFor({
    balance: -2585,
    games: [
      game({ id: 1, absences: [{ player_name: "周安", covered_by: "Jason" }] }),
      game({ id: 2, absences: [{ player_name: "周安", covered_by: null }] }),
      game({ id: 3 }),
    ],
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
      { entry_type: "absence_refund", amount: "470", season_id: 1 },
    ],
  }).find((r) => r.label === "請假退費");
  const leave = rows;

  assert.equal(leave.amount, 470);
  assert.match(leave.detail, /請假 2 次/);
  assert.match(leave.detail, /1 次有人代打/);
});

test("drop-ins are counted from the games you're actually on", () => {
  const rows = rowsFor({
    balance: -470,
    games: [
      game({ id: 1, confirmed_drop_ins: [{ player_name: "周安" }] }),
      game({ id: 2, confirmed_drop_ins: [{ player_name: "周安" }] }),
      game({ id: 3, confirmed_drop_ins: [{ player_name: "別人" }] }),
    ],
    entries: [{ entry_type: "drop_in_fee_charged", amount: "-470", season_id: 1 }],
  }).find((r) => r.label === "臨打費");
  const dropIn = rows;

  assert.equal(dropIn.detail, "2 場");
  assert.equal(dropIn.amount, -470);
});

test("money from another season is its own line, not mixed in", () => {
  // The ledger spans seasons on purpose — a refund can be carried
  // forward — so another season's money must not read as this season's.
  const rows = rowsFor({
    balance: -2350,
    games: [game({})],
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
      { entry_type: "absence_refund", amount: "705", season_id: 99 },
    ],
  });

  assert.equal(rows.find((r) => r.label === "上季餘額").amount, 705);
  assert.equal(rows.find((r) => r.label === "本季季費").amount, -3055);
});

test("a season booked for later is left out, not called 上季", () => {
  // Reported 2026-09-17 and again in 2026-10: a season booked for
  // January has its fees charged as soon as it is created. It used to
  // be shown as 其他季別 and counted in the headline, so a member read
  // as owing January's fee in October.
  const page = load("member.html");
  const season = { id: 1, games: [game({})] };
  const ledger = {
    balance: "-5055",
    season_starts: { 1: "2026-10-06", 42: "2027-01-05" },
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
      { entry_type: "season_fee_charged", amount: "-2000", season_id: 42 },
    ],
  };

  const rows = page.summaryRows(ledger, season, "周安");

  assert.equal(page.ledgerUpTo(ledger, season).total, -3055, "the headline");
  assert.equal(rows.reduce((t, r) => t + r.amount, 0), -3055, "the lines agree with it");
  assert.equal(rows.filter((r) => /上季|其他/.test(r.label)).length, 0);
});

test("what an earlier season still owes is carried in as 上季未繳", () => {
  const rows = rowsFor({
    balance: -3520,
    games: [game({})],
    starts: { 1: "2026-10-06", 7: "2026-07-07" },
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 7 },
      { entry_type: "payment", amount: "2590", season_id: 7 },
    ],
  });

  assert.equal(rows.find((r) => r.label === "上季未繳").amount, -465);
});

test("the lines always add up to the balance", () => {
  // The invariant. An entry type this screen doesn't know about still
  // has to appear, or the sum of what's shown contradicts the headline.
  const rows = rowsFor({
    balance: -1000,
    games: [game({})],
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
      { entry_type: "payment", amount: "2055", season_id: 1 },
      { entry_type: "something_new", amount: "0", season_id: 1 },
    ],
  });

  assert.equal(rows.reduce((t, r) => t + r.amount, 0), -1000);
});

test("an unknown entry type surfaces rather than vanishing", () => {
  const rows = rowsFor({
    balance: -500,
    games: [game({})],
    entries: [
      { entry_type: "season_fee_charged", amount: "-300", season_id: 1 },
      { entry_type: "invented_later", amount: "-200", season_id: 1 },
    ],
  });

  assert.equal(rows.find((r) => r.label === "其他").amount, -200);
  assert.equal(rows.reduce((t, r) => t + r.amount, 0), -500);
});

test("someone with nothing charged yet is told so, not shown a blank", () => {
  const rows = rowsFor({ balance: 0, games: [game({})], entries: [] });
  assert.deepEqual(rows.map((r) => r.label), ["尚無任何費用"]);
});
