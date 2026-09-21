const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { inlineScript } = require("./harness.js");

const pagePath = path.join(__dirname, "..", "..", "frontend", "organizer-members.html");
const html = fs.readFileSync(pagePath, "utf8");
const script = inlineScript("organizer-members.html");

function between(start, end) {
  const from = script.indexOf(start);
  const to = end ? script.indexOf(end, from + start.length) : script.length;
  assert.notEqual(from, -1, `missing ${start}`);
  assert.notEqual(to, -1, `missing ${end}`);
  return script.slice(from, to);
}

test("a roster correction updates its lists without reloading the page", () => {
  const directCorrections = [
    between("async function promoteFromPool", "function guestTag"),
    between("async function setGender", "async function linkToRosterEntry"),
    between("async function removeFromClub", "function canEditRoster"),
    between("async function addGuest", "async function removeMember"),
    between("async function removeMember"),
  ].join("\n");

  assert.doesNotMatch(directCorrections, /reloadCurrentPage\s*\(/);
  assert.match(directCorrections, /renderRosterLists|addToRosterLocally|addGuestToPoolLocally|returnToPoolLocally/);
});

test("fixed-roster rows move before their network request finishes", () => {
  const add = between("async function promoteFromPool", "function guestTag");
  const remove = between("async function removeMember");

  assert.ok(add.indexOf("addToRosterLocally(player)") < add.indexOf("await enqueueRequest"));
  assert.ok(remove.indexOf("returnToPoolLocally(member)") < remove.indexOf("await enqueueRequest"));
  assert.match(add, /rosterWrites\.has\(playerId\)/);
  assert.match(remove, /rosterWrites\.has\(playerId\)/);
});

test("adding a fixed member names any drop-in moved back to the waitlist", () => {
  const add = between("async function promoteFromPool", "function guestTag");

  assert.match(add, /added\.displaced_drop_ins/);
  assert.match(add, /已移至候補/);
  assert.match(add, /describeDate\(dropIn\.game_date\)/);
});

test("an empty pending list keeps the same subordinate container", () => {
  const requests = between("function renderRequests", "async function approveRequest");

  assert.match(requests, /request-tree empty/);
  assert.match(requests, /目前沒有待核准的申請/);
});

test("a matching pending LINE account is explicitly linked to the roster", () => {
  const approval = between("async function approveRequest", "async function declineRequest");

  assert.match(approval, /球隊名單已有/);
  assert.match(approval, /這個 LINE 帳號就是同一位成員嗎/);
  assert.match(approval, /line_player_id: person\.id/);
  assert.match(approval, /existing\.id.*\/link/s);
  assert.match(approval, /請先請申請者在個人資料使用可辨識的名稱/);
});

test("old generated number suffixes are included in duplicate review", () => {
  const duplicates = between("function renderDuplicates", "function keeperOf");

  assert.match(duplicates, /m\.status === "active"/);
  assert.match(duplicates, /const numbered = name\.match/);
  assert.match(duplicates, /exactNames\.has\(numbered\[1\]\)/);
});

test("guest entry uses the same name and gender select row as drop-in entry", () => {
  assert.match(html, /<div class="picker-manual">[\s\S]*id="new-member-name"[\s\S]*<select id="new-member-gender"/);
  assert.match(html, /<option value="male">男<\/option>/);
  assert.match(html, /<option value="female">女<\/option>/);
  assert.doesNotMatch(html, /data-guest-gender=/);
});
