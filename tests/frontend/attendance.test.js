// The game sheet's job is to answer "who is actually playing", which is
// members-minus-absences-plus-drop-ins. Getting that wrong on the day
// means turning up short, so the headline count and the list under it
// have to agree with each other and with the hero card.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeElement } = require("./harness.js");

const { renderGameDetail } = load();

function fixture() {
  return {
    season: {
      id: 1,
      capacity: 18,
      share_per_game: "235",
      game_start_time: "18:30:00",
      game_end_time: "22:00:00",
      location: "啪排郎",
      change_deadline_days: 1,
      members: [
        { id: 1, name: "蘇慬", gender: "male", linked: true },
        { id: 2, name: "楊于嫺", gender: "female", linked: true },
        { id: 3, name: "莊", gender: "male", linked: true },
        { id: 4, name: "阿May", gender: "female", linked: false },
      ],
    },
    game: {
      id: 10,
      date: "2026-09-15",
      status: "scheduled",
      locked: false,
      absences: [
        { id: 100, player_name: "楊于嫺", covered_by: "Ricky" },
        { id: 101, player_name: "莊", covered_by: null },
      ],
      confirmed_drop_ins: [
        {
          id: 200,
          player_id: 9,
          player_name: "Ricky",
          gender: "male",
          covering: "楊于嫺",
          linked: true,
        },
        {
          id: 201,
          player_id: 8,
          player_name: "Taco",
          gender: "male",
          covering: null,
          linked: false,
        },
      ],
      waitlist_entries: [{ id: 300, player_name: "辭瑄", gender: "female" }],
    },
  };
}

function render(options = {}) {
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, { viewerName: "蘇慬", ...options });
  return el.innerHTML;
}

test("counts the people who will actually be there", () => {
  // 4 members - 2 absent + 2 drop-ins = 4
  const html = render();
  assert.match(html, /出席名單（4 人）/);
});

test("the hero's headcount matches the list beneath it", () => {
  // These are computed separately; if they ever disagree the screen is
  // lying to whoever is deciding whether to find another player.
  const html = render();
  assert.match(html, /class="count-big">4</);
});

test("a substitute is listed as playing, in the absent member's place", () => {
  const html = render();
  assert.match(html, /Ricky/);
  assert.match(html, /代 楊于嫺/);
});

test("an uncovered absence is called out as a gap", () => {
  const html = render();
  assert.match(html, /缺額/);
  assert.match(html, /請假（2 人）/);
});

test("someone with no account is marked, wherever they appear", () => {
  // They can't record their own absence, so the organizer needs to know
  // to do it for them.
  const html = render();
  assert.match(html, /訪客/);
});

test("the waitlist keeps its order and marks the viewer", () => {
  const { season, game } = fixture();
  game.waitlist_entries = [
    { id: 300, player_name: "辭瑄", gender: "female" },
    { id: 301, player_name: "蘇慬", gender: "male" },
  ];
  const el = makeElement();
  renderGameDetail(el, season, game, { viewerName: "蘇慬" });
  assert.match(el.innerHTML, /候補（2 人）/);
  assert.match(el.innerHTML, /（你）/);
});

test("editing controls appear only for a caller that can edit", () => {
  const readOnly = render();
  assert.doesNotMatch(readOnly, /data-mark-absent/);
  assert.doesNotMatch(readOnly, /data-add-dropin/);

  const editable = render({
    onRecordAbsence() {},
    onCancelAbsence() {},
    onRemoveDropIn() {},
    onAddDropIn() {},
  });
  assert.match(editable, /data-mark-absent/);
  assert.match(editable, /data-add-dropin/);
  assert.match(editable, /data-remove-drop-in/);
});

test("names are escaped — they come from LINE profiles", () => {
  const { season, game } = fixture();
  season.members.push({
    id: 5,
    name: '<img src=x onerror=alert(1)>',
    gender: null,
    linked: true,
  });
  const el = makeElement();
  renderGameDetail(el, season, game, {});
  assert.doesNotMatch(el.innerHTML, /<img src=x/);
});

test("a game with nobody in it says so rather than showing an empty box", () => {
  const { season, game } = fixture();
  season.members = [];
  game.absences = [];
  game.confirmed_drop_ins = [];
  const el = makeElement();
  renderGameDetail(el, season, game, {});
  assert.match(el.innerHTML, /沒有人出席/);
});

test("controls come before the lists, never after them", () => {
  // The bug this locks out: every control rendered after the attendance,
  // absence and waitlist lists. On a full roster that's most of a screen
  // of names before you reach the button you opened the sheet to press,
  // so nobody found them. Scrolling must only ever reveal more names.
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    extraHtml: '<button id="my-action">請假</button>',
    onAddDropIn() {},
    onRecordAbsence() {},
    onCancelAbsence() {},
    onRemoveDropIn() {},
  });

  const html = el.innerHTML;
  const action = html.indexOf("my-action");
  const addDropIn = html.indexOf("data-add-dropin");
  const roster = html.indexOf("出席名單");

  assert.ok(action >= 0 && addDropIn >= 0 && roster >= 0, "all three rendered");
  assert.ok(action < roster, "my own action is above the roster");
  assert.ok(addDropIn < roster, "the organizer's 新增臨打 is above the roster too");
});

test("the queue is a roster too, and removable through its own route", () => {
  // Two bugs at once. The rows were borderless text beside eighteen
  // bordered cards, so a queue of three read as nothing and got
  // reported as missing. And the remove control routed through the
  // drop-in handler, whose ids are a separate sequence — pressing it
  // could cancel a different person's confirmed signup.
  const { season, game } = fixture();
  const el = makeElement();
  const removed = { dropIn: [], waitlist: [] };
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onRemoveDropIn: (id) => removed.dropIn.push(id),
    onLeaveWaitlist: (id) => removed.waitlist.push(id),
  });

  assert.match(el.innerHTML, /att-row queued/, "same row shape as the attendance list");
  assert.doesNotMatch(el.innerHTML, /wl-row/);
  assert.match(el.innerHTML, /data-remove-waitlist="300"/);
  assert.doesNotMatch(
    el.innerHTML,
    /data-remove-drop-in="300"/,
    "a queue id must never reach the drop-in handler"
  );
});

test("without a waitlist handler the queue shows no remove button", () => {
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, { viewerName: "蘇慬", onRemoveDropIn() {} });
  assert.doesNotMatch(el.innerHTML, /data-remove-waitlist/);
});
