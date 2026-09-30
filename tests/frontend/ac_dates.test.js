// 冷氣場次 on the settings page: correcting the forecast made when the
// season was set up.
//
// Worth testing away from a browser because the block is invisible most
// of the time — it only appears when the season prices air conditioning
// at all — and because a settled season must not offer it. Both are the
// kind of condition that looks right in the markup and is wrong in the
// one state nobody opens by hand.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

function season(overrides = {}) {
  return {
    id: 7,
    ac_surcharge: "630",
    settled_at: null,
    games: [
      { id: 1, date: "2026-10-06", air_conditioned: true },
      { id: 2, date: "2026-10-13", air_conditioned: false },
      { id: 3, date: "2026-10-20", air_conditioned: true },
      { id: 4, date: "2026-10-27", air_conditioned: false },
    ],
    ...overrides,
  };
}

/** The page's own render, with the harness's stub elements behind it. */
function render(s) {
  const api = load("organizer-settings.html");
  api.renderAcDates(s);
  return {
    api,
    block: api.elements["ac-block"],
    dates: api.elements["ac-dates"],
    count: api.elements["ac-count"],
    save: api.elements["ac-save"],
  };
}

test("the block stays away when the season has no air-conditioning charge", () => {
  const { block } = render(season({ ac_surcharge: "0" }));

  assert.equal(block.hidden, true, "nothing to place when the venue doesn't charge for it");
});

test("it opens on the nights the season is already down for", () => {
  const { block, count, dates } = render(season());

  assert.equal(block.hidden, false);
  assert.equal(count.textContent, "2 場");
  assert.equal((dates.innerHTML.match(/chip-ac on/g) || []).length, 2);
  assert.equal(
    (dates.innerHTML.match(/class="chip"/g) || []).length,
    4,
    "every game is offered, not only the cooled ones"
  );
});

test("a settled season shows the nights but refuses to change them", () => {
  const { dates, save } = render(season({ settled_at: "2026-11-01T00:00:00" }));

  assert.equal(save.disabled, true);
  assert.equal((dates.innerHTML.match(/ disabled/g) || []).length, 4, "every toggle is dead");
});

test("toggling a night is local until it is saved", () => {
  // The whole reason this collects a set and posts once: re-pricing on
  // every tap would write an adjustment entry per tap, and a
  // half-finished correction would be live in between.
  const { api, count } = render(season());

  api.toggleSeasonAcDate("2026-10-13");
  assert.equal(count.textContent, "3 場");

  api.toggleSeasonAcDate("2026-10-06");
  assert.equal(count.textContent, "2 場");
});
