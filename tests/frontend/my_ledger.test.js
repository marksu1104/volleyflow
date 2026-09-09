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
function rowsFor({ entries, balance, games = [], name = "蘇慬" }) {
  return summaryRows({ balance: String(balance), entries }, { id: 1, games }, name);
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
      game({ id: 1, absences: [{ player_name: "蘇慬", covered_by: "Ricky" }] }),
      game({ id: 2, absences: [{ player_name: "蘇慬", covered_by: null }] }),
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
      game({ id: 1, confirmed_drop_ins: [{ player_name: "蘇慬" }] }),
      game({ id: 2, confirmed_drop_ins: [{ player_name: "蘇慬" }] }),
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
  // forward — so a credit from last season must not read as this
  // season's.
  const rows = rowsFor({
    balance: -2350,
    games: [game({})],
    entries: [
      { entry_type: "season_fee_charged", amount: "-3055", season_id: 1 },
      { entry_type: "absence_refund", amount: "705", season_id: 99 },
    ],
  });

  assert.equal(rows.find((r) => r.label === "上季結轉").amount, 705);
  assert.equal(rows.find((r) => r.label === "本季季費").amount, -3055);
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
  assert.deepEqual(rows.map((r) => r.label), ["還沒有任何費用"]);
});
