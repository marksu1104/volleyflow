// The panel at the top of a game sheet: what *you* can do about this
// game. Everything else on that screen is information; this is the part
// people tap, so every state has to offer a way back out of itself.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const page = load("member.html");
const { buildMyActionHtml } = page;

function fixture(overrides = {}) {
  return {
    season: {
      id: 1,
      capacity: 18,
      members: [
        { id: 1, name: "蘇慬", gender: "male", linked: true },
        { id: 2, name: "楊于嫺", gender: "female", linked: true },
      ],
      ...overrides.season,
    },
    game: {
      id: 10,
      locked: false,
      absences: [],
      confirmed_drop_ins: [],
      waitlist_entries: [],
      ...overrides.game,
    },
  };
}

/** buildMyActionHtml reads the viewer's name out of the name field
 * rather than taking it as an argument, because playerName() is what
 * every other action on the page keys off too. The page's myClubs stays
 * empty here, which is the ordinary case: viewingOnly() only bites for
 * someone waiting on the organizer to approve them. */
function as(name) {
  page.elements["player-name"].value = name;
}

test("a member who took leave can undo it", () => {
  const { season, game } = fixture({
    game: { absences: [{ id: 100, player_name: "蘇慬", covered_by: null }] },
  });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /已請假這一場/);
  assert.match(html, /cancelAbsence\(100, this\)/);
});

test("a member whose leave is covered can cancel the substitute", () => {
  // The bug: this state showed a sentence and no buttons at all. The
  // server refuses to cancel leave someone is covering, so arranging a
  // 代打 was a one-way door — you could not undo either half of it.
  const { season, game } = fixture({
    game: {
      absences: [{ id: 100, player_name: "蘇慬", covered_by: "Ricky" }],
      confirmed_drop_ins: [
        { id: 200, player_name: "Ricky", covering: "蘇慬", gender: "male" },
      ],
    },
  });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /由 Ricky 代打/);
  assert.match(html, /取消代打/);
  assert.match(html, /cancelDropIn\(200, this\)/);
});

test("cancelling the substitute then leaves ordinary leave to cancel", () => {
  // The two-step the server's rule implies: drop the cover, then the
  // leave. This is the second step, and it has to be reachable.
  const { season, game } = fixture({
    game: { absences: [{ id: 100, player_name: "蘇慬", covered_by: null }] },
  });
  as("蘇慬");

  assert.match(buildMyActionHtml(season, game), /取消請假/);
});

test("everyone in the club can bring a friend, in every state", () => {
  // Signing a friend up is not a state you're in — it's something you
  // can do whether you're playing, on leave, or already covered.
  as("蘇慬");
  const states = [
    fixture(),
    fixture({ game: { absences: [{ id: 1, player_name: "蘇慬", covered_by: null }] } }),
    fixture({
      game: {
        absences: [{ id: 1, player_name: "蘇慬", covered_by: "Ricky" }],
        confirmed_drop_ins: [{ id: 2, player_name: "Ricky", covering: "蘇慬" }],
      },
    }),
  ];

  for (const { season, game } of states) {
    assert.match(buildMyActionHtml(season, game), /幫朋友報名/);
  }
});

test("a non-member sees both signing themselves up and bringing someone", () => {
  const { season, game } = fixture();
  as("Ricky"); // not on the roster

  const html = buildMyActionHtml(season, game);

  assert.match(html, /＋1 報名/);
  assert.match(html, /幫朋友報名/);
});

test("a full game offers the waitlist rather than pretending there's room", () => {
  const { season, game } = fixture({ season: { capacity: 2 } });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /幫朋友報名候補/);
  assert.match(html, /加入候補/);
});

test("a game with room says 報名, not 候補", () => {
  const { season, game } = fixture({ season: { capacity: 18 } });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /幫朋友報名(?!候補)/);
  assert.match(html, /確認報名/);
});

test("a locked game offers nothing at all", () => {
  const { season, game } = fixture({ game: { locked: true } });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /已過更動期限/);
  assert.doesNotMatch(html, /幫朋友報名/);
  assert.doesNotMatch(html, /請假/);
});

test("a substitute's name is escaped — it was typed by a person", () => {
  const { season, game } = fixture({
    game: {
      absences: [{ id: 100, player_name: "蘇慬", covered_by: '<img src=x onerror=alert(1)>' }],
      confirmed_drop_ins: [
        { id: 200, player_name: "x", covering: "蘇慬", gender: null },
      ],
    },
  });
  as("蘇慬");

  assert.doesNotMatch(buildMyActionHtml(season, game), /<img src=x/);
});
