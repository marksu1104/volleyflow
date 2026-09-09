// The panel at the top of a game sheet: what *you* can do about this
// game. Everything else on that screen is information; this is the part
// people tap, so every state has to offer a way back out of itself.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const page = load("member.html");
const { myActions } = page;

/** The card renders these two pieces in two places — status above the
 * rule, buttons below — so a test that cares about both reads them as
 * one string. */
function buildMyActionHtml(season, game) {
  const mine = myActions(season, game);
  return mine.status + (mine.actions ? `<div class="hero-actions">${mine.actions}</div>` : "");
}

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

/** myActions reads the viewer's name out of the name field
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

test("signing people up is offered in every state", () => {
  // Not a state you're in — you can do it whether you're playing, on
  // leave, already covered, or not going at all.
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
    assert.match(buildMyActionHtml(season, game), /openSignup/);
  }
});

test("a non-member gets one control, not two that overlap", () => {
  // The signup sheet's first row is already you, so a separate
  // "sign myself up" button would just be a second way in.
  const { season, game } = fixture();
  as("Ricky"); // not on the roster

  const html = buildMyActionHtml(season, game);

  assert.equal(html.match(/openSignup/g).length, 1);
  assert.doesNotMatch(html, /＋1 報名/);
});

test("a full game offers the waitlist rather than pretending there's room", () => {
  const { season, game } = fixture({ season: { capacity: 2 } });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /候補/, "a full game must not look like it has room");
});

test("a game with room says 報名, not 候補", () => {
  const { season, game } = fixture({ season: { capacity: 18 } });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.doesNotMatch(html, /候補/);
});

test("a locked game offers nothing at all", () => {
  const { season, game } = fixture({ game: { locked: true } });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /已過更動期限/);
  assert.doesNotMatch(html, /openSignup/);
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

test("the button says who the signup is for", () => {
  // A fixed member is already on the sheet, so the panel is for the
  // people they're bringing — calling that 報名 reads as signing
  // themselves up a second time.
  const onRoster = fixture();
  as("蘇慬");
  assert.match(buildMyActionHtml(onRoster.season, onRoster.game), /帶朋友/);

  const guest = fixture();
  as("Ricky"); // not on the roster
  assert.match(buildMyActionHtml(guest.season, guest.game), /＋ 報名/);
});

test("every control in the row is the same kind of button", () => {
  // Three different widths and weights stacked under the card was the
  // complaint; they are one row of equal-weight buttons now.
  const { season, game } = fixture({
    game: { absences: [{ id: 1, player_name: "蘇慬", covered_by: null }] },
  });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.equal((html.match(/class="hact/g) || []).length, 3);
  assert.doesNotMatch(html, /class="btn |class="action/);
});

test("taking leave puts naming a substitute right there, not two taps away", () => {
  // 代打 and 報名 are different acts — a substitute takes *your* slot,
  // a signup queues for whatever slot is free — and burying 指定代打
  // inside 看名單 made it look as though it had been removed, leaving
  // 報名 as the only visible way to get someone into your place.
  const { season, game } = fixture({
    game: { absences: [{ id: 77, player_name: "蘇慬", covered_by: null }] },
  });
  as("蘇慬");

  const html = buildMyActionHtml(season, game);

  assert.match(html, /openSubstitutePicker\(77\)/);
  assert.match(html, /指定代打/);
});

test("an optimistic row's controls carry a usable id once the server answers", () => {
  // The bug this pins: a placeholder id of -1 goes into the button's
  // onclick, the server's real id then lands in the data, and the
  // refresh sees no difference and skips the redraw — leaving 取消請假
  // wired to -1, which its own guard treats as "still saving" and
  // ignores. The button must never be left holding the placeholder.
  const { season, game } = fixture({
    game: { absences: [{ id: -1, player_name: "蘇慬", covered_by: null }] },
  });
  as("蘇慬");

  const pending = buildMyActionHtml(season, game);
  assert.match(pending, /cancelAbsence\(-1,/, "the optimistic paint uses the placeholder");

  game.absences[0].id = 412; // what reconcile does
  const settled = buildMyActionHtml(season, game);

  assert.match(settled, /cancelAbsence\(412,/);
  assert.doesNotMatch(settled, /cancelAbsence\(-1,/);
});

// A member seeing 235 on one night and 205 on the next needs the reason
// beside the number, not somewhere else on the page.
const { acPill } = page;

test("a cooled night says what the extra is for", () => {
  const season = { ac_surcharge: "540", members: new Array(18) };
  const html = acPill(season, { air_conditioned: true });

  assert.match(html, /含冷氣/);
  assert.match(html, /\+\$30/, "540 shared between 18 people");
});

test("the share of the air conditioning follows the roster size", () => {
  // The venue charges the same whatever the turnout, so a smaller roster
  // pays more each — a figure hardcoded per person would drift.
  const html = acPill({ ac_surcharge: "540", members: new Array(12) }, { air_conditioned: true });
  assert.match(html, /\+\$45/);
});

test("a night with no air conditioning needs no explaining", () => {
  const season = { ac_surcharge: "540", members: new Array(18) };
  assert.equal(acPill(season, { air_conditioned: false }), "");
});

test("a club whose venue bundles it never sees any of this", () => {
  const season = { ac_surcharge: "0", members: new Array(18) };
  assert.equal(acPill(season, { air_conditioned: true }), "");
});
