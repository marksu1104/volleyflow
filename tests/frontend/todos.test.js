// 待辦: what the organizer still has to do, worked out from what the page
// already has. Most urgent first, and nothing at all when there's nothing.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const { buildTodos } = load();

function season(games, members = [{ id: 1, name: "周安" }, { id: 2, name: "林書妤" }]) {
  return { id: 1, capacity: 18, minimum_roster: 1, members, games };
}

function game(overrides) {
  return {
    id: 10,
    date: "2031-05-06",
    status: "scheduled",
    absences: [],
    confirmed_drop_ins: [],
    waitlist_entries: [],
    ...overrides,
  };
}

test("nothing waiting means no 待辦 at all", () => {
  assert.deepEqual(buildTodos(season([game({})]), [], []), []);
});

test("people waiting to be approved come first", () => {
  const todos = buildTodos(
    season([game({ absences: [{ id: 1, player_name: "林書妤", filled_by: null }] })]),
    [{ id: 9, name: "新朋友" }],
    [{ player_id: 1, balance: "-3055", season_total: "-3055" }]
  );

  assert.equal(todos[0].kind, "urgent");
  assert.match(todos[0].label, /1 位新成員等待核准/);
  assert.equal(todos[0].href, "organizer-members.html");
});

test("an absence nobody is covering names who is away, and opens that game", () => {
  const todos = buildTodos(
    season([game({ absences: [{ id: 1, player_name: "林書妤", filled_by: null }] })]),
    [],
    []
  );

  assert.equal(todos.length, 1);
  assert.match(todos[0].label, /林書妤請假，沒人代打/);
  assert.equal(todos[0].gameId, 10);
});

test("an absence somebody is covering is not a 待辦", () => {
  const todos = buildTodos(
    season([game({ absences: [{ id: 1, player_name: "林書妤", filled_by: "Momo" }] })]),
    [],
    []
  );

  assert.deepEqual(todos, []);
});

test("a game below the minimum says how many are on it", () => {
  const short = season([game({})]);
  short.minimum_roster = 12;

  const todos = buildTodos(short, [], []);

  assert.equal(todos[0].kind, "warn");
  assert.match(todos[0].label, /只有 2 人/);
  assert.match(todos[0].sub, /門檻 12 人/);
});

test("unpaid season fees are counted from the balances, and sit last", () => {
  const todos = buildTodos(
    season([game({})]),
    [],
    [
      { player_id: 1, balance: "-3055", season_total: "-3055" },
      { player_id: 2, balance: "0", season_total: "0" },
    ]
  );

  assert.equal(todos.length, 1);
  assert.equal(todos[0].kind, "calm");
  assert.match(todos[0].label, /季費未收 1 人/);
  assert.equal(todos[0].href, "organizer-ledger.html");
});

test("a fee belonging to another season is not this season's 待辦", () => {
  // Reported 2026-09-17, the same bug as the 帳務 page's. Balances are
  // fetched with ?season_id=, so season_total is the season on screen
  // while balance spans every season the club has ever run — including
  // ones booked for later, whose fees are already charged.
  const todos = buildTodos(
    season([game({})]),
    [],
    [
      { player_id: 1, balance: "-3055", season_total: "0" },
      { player_id: 2, balance: "0", season_total: "0" },
    ]
  );

  assert.deepEqual(todos, []);
});

test("not having added the Official Account is a 待辦 that offers the way in", () => {
  const todos = buildTodos(season([game({})]), [], [], false);

  assert.equal(todos.length, 1);
  assert.equal(todos[0].kind, "warn");
  assert.match(todos[0].label, /尚未加入官方帳號好友/);
  // The real address, not the constant compared to itself: this is what
  // somebody actually taps, and a typo in it is silent.
  assert.equal(todos[0].href, "https://line.me/R/ti/p/@363bopii");
});

test("already having added it is not a 待辦", () => {
  assert.deepEqual(buildTodos(season([game({})]), [], [], true), []);
});

test("not being able to ask LINE is not a 待辦 either", () => {
  // null is the server saying it could not check; undefined is a caller
  // that never asked. Neither is evidence the friend is missing, and
  // warning on either would fire at everybody the day LINE is slow.
  assert.deepEqual(buildTodos(season([game({})]), [], [], null), []);
  assert.deepEqual(buildTodos(season([game({})]), [], []), []);
});

test("a game already played is nobody's 待辦", () => {
  const past = season([game({ date: "2020-01-07", absences: [{ id: 1, player_name: "林書妤", filled_by: null }] })]);
  past.minimum_roster = 12;

  assert.deepEqual(buildTodos(past, [], []), []);
});
