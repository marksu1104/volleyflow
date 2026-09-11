// Reads must never undo a change the person has already made.
//
// The bug these guard against is 「按了成功結果又切回去又切回來」, and it
// was in the plumbing rather than in any one screen: refreshing after a
// change fired a GET straight away, which overtook the *next* change
// still waiting its turn in the write queue. The answer came back
// describing the roster as it was before that tap, the renderer trusted
// it, and the tap was painted away — then the following refresh put it
// back. Measured on a real page: correct at 316ms, wrong from 1257ms,
// right again at 2017ms.
//
// Two rules fix it and both are tested here: a refresh goes through the
// same queue as the writes, and a renderer refuses an answer that set off
// before the latest local change.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const tick = () => new Promise((r) => setTimeout(r, 0));
const after = (ms) => new Promise((r) => setTimeout(r, ms));

test("a refresh waits for every change already queued", async () => {
  const { coalescedRefresh, enqueueRequest } = load();
  const order = [];
  let releaseWrite;
  const write = new Promise((r) => {
    releaseWrite = r;
  });

  enqueueRequest(async () => {
    await write;
    order.push("write");
  });
  const refresh = coalescedRefresh(async () => {
    order.push("read");
  });
  refresh();
  releaseWrite();
  await after(20);

  assert.deepEqual(order, ["write", "read"], "the read must not overtake the write");
});

test("asking twice while one is still queued only reads once", async () => {
  const { coalescedRefresh, enqueueRequest } = load();
  let reads = 0;
  let releaseWrite;
  const write = new Promise((r) => {
    releaseWrite = r;
  });
  enqueueRequest(() => write);

  const refresh = coalescedRefresh(async () => {
    reads += 1;
  });
  refresh();
  refresh();
  refresh();
  releaseWrite();
  await after(20);

  assert.equal(reads, 1, "the queued read hasn't started, so it will see everything");
});

test("a change after the read has finished gets a read of its own", async () => {
  const { coalescedRefresh } = load();
  let reads = 0;
  const refresh = coalescedRefresh(async () => {
    reads += 1;
  });

  refresh();
  await after(20);
  refresh();
  await after(20);

  assert.equal(reads, 2);
});

test("a read that fails doesn't stop the next one", async () => {
  const { coalescedRefresh } = load();
  let reads = 0;
  const refresh = coalescedRefresh(async () => {
    reads += 1;
    throw new Error("offline");
  });

  refresh();
  await after(20);
  refresh();
  await after(20);

  assert.equal(reads, 2, "busy must be released even when the read threw");
});

test("changing the screen locally moves the revision on", async () => {
  const { optimisticRunner, localRevision } = load();
  const state = { n: 0 };
  const run = optimisticRunner({
    getState: () => state,
    setState: () => {},
    repaint: () => {},
  });

  const before = localRevision();
  await run(
    (s) => {
      s.n = 1;
    },
    async () => ({}),
    "failed: "
  );

  assert.ok(localRevision() > before, "a read in the air is now out of date");
});

test("rolling a failed change back moves the revision on again", async () => {
  const { optimisticRunner, localRevision } = load();
  const state = { n: 0 };
  const run = optimisticRunner({
    getState: () => state,
    setState: (s) => {
      state.n = s.n;
    },
    repaint: () => {},
  });

  await run(
    (s) => {
      s.n = 1;
    },
    async () => {
      throw new Error("no");
    },
    "failed: "
  );
  const afterRollback = localRevision();
  await tick();

  assert.equal(state.n, 0, "the screen goes back to what the server still believes");
  assert.ok(afterRollback >= 2, "the rollback is itself a local change");
});

test("a renderer can tell an answer is older than the screen", async () => {
  // How every loadSeason/loadDashboard/loadMembers guards itself: note
  // the revision when the read sets off, compare when it lands.
  const { optimisticRunner, localRevision } = load();
  const state = { n: 0 };
  const run = optimisticRunner({
    getState: () => state,
    setState: () => {},
    repaint: () => {},
  });

  const askedAt = localRevision();
  await run(
    (s) => {
      s.n = 1;
    },
    async () => ({}),
    "failed: "
  );

  assert.notEqual(localRevision(), askedAt, "so the answer is dropped, not painted");
});

test("recording and undoing leave keep the roster lists we already have", () => {
  // These three are the most-used writes in the app and none of them can
  // introduce, rename or remove anybody — so throwing away the club
  // roster and the guest list after each one was two round trips spent
  // re-reading something that could not have changed.
  const { keepsPeople } = load();

  assert.equal(keepsPeople("/absences"), true);
  assert.equal(keepsPeople("/absences/12/cancel"), true);
  assert.equal(keepsPeople("/waitlist/3/cancel"), true);
});

test("a change that saves quickly never mentions itself", async () => {
  // The page-level marker is for waits worth reporting. Flashing it on
  // every tap would be the same mistake as a spinner on a button whose
  // job is already done.
  const { beginPendingWrite, elements } = load();

  const settled = beginPendingWrite();
  await after(60);
  settled();
  await after(500);

  assert.equal(elements["vf-pending"].classList.contains("shown"), false);
});

test("a change still saving after half a second says so", async () => {
  const { beginPendingWrite, elements } = load();

  const settled = beginPendingWrite();
  await after(520);
  const shownWhileWaiting = elements["vf-pending"].classList.contains("shown");
  settled();

  assert.equal(shownWhileWaiting, true, "this is the state 請假 could never show on its button");
  assert.equal(elements["vf-pending"].classList.contains("shown"), false, "and it goes when done");
});

test("two changes at once say it once, and only stop when both are done", async () => {
  const { beginPendingWrite, elements } = load();

  const first = beginPendingWrite();
  const second = beginPendingWrite();
  await after(520);
  first();
  const stillShown = elements["vf-pending"].classList.contains("shown");
  second();

  assert.equal(stillShown, true, "the second one is still in the air");
  assert.equal(elements["vf-pending"].classList.contains("shown"), false);
});

test("releasing the same change twice doesn't unbalance the count", async () => {
  const { beginPendingWrite, elements } = load();

  const first = beginPendingWrite();
  const second = beginPendingWrite();
  first();
  first();
  await after(520);
  const shown = elements["vf-pending"].classList.contains("shown");
  second();

  assert.equal(shown, true, "second is still outstanding; a double release must not hide it");
});

test("anything that can introduce somebody drops the roster lists", () => {
  const { keepsPeople } = load();

  assert.equal(keepsPeople("/absences/12/substitute"), false, "names a new person");
  assert.equal(keepsPeople("/games/4/drop-ins"), false, "brings a guest");
  assert.equal(keepsPeople("/clubs/2/join"), false);
  assert.equal(keepsPeople("/players/7/gender"), false);
  assert.equal(keepsPeople("/drop-ins/9/cancel"), false, "may empty a guest list");
});
