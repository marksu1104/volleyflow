// 設定, redesigned 2026-10-21: values on cards, edits in sheets, the
// season's dates as one grid shared with the wizard. These pin what the
// redesign must not lose — every existing control still reachable in the
// states where it was, and none offered where the server would refuse.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

function game(id, date, extra = {}) {
  return {
    id,
    date,
    status: "scheduled",
    air_conditioned: false,
    share: "205",
    venue_cost_delta: "0",
    start_time: null,
    location: null,
    ...extra,
  };
}

function season(overrides = {}) {
  return {
    id: 7,
    total_venue_cost: "51210",
    ac_surcharge: "540",
    share_per_game: "205",
    capacity: 18,
    minimum_roster: 12,
    game_start_time: "18:30:00",
    game_end_time: "22:00:00",
    location: "晴光館",
    change_deadline_hours: 24,
    settled_at: null,
    members: [{ id: 1, name: "甲" }, { id: 2, name: "乙" }],
    games: [
      game(1, "2020-09-15"),
      game(2, "2020-09-22", { air_conditioned: true, share: "235" }),
      game(3, "2031-09-29", { air_conditioned: true, share: "235" }),
      game(4, "2031-10-06"),
      game(5, "2031-10-13", { status: "cancelled_refunded", share: "205" }),
    ],
    ...overrides,
  };
}

/** An element as the page will find it — the harness creates them on
 * first lookup, so a test sets one up through the same door. */
const el = (id) => globalThis.document.getElementById(id);

/** The page with this season loaded the way the page loads it. */
async function loaded(s) {
  const api = load("organizer-settings.html");
  // The game sheet's time pickers are <select>s, read through .options.
  for (const id of ["g-start-h", "g-start-m", "g-end-h", "g-end-m"]) el(id).options = [];
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => s });
  await api.loadSettings(s.id);
  return api;
}

test("本季 shows both prices and a season fee without the refunded night", async () => {
  const api = await loaded(season());
  const tiles = api.elements["price-tiles"].innerHTML;

  assert.match(tiles, /一般場次[\s\S]*\$205/);
  assert.match(tiles, /冷氣場次[\s\S]*\$235/);
  // 205 + 235 + 235 + 205, and not the cancelled-and-refunded 205.
  assert.equal(api.elements["season-fee"].textContent, "$880");
});

test("a season with no air-conditioning charge shows one price", async () => {
  const api = await loaded(season({ ac_surcharge: "0" }));

  assert.match(api.elements["price-tiles"].innerHTML, /每人每場/);
  assert.doesNotMatch(api.elements["price-tiles"].innerHTML, /冷氣場次/);
});

test("a settled season keeps its rules editable but not its money", async () => {
  // The same lines the server draws: cost and the AC charge are locked,
  // capacity and the rest are not.
  const api = await loaded(season({ settled_at: "2031-12-01T00:00:00" }));

  assert.equal(api.elements["edit-cost"].hidden, true);
  assert.equal(api.elements["cost-locked-note"].hidden, false);
  assert.equal(api.elements["settle-row"].hidden, true);
  assert.equal(api.elements["settled-note"].hidden, false);
});

test("a game still to come can be edited and called off", async () => {
  const api = await loaded(season());

  api.openGameSheet(4);

  assert.equal(api.elements["game-edit-fields"].hidden, false);
  assert.equal(api.elements["game-save"].hidden, false);
  assert.equal(api.elements["game-cancel-zone"].hidden, false);
});

test("a game already played can be called off but not moved", async () => {
  const api = await loaded(season());

  api.openGameSheet(1);

  assert.equal(api.elements["game-edit-fields"].hidden, true);
  assert.equal(api.elements["game-save"].hidden, true);
  assert.equal(api.elements["game-cancel-zone"].hidden, false, "a night recorded wrongly is correctable");
  assert.match(api.elements["game-sheet-note"].textContent, /已結束/);
});

test("a cancelled game is only read", async () => {
  const api = await loaded(season());

  api.openGameSheet(5);

  assert.equal(api.elements["game-edit-fields"].hidden, true);
  assert.equal(api.elements["game-cancel-zone"].hidden, true);
  assert.match(api.elements["game-sheet-info"].innerHTML, /已取消・場地費不算/);
});

test("nothing in a settled season can be edited or called off", async () => {
  const api = await loaded(season({ settled_at: "2031-12-01T00:00:00" }));

  api.openGameSheet(4);

  assert.equal(api.elements["game-edit-fields"].hidden, true);
  assert.equal(api.elements["game-cancel-zone"].hidden, true);
});

test("the games list says what makes a night different", async () => {
  const s = season();
  s.games[3] = { ...s.games[3], venue_cost_delta: "600", location: "大同" };
  const api = await loaded(s);
  const html = api.elements["upcoming-games"].innerHTML;

  assert.match(html, /場地差額 \+\$600/);
  assert.match(html, /大同/);
  assert.match(html, /已取消・場地費不算/);
  assert.match(html, /ac-tag/, "the cooled night is marked");
});

/* ---- the wizard's dates step ---- */

function wizard(acCharge) {
  const api = load("organizer-settings.html");
  el("c-ac").value = acCharge;
  el("g-weekday").value = "2";
  el("g-start").value = "2031-09-01";
  el("g-weeks").value = "4";
  api.generateDates();
  return api;
}

test("the wizard's dates toggle the air conditioning", () => {
  const api = wizard("540");
  const html = api.elements["date-grid"].innerHTML;

  assert.equal((html.match(/data-wz-ac=/g) || []).length, 4);
  assert.doesNotMatch(html, /data-wz-del=/);
});

test("移除場次 turns a tap into removing the date, until 完成", () => {
  const api = wizard("540");

  api.toggleRemoving();
  assert.equal((api.elements["date-grid"].innerHTML.match(/data-wz-del=/g) || []).length, 4);
  assert.equal(api.elements["wz-remove"].textContent, "完成");

  api.removeDate("2031-09-02");
  assert.equal((api.elements["date-grid"].innerHTML.match(/data-wz-del=/g) || []).length, 3);

  api.toggleRemoving();
  assert.match(api.elements["date-grid"].innerHTML, /data-wz-ac=/);
});

test("with no air-conditioning charge a date has nothing to toggle", () => {
  const api = wizard("0");
  const html = api.elements["date-grid"].innerHTML;

  assert.doesNotMatch(html, /data-wz-ac=/);
  assert.equal(api.elements["wz-all-ac"].hidden, true);
});

test("全部開啟 cools every date and then offers 全部關閉", () => {
  const api = wizard("540");

  api.toggleAllAc();

  assert.equal((api.elements["date-grid"].innerHTML.match(/class="day-cell on/g) || []).length, 4);
  assert.equal(api.elements["wz-all-ac"].textContent, "全部關閉");
});

/* ---- the air conditioning drawn the same everywhere ---- */

test("the 含冷氣 pill carries the snowflake", () => {
  const { acPill } = load();
  const html = acPill(
    { ac_surcharge: "540", capacity: 18, share_per_game: "205", games: [] },
    { air_conditioned: true, share: "235" }
  );

  assert.match(html, /meta-pill ac/);
  assert.match(html, /<svg/);
  assert.match(html, /含冷氣 \+\$30/);
});
