// The calendar stays on the month you moved to. It used to jump back to
// the next game's month on every repaint, including the one a tap causes.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeElement } = require("./harness.js");

const games = [
  { id: 1, date: "2031-09-02" },
  { id: 2, date: "2031-10-07" },
];

function goToNextMonth(el) {
  el.onclick({
    target: { closest: (sel) => (sel === ".mcal-nav" ? { dataset: { dir: "1" } } : null) },
  });
}

test("picking a date in the next month keeps that month on screen", () => {
  const { renderMonthCalendar } = load();
  const el = makeElement();
  renderMonthCalendar(el, games, () => {}, { viewKey: 7 });
  goToNextMonth(el);

  renderMonthCalendar(el, games, () => {}, { viewKey: 7, selectedId: 2 });

  assert.match(el.innerHTML, /2031 年 10 月/);
});

test("a background repaint doesn't move the month either", () => {
  const { renderMonthCalendar } = load();
  const el = makeElement();
  renderMonthCalendar(el, games, () => {}, { viewKey: 7, selectedId: 1 });
  goToNextMonth(el);

  renderMonthCalendar(el, games, () => {}, { viewKey: 7, selectedId: 1 });

  assert.match(el.innerHTML, /2031 年 10 月/);
});

test("another season starts from its own next game", () => {
  const { renderMonthCalendar } = load();
  const el = makeElement();
  renderMonthCalendar(el, games, () => {}, { viewKey: 7 });
  goToNextMonth(el);

  renderMonthCalendar(el, games, () => {}, { viewKey: 8 });

  assert.match(el.innerHTML, /2031 年 9 月/);
});
