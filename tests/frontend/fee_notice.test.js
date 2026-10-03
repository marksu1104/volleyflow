// The 繳費通知 card on the 季費 tab. A LINE message cannot be taken back,
// so the card must never offer a send that would reach somebody twice,
// ask for money nobody owes, or state a figure that isn't final yet.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const TODAY = "2026-10-01";

/** Alice owes, Bob has paid, Carol owes but has no LINE account. */
function setup(over = {}) {
  const api = load("organizer-ledger.html");
  api.applyBalances([
    { player_id: 1, balance: "-4230", season_total: "-4700", through_season: "-4230", season_fee_charged: "-4700" },
    { player_id: 2, balance: "0", season_total: "0", through_season: "0", season_fee_charged: "-4700" },
    { player_id: 3, balance: "-4700", season_total: "-4700", through_season: "-4700", season_fee_charged: "-4700" },
  ]);
  const season = {
    id: 9,
    previous_season_settled: true,
    games: [
      { id: 1, date: "2026-10-07", status: "scheduled", absences: [] },
      { id: 2, date: "2026-12-23", status: "scheduled", absences: [] },
    ],
    members: [
      { id: 1, name: "Alice", linked: true, fee_notice_sent_at: null },
      { id: 2, name: "Bob", linked: true, fee_notice_sent_at: null },
      { id: 3, name: "Carol", linked: false, fee_notice_sent_at: null },
    ],
    ...over,
  };
  return { api, season };
}

test("only people who owe and can receive it are sent it", () => {
  const { api, season } = setup();

  const state = api.feeNoticeState(season, TODAY);

  assert.equal(state.kind, "ready");
  assert.deepEqual(state.recipients.map((m) => m.name), ["Alice"]);
  assert.equal(state.withoutLine, 1, "Carol is counted so she can be told in person");
});

test("somebody already sent it is never sent it again", () => {
  const { api, season } = setup();
  season.members[0].fee_notice_sent_at = "2026-10-01T14:30:00";

  const state = api.feeNoticeState(season, TODAY);

  assert.equal(state.kind, "done", "nobody left to send to, so no button");
  assert.match(state.text, /已通知 <b>1<\/b> 人・10\/1 14:30/);
});

test("the button waits for the previous season to be settled", () => {
  const { api, season } = setup({ previous_season_settled: false });

  const state = api.feeNoticeState(season, TODAY);

  assert.equal(state.kind, "blocked");
  assert.equal(state.text, "上一季結算後可發送繳費通知。");
});

test("a season already over has no card at all", () => {
  const { api, season } = setup();

  assert.equal(api.feeNoticeState(season, "2026-12-24").kind, "none");
});

test("switching club or season clears the old figures and shows 載入中", () => {
  // Reported 2026-10-03: the previous season's money stayed on screen
  // until the new one arrived, so the switch looked like it hadn't taken.
  const api = load("organizer-ledger.html");
  api.elements["ledger-tabs"].hidden = false;

  api.showSwitching();

  assert.equal(api.elements["ledger-tabs"].hidden, true);
  assert.match(api.elements["no-season"].innerHTML, /載入中/);
});
