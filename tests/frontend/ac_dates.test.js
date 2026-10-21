// 冷氣場次 on the settings page: correcting the forecast made when the
// season was set up.
//
// Worth testing away from a browser because the block is invisible most
// of the time — it only appears when the season prices air conditioning
// at all — and because a settled season, or a game already played, must
// not offer it. Both are the kind of condition that looks right in the
// markup and is wrong in the one state nobody opens by hand.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

function season(overrides = {}) {
  return {
    id: 7,
    ac_surcharge: "630",
    settled_at: null,
    games: [
      { id: 1, date: "2020-10-06", air_conditioned: true, status: "scheduled" },
      { id: 2, date: "2020-10-13", air_conditioned: false, status: "scheduled" },
      { id: 3, date: "2031-10-20", air_conditioned: true, status: "scheduled" },
      { id: 4, date: "2031-10-27", air_conditioned: false, status: "scheduled" },
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
    grid: api.elements["ac-grid"],
    sum: api.elements["ac-sum"],
    open: api.elements["forecast-open"],
  };
}

const cells = (html) => (html.match(/class="day-cell/g) || []).length;
const cooled = (html) => (html.match(/class="day-cell on/g) || []).length;

test("the block stays away when the season has no air-conditioning charge", () => {
  const { block } = render(season({ ac_surcharge: "0" }));

  assert.equal(block.hidden, true, "nothing to place when the venue doesn't charge for it");
});

test("it shows every game, with the cooled nights filled", () => {
  const { block, grid, sum } = render(season());

  assert.equal(block.hidden, false);
  assert.equal(cells(grid.innerHTML), 4, "every game is drawn, not only the cooled ones");
  assert.equal(cooled(grid.innerHTML), 2);
  assert.equal(sum.textContent, "預定 2 場・每場冷氣費用 $630");
});

test("the card itself changes nothing", () => {
  const { grid } = render(season());

  assert.doesNotMatch(grid.innerHTML, /data-fc=/, "corrections happen in the sheet");
});

test("a settled season offers no correction", () => {
  const { open } = render(season({ settled_at: "2026-11-01T00:00:00" }));

  assert.equal(open.hidden, true);
});

test("a season with every game played offers no correction", () => {
  const s = season();
  s.games = s.games.slice(0, 2);

  const { open } = render(s);

  assert.equal(open.hidden, true, "what happened on those nights is recorded on 總覽");
});

test("only games still to come can be toggled", () => {
  const { api } = render(season());

  api.openForecastSheet();

  const html = api.elements["forecast-grid"].innerHTML;
  assert.match(html, /data-fc="2031-10-20"/);
  assert.match(html, /data-fc="2031-10-27"/);
  assert.doesNotMatch(html, /data-fc="2020-10-06"/, "a played night is not a forecast");
  assert.doesNotMatch(html, /data-fc="2020-10-13"/);
});

test("toggling a night is local until it is saved", () => {
  // The whole reason this collects a set and puts it once: re-pricing on
  // every tap would write an adjustment entry per tap, and a
  // half-finished correction would be live in between.
  const { api } = render(season());
  api.openForecastSheet();

  api.toggleSeasonAcDate("2031-10-27");
  assert.equal(cooled(api.elements["forecast-grid"].innerHTML), 3);

  api.toggleSeasonAcDate("2031-10-20");
  assert.equal(cooled(api.elements["forecast-grid"].innerHTML), 2);
});

test("a cancelled game is drawn but cannot be toggled", () => {
  const s = season();
  s.games[3] = { ...s.games[3], status: "cancelled_refunded" };
  const { api } = render(s);

  api.openForecastSheet();

  assert.doesNotMatch(api.elements["forecast-grid"].innerHTML, /data-fc="2031-10-27"/);
});
