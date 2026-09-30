// Which season a page opens on.
//
// The picker used to take whichever season the API listed first, and the
// API lists newest-created first — so booking next season in advance
// moved every page's default onto a season nobody had played yet. These
// pin the order of preference instead: being played now, then about to
// start, then most recently finished.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const { defaultSeasonId } = load();

/** `days` from today, as the API writes a date. Relative on purpose: a
 * written-down date would quietly become a past one and change which
 * branch these tests exercise. */
function day(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const season = (id, from, to) => ({
  id,
  first_game_date: day(from),
  last_game_date: day(to),
});

test("a season being played now wins over one booked ahead", () => {
  // The reported case: next season created later, so it comes first.
  const seasons = [season(2, 30, 120), season(1, -40, 40)];

  assert.equal(defaultSeasonId(seasons), 1);
});

test("today counts as inside a season on its first and last day", () => {
  assert.equal(defaultSeasonId([season(1, 0, 60)]), 1, "first day");
  assert.equal(defaultSeasonId([season(1, -60, 0)]), 1, "last day");
});

test("between seasons it looks forward, not back", () => {
  const seasons = [season(2, 10, 100), season(1, -100, -10)];

  assert.equal(defaultSeasonId(seasons), 2);
});

test("the nearest of several upcoming seasons", () => {
  const seasons = [season(3, 60, 120), season(2, 10, 50)];

  assert.equal(defaultSeasonId(seasons), 2);
});

test("with everything finished it opens the one that ended last", () => {
  // Where the money still is: a season can be over and unsettled.
  const seasons = [season(1, -200, -100), season(2, -90, -10)];

  assert.equal(defaultSeasonId(seasons), 2);
});

test("overlapping seasons open the one finishing soonest", () => {
  const seasons = [season(2, -10, 90), season(1, -50, 20)];

  assert.equal(defaultSeasonId(seasons), 1);
});
