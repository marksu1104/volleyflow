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
  assert.match(html, /data-gd-tab="attending"[^>]*>出席 <b>4<\/b>/);
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
  assert.match(html, /data-gd-tab="absent"[^>]*>請假 <b>2<\/b>/);
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
  assert.match(el.innerHTML, /data-gd-tab="queued"[^>]*>候補 <b>2<\/b>/);
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
  assert.match(el.innerHTML, /尚無出席名單/);
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
  const lists = html.indexOf('class="gd-tabs"');

  assert.ok(action >= 0 && addDropIn >= 0 && lists >= 0, "all three rendered");
  assert.ok(action < lists, "my own action is above the names");
  assert.ok(addDropIn < lists, "the organizer's 新增臨打 is above them too");
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

// The three groups are tabs, so the counts are always on screen and no
// group is buried under the eighteen names of another. Reported as "I
// can't see in one page who's away, who's covering, who's queued".
test("the tab strip carries all three counts at once", () => {
  const { season, game } = fixture();
  const el = makeElement();

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  // Two members still playing, two drop-ins standing in, two away, one
  // queued — the sum the sheet exists to do for you.
  assert.match(el.innerHTML, /data-gd-tab="attending"[^>]*>出席 <b>4<\/b>/);
  assert.match(el.innerHTML, /data-gd-tab="absent"[^>]*>請假 <b>2<\/b>/);
  assert.match(el.innerHTML, /data-gd-tab="queued"[^>]*>候補 <b>1<\/b>/);
});

test("a game with no price yet says nothing rather than $undefined", () => {
  // Seen in a rendered sheet: the fixture had no share and the hero
  // printed "每人 $undefined" straight at the reader.
  const { season, game } = fixture();
  const el = makeElement();
  delete game.share;

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  assert.doesNotMatch(el.innerHTML, /undefined/);
  assert.doesNotMatch(el.innerHTML, /每人 <strong>\$<\/strong>/);
});

test("a group with nobody in it still gets a tab, showing zero", () => {
  // "我根本沒看到遞補的按鈕" — the queue section only existed once
  // somebody was in it, so the whole feature was invisible.
  const { season } = fixture();
  const el = makeElement();
  const quiet = {
    id: 1, date: "2026-10-06", status: "scheduled", locked: false,
    absences: [], confirmed_drop_ins: [], waitlist_entries: [],
  };

  renderGameDetail(el, season, quiet, { viewerName: "蘇慬" });

  assert.match(el.innerHTML, /data-gd-tab="queued"[^>]*>候補 <b>0<\/b>/);
  assert.match(el.innerHTML, /無人候補/);
  assert.match(el.innerHTML, /無人請假/);
});

test("only the chosen group is shown", () => {
  const { season, game } = fixture();
  const el = makeElement();

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  assert.match(el.innerHTML, /data-gd-panel="attending"(?![^>]*hidden)/);
  assert.match(el.innerHTML, /data-gd-panel="absent"[^>]*hidden/);
  assert.match(el.innerHTML, /data-gd-panel="queued"[^>]*hidden/);
});

test("新增臨打 with no name signs nobody up", () => {
  // The other half — that it says why rather than ignoring the tap —
  // can't be seen from here: toast() resolves inside the loaded scope,
  // so a stub on globalThis never reaches it. tests/visual/smoke.js
  // presses this in a real browser and fails if nothing happens at all.
  const { season, game } = fixture();
  const el = makeElement();
  const added = [];
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onAddDropIn: (name) => added.push(name),
  });
  el.querySelector = () => ({ value: "   ", focus() {} });

  el.onclick({ target: { closest: (s) => (s === "[data-add-dropin]" ? {} : null) } });

  assert.deepEqual(added, [], "a blank name must never become a person");
});

test("a tap on a roster button is not swallowed by the tab strip", () => {
  // The bug, mine, found by clicking in a real browser: the container
  // remembers the open tab in a data attribute, and the tab handler
  // looked for a bare [data-gd-tab]. closest() therefore walked up from
  // any button in the sheet, hit the container, and every 請假 / 移除 /
  // 遞補 / 指定代打 tap was treated as a tab switch and did nothing.
  const { season, game } = fixture();
  const el = makeElement();
  const absences = [];
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onRecordAbsence: (name) => absences.push(name),
  });

  // The stored key must not be readable as one of the tab buttons.
  assert.equal(el.dataset.gdTab, undefined, "the container's own key is named apart");
  assert.equal(el.dataset.gdActiveTab, "attending");
  // And the markup a tab handler looks for is a button, not the container.
  assert.match(el.innerHTML, /<button[^>]*data-gd-tab="attending"/);
});

test("the chosen group survives a repaint", () => {
  // Every action repaints this sheet. Losing the tab each time is one
  // of the "it jumps around" complaints.
  const { season, game } = fixture();
  const el = makeElement();
  el.dataset.gdActiveTab = "queued";

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  assert.equal(el.dataset.gdActiveTab, "queued");
  assert.match(el.innerHTML, /data-gd-panel="queued"(?![^>]*hidden)/);
  assert.match(el.innerHTML, /data-gd-panel="attending"[^>]*hidden/);
});

test("a tab that no longer makes sense falls back to who's playing", () => {
  const { season, game } = fixture();
  const el = makeElement();
  el.dataset.gdActiveTab = "nonsense";

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  assert.equal(el.dataset.gdActiveTab, "attending");
});

test("only the organizer is offered 遞補 on a queued person", () => {
  // Choosing who comes off the queue overrides the queue's own order,
  // so it is the organizer's call and nobody else's — a member opening
  // the same sheet must not see the button at all.
  const { season, game } = fixture();
  const asMember = makeElement();
  const asOrganizer = makeElement();

  renderGameDetail(asMember, season, game, { viewerName: "蘇慬", onLeaveWaitlist() {} });
  renderGameDetail(asOrganizer, season, game, {
    viewerName: "蘇慬",
    onLeaveWaitlist() {},
    onPromoteFromWaitlist() {},
  });

  assert.doesNotMatch(asMember.innerHTML, /data-promote-waitlist/);
  assert.match(asOrganizer.innerHTML, /data-promote-waitlist="300"/);
});

// 代打 and 臨打 stopped being the same thing on 2026-09-10. A drop-in
// who signed themselves up covers nobody in particular — the FIFO match
// that refunds an absence is a money rule, not an arrangement between
// two people, and showing it as one told members a stranger was "their"
// substitute.
test("somebody who signed themselves up is 臨打, not anybody's 代打", () => {
  const { season, game } = fixture();
  game.confirmed_drop_ins = [
    { id: 200, player_id: 9, player_name: "Taco", gender: "male", covering: null, linked: false },
  ];
  game.absences = [
    { id: 100, player_name: "楊于嫺", covered_by: null, filled_by: "Taco" },
  ];
  const el = makeElement();

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  assert.match(el.innerHTML, /臨打/);
  assert.doesNotMatch(el.innerHTML, /代 楊于嫺/, "he agreed to no such thing");
  // Named, because "who took my slot" is the first thing asked — but
  // 已補上, not 代打: he was never asked to stand in for her.
  assert.match(el.innerHTML, /Taco 已補上/);
  assert.doesNotMatch(el.innerHTML, /Taco 代打/);
});

test("an absence nobody is filling is still a gap", () => {
  const { season, game } = fixture();
  game.confirmed_drop_ins = [];
  game.absences = [{ id: 100, player_name: "楊于嫺", covered_by: null, filled_by: null }];
  const el = makeElement();

  renderGameDetail(el, season, game, { viewerName: "蘇慬" });

  assert.match(el.innerHTML, /缺額/);
  assert.doesNotMatch(el.innerHTML, /已補上/);
});

test("a member is only offered the signups that are theirs to undo", () => {
  // The member screen is not the place to take somebody else off the
  // list. The server refuses it too — see tests/api/test_permissions.py.
  const { season, game } = fixture();
  game.confirmed_drop_ins = [
    { id: 200, player_id: 9, player_name: "我的客人", covering: null, signed_up_by_me: true },
    { id: 201, player_id: 8, player_name: "別人的客人", covering: null, signed_up_by_me: false },
  ];
  const el = makeElement();

  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onRemoveDropIn() {},
    canRemoveDropIn: (d) => d.signed_up_by_me === true,
  });

  assert.match(el.innerHTML, /data-remove-drop-in="200"/);
  assert.doesNotMatch(el.innerHTML, /data-remove-drop-in="201"/);
});

test("the management screen is offered all of them", () => {
  const { season, game } = fixture();
  const el = makeElement();

  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onRemoveDropIn() {},
    canRemoveDropIn: () => true,
  });

  assert.match(el.innerHTML, /data-remove-drop-in="200"/);
  assert.match(el.innerHTML, /data-remove-drop-in="201"/);
});

test("a tap is never swallowed while an earlier change is in flight", () => {
  // The first attempt at fixing the interleaving locked the buttons
  // until each change came back, and that is what "按了沒反應" was: the
  // screen had already updated optimistically, so the next tap looked
  // like it should work and silently did nothing. The person is never
  // blocked; the requests queue instead.
  const { season, game } = fixture();
  const el = makeElement();
  const calls = [];
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onRecordAbsence: (name) => {
      calls.push(name);
      return new Promise(() => {}); // never settles: still in flight
    },
  });
  const tap = () =>
    el.onclick({
      target: {
        closest: (s) =>
          s === "[data-mark-absent]" ? { dataset: { markAbsent: "阿May" } } : null,
      },
    });

  tap();
  tap();

  assert.equal(calls.length, 2, "both taps reached the page");
});

test("a full game offers who to swap out as tappable names", () => {
  // The first version put a numbered list in a prompt() and asked for a
  // digit. Same picker rows as everything else on this screen now.
  const { season, game } = fixture();
  season.capacity = 4; // 2 members playing + 2 drop-ins = full
  const el = makeElement();

  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onPromoteFromWaitlist() {},
  });

  assert.match(el.innerHTML, /data-swap-form="300"/);
  assert.match(el.innerHTML, /data-swap-in="300"[^>]*data-swap-out="200"/);
  assert.match(el.innerHTML, /data-swap-out="201"/);
  assert.doesNotMatch(el.innerHTML, /輸入.*編號/, "no numbers to type");
});

test("a game with room promotes straight away, with nobody to choose", () => {
  const { season, game } = fixture();
  season.capacity = 18; // plenty of room
  const el = makeElement();

  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onPromoteFromWaitlist() {},
  });

  assert.match(el.innerHTML, /data-promote-waitlist="300"/);
  assert.doesNotMatch(el.innerHTML, /data-swap-form/, "nothing to swap out");
});

test("遞補 and 移除 are separate ids on the same queued person", () => {
  // Both controls sit on one row, and the two tables have overlapping
  // id sequences — routing either through the other's handler acts on
  // whoever happens to hold that number.
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onLeaveWaitlist() {},
    onPromoteFromWaitlist() {},
    onRemoveDropIn() {},
  });

  assert.match(el.innerHTML, /data-promote-waitlist="300"/);
  assert.match(el.innerHTML, /data-remove-waitlist="300"/);
  assert.doesNotMatch(el.innerHTML, /data-remove-drop-in="300"/);
});

// acPill and isGameFull moved out of member.html so the organizer's
// sheet reports the same facts about the same game.
test("the air conditioning surcharge is per person, and only when it ran", () => {
  const { acPill } = load();
  const season = { ac_surcharge: "540", members: new Array(18) };

  assert.match(acPill(season, { air_conditioned: true }), /\+\$30/);
  assert.equal(acPill(season, { air_conditioned: false }), "");
  assert.equal(
    acPill({ ac_surcharge: "0", members: new Array(18) }, { air_conditioned: true }),
    "",
    "a season that doesn't charge for it says nothing"
  );
});

test("a game is full when the people expected reach the cap", () => {
  const { isGameFull } = load();
  const season = { members: new Array(18), capacity: 18 };
  const empty = { absences: [], confirmed_drop_ins: [] };

  assert.equal(isGameFull(season, empty), true);
  assert.equal(isGameFull(season, { absences: [{}], confirmed_drop_ins: [] }), false);
  assert.equal(
    isGameFull(season, { absences: [{}], confirmed_drop_ins: [{}] }),
    true,
    "a substitute fills the slot the absence opened"
  );
});

test("every kind of row is built from the same slots", () => {
  // The rows came out visibly different heights next to each other. The
  // cause was the note: as a flex child of the row it was blockified, so
  // its vertical padding counted toward the row's height, while the 訪客
  // tag inline in the same place cost nothing. Measured at 57px against
  // its neighbours' 46px, with the note sitting 9px off the controls.
  // It is inline inside the name now, and this pins the structure that
  // depends on.
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, {
    viewerName: "蘇慬",
    onRecordAbsence() {},
    onCancelAbsence() {},
    onRemoveDropIn() {},
    onLeaveWaitlist() {},
  });

  const rows = el.innerHTML.split('<div class="att-row').slice(1);
  assert.ok(rows.length >= 4, "member, drop-in, absent and queued rows all present");

  for (const row of rows) {
    const seg = row.split('<div class="att-row')[0];
    const kind = /^[^"]*/.exec(seg)[0].trim() || "member";
    for (const slot of ["att-num", "avatar sm", "att-name", "att-who", "roster-note"]) {
      assert.ok(seg.includes(`class="${slot}`), `${kind} row is missing ${slot}`);
    }
    const note = /class="att-note/.test(seg);
    if (note) {
      const noteAt = seg.indexOf('class="att-note');
      const nameEnd = seg.indexOf("</span>", seg.indexOf('class="roster-note'));
      assert.ok(
        noteAt < seg.indexOf('class="roster-note'),
        `${kind}: the note must sit inside the name, before the controls`
      );
      assert.ok(nameEnd > 0);
    }
  }
});

test("only the person's name gives way when a row runs out of room", () => {
  // Truncating the whole name element ate the 代打 note beside it and
  // left a blank pill, so the truncation is on the name alone.
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, {});
  assert.match(el.innerHTML, /<span class="att-who">/);
});

test("the absent list drops the 訪客 tag, which means nothing there", () => {
  // It says "can't record their own absence" — moot for someone already
  // absent, and it was the content that pushed the row past a phone's
  // width.
  const { season, game } = fixture();
  const el = makeElement();
  renderGameDetail(el, season, game, {});

  const absent = el.innerHTML.split('<div class="att-row absent')[1].split("</div>")[0];
  assert.doesNotMatch(absent, /guest-tag/);
  assert.match(el.innerHTML, /guest-tag/, "but it still appears on the attendance list");
});
