// Two things reported from real use on 2026-10-16:
//  * the organizer, on the member screen, could still take leave on a
//    night already played — `locked` is answered per caller and is never
//    true for them; the member screen now also locks on past_deadline;
//  * the organizer could not name an ordinary club member as somebody's
//    代打 — the picker offered only the queue and the guests they had
//    signed up before.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeElement } = require("./harness.js");

// renderGameDetail lives in shared.js; member.html is simply the page that
// loads cleanly in the harness.
const { renderGameDetail } = load("member.html");

function sheet(gameOverrides = {}) {
  const game = {
    id: 9,
    date: "2031-08-19",
    status: "scheduled",
    locked: false,
    past_deadline: false,
    share: "235",
    absences: [{ id: 5, player_name: "Alice", covered_by: null, filled_by: null }],
    confirmed_drop_ins: [],
    waitlist_entries: [],
    ...gameOverrides,
  };
  const season = {
    id: 1,
    capacity: 2,
    settled_at: null,
    share_per_game: "235",
    members: [
      { id: 1, name: "Alice", gender: "female" },
      { id: 2, name: "Bob", gender: "male" },
    ],
    games: [game],
  };
  return { season, game };
}

const club = [
  { id: 1, name: "Alice", gender: "female" },
  { id: 2, name: "Bob", gender: "male" },
  { id: 7, name: "Kevin", gender: "male" },
];

function render(game, season, options) {
  const el = makeElement();
  renderGameDetail(el, season, game, {
    onAssignSubstitute: () => {},
    canAssignSubstitute: () => true,
    ...options,
  });
  return el.innerHTML;
}

test("the member screen locks a night past its deadline, for the organizer too", () => {
  // locked is false: this caller is the organizer.
  const { season, game } = sheet({ locked: false, past_deadline: true });

  const asMember = render(game, season, { asMember: true });
  const asOrganizer = render(game, season, {});

  assert.match(asMember, /已過更動期限/);
  assert.doesNotMatch(asOrganizer, /已過更動期限/, "the management screen still corrects it");
});

test("on the management screen a 代打 can be any club member who isn't on the roster", () => {
  const { season, game } = sheet();

  const html = render(game, season, { clubMembers: club, substituteFromClub: true });

  assert.match(html, /data-pick-name="Kevin"/);
  assert.match(html, /球隊成員/);
  assert.doesNotMatch(html, /data-pick-name="Bob"/, "a fixed member is expected already");
  assert.doesNotMatch(html, /data-pick-name="Alice"/, "nobody covers their own absence");
});

test("the member screen still doesn't offer the whole club as a 代打", () => {
  // A member may not name somebody with their own account.
  const { season, game } = sheet();

  const html = render(game, season, { clubMembers: club });

  assert.doesNotMatch(html, /data-pick-name="Kevin"/);
});
