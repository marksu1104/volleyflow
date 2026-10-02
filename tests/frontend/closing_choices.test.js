// Deciding each member's money while settling, rather than afterwards.
//
// The subtle part is which figure a row shows. The settlement has not
// been written yet, so a member's balance does not include the refund it
// is about to credit them — someone owed exactly their refund would read
// as 已結清 and never be offered the payout. renderClosingChoices adds
// the pending refund in; these pin that.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

/** A club where Alice paid up front and is owed a refund, Bob still owes
 * his fee, and Carol is square. */
function setup() {
  const api = load("organizer-ledger.html");
  api.applyBalances([
    { player_id: 1, balance: "0", season_total: "0", season_fee_charged: "235" },
    { player_id: 2, balance: "-235", season_total: "-235", season_fee_charged: "235" },
    { player_id: 3, balance: "0", season_total: "0", season_fee_charged: "235" },
  ]);
  const season = {
    id: 5,
    settled_at: null,
    members: [
      { id: 1, name: "Alice", gender: "female" },
      { id: 2, name: "Bob", gender: "male" },
      { id: 3, name: "Carol", gender: "female" },
    ],
  };
  const preview = {
    members: [
      { player_id: 1, player_name: "Alice", refund: "235", net: "235" },
      { player_id: 2, player_name: "Bob", refund: "0", net: "-235" },
      { player_id: 3, player_name: "Carol", refund: "0", net: "0" },
    ],
  };
  api.renderClosingChoices(season, preview);
  return { api, list: api.elements["closing-preview"], cash: api.elements["closing-cash"] };
}

test("a member owed only the refund about to be credited is still offered it", () => {
  // Alice's balance is 0 right now; the refund is what makes her owed.
  const { list } = setup();

  assert.match(list.innerHTML, /Alice/);
  assert.match(list.innerHTML, /應退 \$235/);
});

test("somebody who owes is offered 已收, somebody owed is offered 退款", () => {
  const { list } = setup();

  assert.match(list.innerHTML, /應收 \$235/, "Bob owes his fee");
  assert.match(list.innerHTML, /退款/);
  assert.match(list.innerHTML, /已收/);
});

test("a settled-up member is left out entirely", () => {
  const { list } = setup();

  assert.doesNotMatch(list.innerHTML, /Carol/, "nothing to decide for a zero balance");
});

test("every row starts on 保留至下一季", () => {
  const { list } = setup();

  const kept = list.innerHTML.match(/aria-checked="true"\s+class="selected"\s+onclick="pickClosing\(\d, 'keep'\)"/g);
  assert.equal((kept || []).length, 2);
});

test("a choice here looks like a choice, not like 已收 on the 季費 tab", () => {
  // Asked for on 2026-10-02: 已收 on 季費 records the moment it is
  // pressed; these record only when 結算此季 is. They used the same
  // solid green, so a picked option read as money already booked.
  const { list } = setup();

  assert.doesNotMatch(list.innerHTML, /class="paid"/);
  assert.match(list.innerHTML, /class="seg-pick"/);
});

test("the line under the list says what settling will write", () => {
  const { api } = setup();
  const pending = api.elements["closing-pending"];

  assert.equal(pending.textContent, "結算時記錄：現金收付 0 筆，保留 2 筆。");
  api.pickClosing(2, "cash");
  assert.equal(pending.textContent, "結算時記錄：現金收付 1 筆，保留 1 筆。");
});

function seasonEnding(lastDate, games = []) {
  return {
    games: [
      ...games,
      { id: 99, date: lastDate, status: "scheduled", absences: [] },
    ],
  };
}

test("settling opens three weeks before the last game", () => {
  const { settleBlocker } = load("organizer-ledger.html");
  const season = seasonEnding("2026-12-23");

  assert.equal(settleBlocker(season, "2026-12-01"), "12/2 起可結算（最後一場前 3 週）。");
  assert.equal(settleBlocker(season, "2026-12-02"), null);
});

test("an open slot still to come holds it back; one already played does not", () => {
  const { settleBlocker } = load("organizer-ledger.html");
  const gap = [{ id: 1, player_name: "Alice", filled_by: null }];
  const filled = [{ id: 2, player_name: "Bob", filled_by: "Carol" }];

  const ahead = seasonEnding("2026-12-23", [
    { id: 1, date: "2026-12-09", status: "scheduled", absences: gap },
  ]);
  const played = seasonEnding("2026-12-23", [
    { id: 1, date: "2026-11-25", status: "scheduled", absences: gap },
    { id: 2, date: "2026-12-09", status: "scheduled", absences: filled },
  ]);

  assert.equal(settleBlocker(ahead, "2026-12-05"), "12/9 尚有缺額，補齊後可結算。");
  assert.equal(settleBlocker(played, "2026-12-05"), null);
});

test("the cash figure is money in less money out", () => {
  const { api, cash } = setup();

  assert.equal(cash.textContent, "—", "nothing decided yet");

  api.pickClosing(2, "cash"); // Bob hands over $235
  assert.equal(cash.textContent, "+$235");

  api.pickClosing(1, "cash"); // Alice is handed $235 back
  assert.equal(cash.textContent, "—".replace("—", "$0"));

  api.pickClosing(2, "keep");
  assert.equal(cash.textContent, "−$235", "only the payout is left");
});
