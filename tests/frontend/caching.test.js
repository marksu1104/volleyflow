// Pages paint from the last thing this device saw and correct themselves
// when the network answers, because every navigation otherwise waits on
// a chain of requests that can only run one after another. That's only
// safe if the cache never outranks the server on anything irreversible,
// and if a failure with nothing cached still surfaces.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeSelect } = require("./harness.js");

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
