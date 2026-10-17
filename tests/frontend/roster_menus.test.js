// Tapping a name opens a menu of what can be done to that person
// (2026-10-17). The rows used to carry two-character buttons that could
// not say what they did — 取消 beside a 代打 note, and 銷假, a word
// nobody uses — and 請假並指定代打 is one step instead of two.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeElement } = require("./harness.js");

const { applyAbsenceWithSubstituteLocally, renderGameDetail } = load("member.html");

function sheet(gameOverrides = {}) {
  const game = {
    id: 9,
    date: "2031-08-19",
    status: "scheduled",
    locked: false,
    past_deadline: false,
    share: "235",
    absences: [],
    confirmed_drop_ins: [],
    waitlist_entries: [],
    ...gameOverrides,
  };
  const season = {
    id: 1,
    capacity: 3,
    settled_at: null,
    members: [
      { id: 1, name: "Alice", gender: "female" },
      { id: 2, name: "Bob", gender: "male" },
    ],
    games: [game],
  };
  return { season, game };
}

function render(game, season, options) {
  const el = makeElement();
  renderGameDetail(el, season, game, options || {});
  return el.innerHTML;
}

test("a row with something to do opens a menu, marked by a large chevron", () => {
  const { season, game } = sheet();

  const html = render(game, season, { onRecordAbsence() {} });

  assert.match(html, /data-row-acts="a0"[\s\S]*?class="att-go"/);
  assert.match(html, /data-act-sheet="a0"[\s\S]*?登記請假/);
});

test("a sheet with nothing to change has no menus and no chevrons", () => {
  const { season, game } = sheet();

  const html = render(game, season, {});

  assert.doesNotMatch(html, /data-row-acts/);
  assert.doesNotMatch(html, /att-go/);
});

test("the member screen offers leave only on the viewer's own row", () => {
  const { season, game } = sheet();

  const html = render(game, season, {
    onRecordAbsence() {},
    canRecordAbsence: (m) => m.name === "Bob",
  });

  assert.match(html, /data-mark-absent="Bob"/);
  assert.doesNotMatch(html, /data-mark-absent="Alice"/);
});

test("登記請假 says who comes off the queue for the slot", () => {
  const { season, game } = sheet({
    waitlist_entries: [{ id: 300, player_name: "Carol", gender: "female" }],
  });

  const html = render(game, season, { onRecordAbsence() {} });

  assert.match(html, /空出的位置由候補 Carol 遞補/);
});

test("取消請假 replaces 銷假, and says who goes back to the queue", () => {
  const { season, game } = sheet({
    absences: [{ id: 5, player_name: "Alice", covered_by: "Dave", filled_by: "Dave" }],
    confirmed_drop_ins: [
      { id: 200, player_id: 9, player_name: "Dave", covering: "Alice", linked: false },
    ],
  });

  const html = render(game, season, {
    onCancelAbsence() {},
    onAssignSubstitute() {},
    onCancelSubstitute() {},
  });

  assert.match(html, /data-undo-absence="5"/);
  assert.match(html, /取消請假/);
  assert.match(html, /恢復出席；Dave 回到候補/);
  assert.match(html, /data-cancel-sub="200"[\s\S]*?取消代打/);
  assert.match(html, /更換代打/);
  assert.doesNotMatch(html, /銷假/);
});

test("請假並指定代打 is offered only where the page can do it in one step", () => {
  const { season, game } = sheet();
  const base = { onRecordAbsence() {}, onAssignSubstitute() {} };

  const offered = render(game, season, { ...base, onRecordAbsenceWithSubstitute() {} });
  const without = render(game, season, base);

  assert.match(offered, /data-absent-with-sub="Bob"[\s\S]*?請假並指定代打/);
  assert.doesNotMatch(without, /data-absent-with-sub/);
});

test("confirming 請假並指定代打 sends who is away and who covers", () => {
  const { season, game } = sheet();
  const calls = [];
  const el = makeElement();
  renderGameDetail(el, season, game, {
    onRecordAbsence() {},
    onAssignSubstitute: () => calls.push("assign"),
    onRecordAbsenceWithSubstitute: (...args) => calls.push(args),
  });
  el.querySelector = (s) =>
    s === '[data-sub-name="new"]'
      ? { value: " Dave " }
      : s === '[data-sub-gender="new"]'
        ? { value: "male" }
        : null;
  const confirm = { dataset: { confirmSub: "new", confirmAbsent: "Bob" } };

  el.onclick({ target: { closest: (s) => (s === "[data-confirm-sub]" ? confirm : null) } });

  assert.deepEqual(calls, [["Bob", "Dave", "male", confirm]]);
});

test("a locked night offers no menus on the member screen", () => {
  const { season, game } = sheet({ past_deadline: true });

  const html = render(game, season, {
    asMember: true,
    onRecordAbsence() {},
    onRecordAbsenceWithSubstitute() {},
    onAssignSubstitute() {},
  });

  assert.doesNotMatch(html, /data-row-acts/);
});

test("the organizer's sheet names the game in one line, not a second card", () => {
  const { season, game } = sheet();

  const html = render(game, season, { compactHead: true });

  assert.match(html, /class="gd-head"/);
  assert.match(html, /每人 <strong>\$235<\/strong>/);
  assert.doesNotMatch(html, /class="hero"/);
});

test("請假並指定代打 is drawn with nobody taken off the queue", () => {
  const { season, game } = sheet({
    waitlist_entries: [
      { id: 300, player_id: 7, player_name: "Carol" },
      { id: 301, player_id: 8, player_name: "Erin" },
    ],
  });

  applyAbsenceWithSubstituteLocally(
    season,
    game.id,
    "Bob",
    { id: 55, substitute: { id: 301, player_id: 8, displaced_player_id: null } },
    { name: "Erin", gender: null }
  );

  assert.deepEqual(game.absences, [
    { id: 55, player_name: "Bob", covered_by: "Erin", filled_by: "Erin" },
  ]);
  assert.deepEqual(
    game.confirmed_drop_ins.map((d) => [d.player_name, d.covering]),
    [["Erin", "Bob"]]
  );
  assert.deepEqual(
    game.waitlist_entries.map((w) => w.player_name),
    ["Carol"],
    "Carol keeps her place"
  );
});
