// The developer's page for reading problem reports.
//
// Reports used to be pushed over LINE, spending one or two of the free
// tier's 200 monthly messages each. They are stored and read here now.
// The thing this page must get right is that every word on it came from
// somebody else — typed into a box, or sent by their browser — and the
// one person reading it is the developer.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

function report(overrides) {
  return {
    id: "tok123",
    message: "帳務頁的金額不對",
    reporter: "Alice",
    club: "晴光館",
    page: "organizer-ledger.html",
    user_agent: "iPhone LINE",
    created_at: "2026-09-15T01:00:00",
    has_screenshot: false,
    read: false,
    ...overrides,
  };
}

test("a reporter's words are shown as text, never as markup", () => {
  // Drawn as markup, a report would be a way to run script in the one
  // browser that can read everybody's reports.
  const { reportHtml } = load("reports.html");

  const html = reportHtml(
    report({
      message: '<img src=x onerror="alert(1)">',
      reporter: "<b>Mallory</b>",
      club: "<script>steal()</script>",
      page: '"><svg onload=alert(2)>',
      user_agent: "<i>ua</i>",
    })
  );

  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<svg/);
  assert.doesNotMatch(html, /<b>Mallory/);
  assert.match(html, /&lt;img src=x/, "still readable, as text");
});

test("an unread report is marked, and a read one is not", () => {
  const { reportHtml } = load("reports.html");

  assert.match(reportHtml(report({ read: false })), /未讀/);
  assert.doesNotMatch(reportHtml(report({ read: true })), /未讀/);
});

test("a screenshot is drawn only when there is one", () => {
  const { reportHtml } = load("reports.html");

  assert.match(reportHtml(report({ has_screenshot: true })), /data-shot="tok123"/);
  assert.doesNotMatch(reportHtml(report({ has_screenshot: false })), /<img/);
});

test("a screenshot is never pointed at by a public address", () => {
  // It can show somebody's name and balance. It was public only because
  // LINE's servers had to fetch it, and they no longer do; now it is
  // fetched with the developer's credentials and shown as a local blob.
  const { reportHtml } = load("reports.html");

  const html = reportHtml(report({ has_screenshot: true }));

  assert.doesNotMatch(html, /src="[^"]*\/reports\//);
  assert.doesNotMatch(html, /href="[^"]*\/reports\//);
});

test("the time is Taiwan's, whatever the device is set to", () => {
  // The server stores UTC and doesn't say so. 01:00 UTC is 09:00 in
  // Taipei, which is what the person who sent it saw on their clock.
  const { localTime } = load("reports.html");

  const shown = localTime("2026-09-15T01:00:00");

  assert.match(shown, /9\/15/);
  assert.match(shown, /09:00/);
});
