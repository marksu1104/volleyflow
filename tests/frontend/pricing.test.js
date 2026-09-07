// The one place billing arithmetic is duplicated outside Python.
//
// previewSharePerGame mirrors pricing.share_per_game so the new-season
// wizard can show the effect of a date or a member as you add it. The
// server stays authoritative, but a preview that disagrees with what
// gets charged is worse than no preview, so these cases are checked
// against the same numbers as the Python engine — see
// tests/api/test_season_fee_ledger.py and docs/billing-rules.md.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const { previewSharePerGame, describeDate, dateKey } = load();

test("share divides cost by games and members, rounding up", () => {
  // 54990 / 13 / 18 = exactly 235
  assert.equal(previewSharePerGame(54990, 13, 18), 235);
  // 10000 / 7 / 5 = 285.71..., and nobody may be charged less than cost
  assert.equal(previewSharePerGame(10000, 7, 5), 286);
  assert.equal(previewSharePerGame(999, 1, 1), 999);
});

test("a season fee is the share times the games", () => {
  assert.equal(previewSharePerGame(54990, 13, 18) * 13, 3055);
});

test("exact division stays exact rather than drifting up", () => {
  // The failure this guards: floating point turning 235 into
  // 235.00000000000003, which ceil would round to 236 and overcharge
  // every member by a dollar a game.
  for (const [cost, games, members, expected] of [
    [10000, 2, 1, 5000],
    [20000, 2, 1, 10000],
    [54990, 13, 18, 235],
    [33333, 11, 7, 433],
  ]) {
    assert.equal(previewSharePerGame(cost, games, members), expected);
  }
});

test("impossible inputs give no answer instead of a wrong one", () => {
  assert.equal(previewSharePerGame(10000, 0, 5), null);
  assert.equal(previewSharePerGame(10000, 5, 0), null);
});

// The wizard generates a season's dates from "every <weekday>, from
// <date>, for <n> weeks", which is how these schedules are actually
// described. Getting a date wrong here puts a game on the wrong day for
// the whole season.
function generate(startValue, weekday, weeks) {
  const first = new Date(startValue + "T00:00:00");
  while (first.getDay() !== weekday) first.setDate(first.getDate() + 1);
  const out = [];
  for (let i = 0; i < weeks; i++) {
    const d = new Date(first);
    d.setDate(first.getDate() + i * 7);
    out.push(dateKey(d));
  }
  return out;
}

test("generating dates starts on the chosen weekday", () => {
  assert.deepEqual(generate("2026-07-07", 2, 3), [
    "2026-07-07",
    "2026-07-14",
    "2026-07-21",
  ]);
  // Sunday start, Tuesday games: walks forward rather than back.
  assert.deepEqual(generate("2026-07-05", 2, 2), ["2026-07-07", "2026-07-14"]);
});

test("generating dates crosses month and year boundaries", () => {
  assert.deepEqual(generate("2026-07-28", 2, 3), [
    "2026-07-28",
    "2026-08-04",
    "2026-08-11",
  ]);
  assert.deepEqual(generate("2026-12-29", 2, 2), ["2026-12-29", "2027-01-05"]);
});

test("every generated date really is that weekday", () => {
  const dates = generate("2026-07-07", 2, 13);
  assert.equal(dates.length, 13);
  assert.equal(dates[12], "2026-09-29");
  const weekdays = new Set(dates.map((d) => new Date(d + "T00:00:00").getDay()));
  assert.deepEqual([...weekdays], [2]);
});

test("a date describes itself with its weekday", () => {
  const info = describeDate("2026-09-08");
  assert.equal(info.month, 9);
  assert.equal(info.day, "8");
  assert.equal(info.weekday, "週二");
});
