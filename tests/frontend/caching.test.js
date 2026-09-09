// Pages paint from the last thing this device saw and correct themselves
// when the network answers, because every navigation otherwise waits on
// a chain of requests that can only run one after another. That's only
// safe if the cache never outranks the server on anything irreversible,
// and if a failure with nothing cached still surfaces.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeSelect, makeElement } = require("./harness.js");

test("with nothing cached, the network's answer is what renders", async () => {
  const { getJsonSWR } = load();
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  const seen = [];
  await getJsonSWR("/x", (d) => seen.push(d));
  assert.deepEqual(seen, [{ v: 1 }]);
});

test("an unchanged answer doesn't repaint the screen a second time", async () => {
  const { getJsonSWR } = load();
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  await getJsonSWR("/x", () => {});
  const seen = [];
  await getJsonSWR("/x", (d) => seen.push(d));
  assert.equal(seen.length, 1, "cached copy only; the server agreed");
});

test("a changed answer paints the stale copy first, then the fresh one", async () => {
  const { getJsonSWR } = load();
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  await getJsonSWR("/x", () => {});
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 2 }) });
  const seen = [];
  await getJsonSWR("/x", (d) => seen.push(d));
  assert.deepEqual(seen, [{ v: 1 }, { v: 2 }]);
});

test("only the fresh copy is allowed to be authoritative", async () => {
  // The flag exists so that irreversible decisions — forgetting a
  // remembered club because there are none — key off the server's word
  // and not off a stale guess.
  const { getJsonSWR } = load();
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  await getJsonSWR("/x", () => {});
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 2 }) });
  const flags = [];
  await getJsonSWR("/x", (_d, meta) => flags.push(meta.fresh));
  assert.deepEqual(flags, [false, true]);
});

test("offline with a cached copy keeps working", async () => {
  const { getJsonSWR } = load();
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ v: 1 }) });
  await getJsonSWR("/x", () => {});
  globalThis.fetch = async () => {
    throw new Error("offline");
  };
  const seen = [];
  await assert.doesNotReject(() => getJsonSWR("/x", (d) => seen.push(d)));
  assert.deepEqual(seen, [{ v: 1 }]);
});

test("offline with nothing cached throws, so the page can say so", async () => {
  const { getJsonSWR } = load();
  globalThis.fetch = async () => {
    throw new Error("offline");
  };
  await assert.rejects(() => getJsonSWR("/never-seen", () => {}));
});

test("an HTTP error is never cached as if it were data", async () => {
  const { getJsonSWR, readCache } = load();
  globalThis.fetch = async () => ({ ok: false, statusText: "500", json: async () => ({}) });
  await assert.rejects(() => getJsonSWR("/boom", () => {}));
  assert.equal(readCache("/boom"), null);
});

test("a write clears cached reads without touching anything else", () => {
  const { writeCache, readCache, clearResponseCache } = load();
  writeCache("/a", { x: 1 });
  writeCache("/b", { y: 2 });
  localStorage.setItem("vf_club", "3");
  clearResponseCache();
  assert.equal(readCache("/a"), null);
  assert.equal(readCache("/b"), null);
  assert.equal(localStorage.getItem("vf_club"), "3");
});

test("a stale empty club list doesn't forget a club that still exists", async () => {
  // The bug this guards: painting from a cached "no clubs" and taking
  // that as licence to delete the remembered club and season, which the
  // server was about to contradict.
  const { initClubAndSeasonPickers, writeCache, currentClubId } = load();
  writeCache("http://x/clubs", []);
  localStorage.setItem("vf_club", "3");
  localStorage.setItem("vf_org_season", "9");
  globalThis.fetch = async (url) => ({
    ok: true,
    json: async () =>
      url.endsWith("/clubs")
        ? [{ id: 3, name: "測試" }]
        : [
            {
              id: 9,
              first_game_date: "2026-07-07",
              last_game_date: "2026-07-07",
              total_games: 1,
              member_count: 2,
              settled: false,
            },
          ],
  });

  await initClubAndSeasonPickers(
    "http://x",
    makeSelect(),
    makeSelect(),
    "vf_org_season",
    () => {},
    () => {}
  );

  assert.equal(currentClubId(), "3");
  assert.equal(localStorage.getItem("vf_org_season"), "9");
});

test("the server saying there are no clubs does forget them", async () => {
  const { initClubAndSeasonPickers, currentClubId } = load();
  localStorage.setItem("vf_club", "3");
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });

  let reported = "unset";
  await initClubAndSeasonPickers(
    "http://x",
    makeSelect(),
    makeSelect(),
    "vf_org_season",
    (id) => {
      reported = id;
    },
    () => {}
  );

  assert.equal(currentClubId(), null);
  assert.equal(reported, null);
});

test("a failed club fetch reaches the page instead of vanishing", async () => {
  // Before this, the rejection was unhandled and the page sat blank for
  // ever with nothing said.
  const { initClubAndSeasonPickers } = load();
  globalThis.fetch = async () => {
    throw new Error("Failed to fetch");
  };
  let error = null;
  await initClubAndSeasonPickers(
    "http://x",
    makeSelect(),
    makeSelect(),
    "k",
    () => assert.fail("should not have reported a season"),
    (e) => {
      error = e;
    }
  );
  assert.equal(error.message, "Failed to fetch");
});

// Two loads overlap on most page loads, because the season picker fires
// its callback once from cache and once from the network. Whichever
// finishes last wins, and that is not always the newest one.
test("an older load discards its result once a newer one has started", () => {
  const { staleGuard } = load();
  const guard = staleGuard();

  const first = guard.take();
  const second = guard.take();

  assert.equal(guard.current(first), false, "the older load must not paint");
  assert.equal(guard.current(second), true);
});

test("a lone load is always allowed to paint", () => {
  const { staleGuard } = load();
  const guard = staleGuard();
  assert.equal(guard.current(guard.take()), true);
});

test("two guards don't invalidate each other", () => {
  const { staleGuard } = load();
  const a = staleGuard();
  const b = staleGuard();
  const ticket = a.take();
  b.take();
  assert.equal(a.current(ticket), true);
});

// The calendar's legend is generated from what the season contains, not
// hardcoded: an entry for 人數不足 on a season where every game is full
// is noise, and on a phone each legend row is a row of dates pushed off
// the screen.
test("the legend names only the states this season actually has", () => {
  const { renderMonthCalendar } = load();
  const el = makeElement();
  const games = [
    { id: 1, date: "2026-09-08" },
    { id: 2, date: "2026-09-15" },
  ];
  renderMonthCalendar(el, games, () => {}, {
    stateOf: (g) => (g.id === 1 ? "full" : ""),
  });
  assert.match(el.innerHTML, /已滿/);
  assert.match(el.innerHTML, /有場次/);
  assert.doesNotMatch(el.innerHTML, /人數不足/);
});

test("a season in one state needs no legend at all", () => {
  const { renderMonthCalendar } = load();
  const el = makeElement();
  renderMonthCalendar(el, [{ id: 1, date: "2026-09-08" }], () => {}, {
    stateOf: () => "",
  });
  assert.doesNotMatch(el.innerHTML, /mcal-legend/);
});

test("the organizer's calendar says 已取消 where a member's says 你請假", () => {
  const { renderMonthCalendar } = load();
  const games = [{ id: 1, date: "2026-09-08" }, { id: 2, date: "2026-09-15" }];
  const stateOf = (g) => (g.id === 1 ? "away" : "");

  const member = makeElement();
  renderMonthCalendar(member, games, () => {}, { stateOf });
  assert.match(member.innerHTML, /你請假/);

  const organizer = makeElement();
  renderMonthCalendar(organizer, games, () => {}, { stateOf, awayLabel: "已取消" });
  assert.match(organizer.innerHTML, /已取消/);
});

test("a management picker lists only the clubs you organize", () => {
  // The bug: GET /clubs returns every club you belong to in any role, so
  // a club you are merely a member of appeared in the organizer pages'
  // picker and opened a management screen for somebody else's club.
  const clubs = [
    { id: 1, name: "我開的", role: "organizer" },
    { id: 2, name: "測試二", role: "member" },
  ];
  const organizerOnly = (c) => c.role === "organizer";
  assert.deepEqual(clubs.filter(organizerOnly).map((c) => c.name), ["我開的"]);
});

test("the picker really applies the filter it is handed", async () => {
  const { initClubAndSeasonPickers, currentClubId } = load();
  globalThis.fetch = async (url) => ({
    ok: true,
    json: async () =>
      url.endsWith("/clubs")
        ? [
            { id: 1, name: "我開的", role: "organizer" },
            { id: 2, name: "測試二", role: "member" },
          ]
        : [],
  });
  const clubEl = makeSelect();
  localStorage.setItem("vf_club", "2"); // remembered from the member page

  await initClubAndSeasonPickers(
    "http://x",
    clubEl,
    makeSelect(),
    "k",
    () => {},
    () => {},
    (c) => c.role === "organizer"
  );

  assert.doesNotMatch(clubEl.innerHTML, /測試二/);
  assert.equal(currentClubId(), "1", "a member-only club must not stay selected here");
});
