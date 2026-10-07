// LINE profile pictures in place of initials, and initials that survive
// names starting with an emoji (2026-10-07).

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeElement } = require("./harness.js");

const { firstGrapheme, avatarHtml, renderGameDetail } = load("member.html");

test("a name starting with an emoji keeps the whole emoji", () => {
  // "🐱".slice(0, 1) is half a surrogate pair — a broken box on screen.
  assert.equal(firstGrapheme("🐱貓"), "🐱");
  assert.equal(firstGrapheme("👍🏽好"), "👍🏽", "skin tone stays attached");
  assert.equal(firstGrapheme("  周安 "), "周");
  assert.equal(firstGrapheme(""), "?");
});

test("no picture is the initial, as before", () => {
  const html = avatarHtml("周安", null);

  assert.equal(html, '<span class="avatar sm">周</span>');
});

test("a picture sits over the initial, which shows if it fails to load", () => {
  const html = avatarHtml("周安", "https://profile.line-scdn.net/abc");

  assert.match(html, /class="avatar sm has-photo">周<img src="https:\/\/profile\.line-scdn\.net\/abc"/);
  assert.match(html, /onerror="this\.remove\(\)"/);
  assert.match(html, /loading="lazy"/);
});

test("the address is escaped — it comes from the client", () => {
  const html = avatarHtml("周安", 'x" onload="alert(1)');

  assert.doesNotMatch(html, /onload="alert/);
});

test("the game sheet shows a member's picture on their row", () => {
  const season = {
    id: 1,
    capacity: 18,
    settled_at: null,
    share_per_game: "235",
    members: [{ id: 1, name: "周安", gender: "male", linked: true, avatar_url: "https://p/1" }],
    games: [],
  };
  const game = {
    id: 1, date: "2026-10-13", status: "scheduled", locked: false, share: "235",
    absences: [], confirmed_drop_ins: [
      { id: 9, player_id: 2, player_name: "🐱", gender: "female", covering: null, avatar_url: null },
    ],
    waitlist_entries: [],
  };
  season.games = [game];
  const el = makeElement();

  renderGameDetail(el, season, game, {});

  assert.match(el.innerHTML, /<img src="https:\/\/p\/1"/);
  assert.match(el.innerHTML, /class="avatar sm">🐱</, "an emoji name keeps its emoji");
});
