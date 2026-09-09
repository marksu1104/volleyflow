// What the page does before it has anything to show.
//
// Every test here is a bug someone hit on a real phone: a spinner and a
// question on screen at the same time, "please open this in LINE" from
// someone who was logged in the whole time, and an invite link that
// quietly moved them to a different club. They share a cause — the boot
// path guessing at state it hasn't been told yet — so they're guarded
// together.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

test("a rejected token says to open it in LINE", () => {
  const { signInFailureHtml } = load();
  const html = signInFailureHtml(null);
  assert.match(html, /請用 LINE 開啟/);
  assert.doesNotMatch(html, /重新載入/, "there is nothing to retry");
});

test("a sleeping backend says so, and offers to try again", () => {
  // The bug: a free-tier cold start reported as a LINE login failure.
  // It sent people off to fix an account that was never broken.
  const { signInFailureHtml } = load();
  const html = signInFailureHtml(false);
  assert.match(html, /連不上伺服器/);
  assert.match(html, /重新載入/);
  assert.doesNotMatch(html, /請用 LINE 開啟/);
});

test("identify retries a cold start rather than giving up on the first try", async () => {
  const { initLiffIdentity } = load();
  globalThis.setTimeout = (fn) => fn(); // don't actually wait out the backoff
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls < 3) throw new Error("Failed to fetch");
    return { ok: true, json: async () => ({ id: 7, name: "蘇慬" }) };
  };
  globalThis.liff = {
    init: async () => {},
    isLoggedIn: () => true,
    getIDToken: () => "tok",
    getProfile: async () => ({ displayName: "蘇慬" }),
  };

  const identified = await initLiffIdentity("http://x", "liff-id");

  assert.equal(calls, 3);
  assert.deepEqual(identified, { id: 7, name: "蘇慬" });
});

test("a backend that never answers reports a server failure, not a login one", async () => {
  const { initLiffIdentity } = load();
  globalThis.setTimeout = (fn) => fn();
  globalThis.fetch = async () => {
    throw new Error("Failed to fetch");
  };
  globalThis.liff = {
    init: async () => {},
    isLoggedIn: () => true,
    getIDToken: () => "tok",
    getProfile: async () => ({ displayName: "蘇慬" }),
  };

  assert.equal(await initLiffIdentity("http://x", "liff-id"), false);
});

test("a rejected token is not retried three times over", async () => {
  const { initLiffIdentity } = load();
  globalThis.setTimeout = (fn) => fn();
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return { ok: false, status: 401, statusText: "Unauthorized", json: async () => ({}) };
  };
  globalThis.liff = {
    init: async () => {},
    isLoggedIn: () => true,
    getIDToken: () => "tok",
    getProfile: async () => ({ displayName: "蘇慬" }),
  };

  assert.equal(await initLiffIdentity("http://x", "liff-id"), null);
  assert.equal(calls, 1, "the server already answered; asking again says the same");
});

test("LINE being unavailable is not retried either", async () => {
  const { initLiffIdentity } = load();
  let identifyCalls = 0;
  globalThis.fetch = async () => {
    identifyCalls += 1;
    return { ok: true, json: async () => ({}) };
  };
  globalThis.liff = {
    init: async () => {
      throw new Error("no liff");
    },
    isLoggedIn: () => false,
  };

  assert.equal(await initLiffIdentity("http://x", "liff-id"), null);
  assert.equal(identifyCalls, 0);
});

// 「請問你是這一季的固定成員嗎？」 — asked once, and only when the answer
// isn't already on file. It kept appearing at the wrong moment.
const { shouldAskIntent } = load("member.html");
const unanswered = { id: 1, wants_fixed_membership: null };

test("the intent question waits until the season is known", () => {
  // The bug: it was asked as soon as identity resolved, so it sat above
  // a hero that still read 載入中.
  assert.equal(shouldAskIntent(false, unanswered, false), false);
  assert.equal(shouldAskIntent(true, unanswered, false), true);
});

test("someone already on the roster is never asked", () => {
  // The same bug's worse half: with no season loaded, onRoster is false
  // for everyone, so existing fixed members got asked whether they were
  // fixed members.
  assert.equal(shouldAskIntent(true, unanswered, true), false);
});

test("an answer already on file isn't asked for again", () => {
  assert.equal(shouldAskIntent(true, { id: 1, wants_fixed_membership: true }, false), false);
  assert.equal(shouldAskIntent(true, { id: 1, wants_fixed_membership: false }, false), false);
});

test("someone not in the club is not asked — that's the join prompt's job", () => {
  assert.equal(shouldAskIntent(true, undefined, false), false);
});

test("an empty state can offer an action that isn't a link", () => {
  // 重新載入 has nowhere to navigate to — it's the same page.
  const { emptyStateHtml } = load();
  const html = emptyStateHtml("標題", "說明", { label: "重新載入", onclick: "location.reload()" });
  assert.match(html, /<button[^>]*onclick="location.reload\(\)"/);
  assert.match(html, /重新載入/);
  assert.doesNotMatch(html, /<a /);
});

test("an empty state with a link still renders a link", () => {
  const { emptyStateHtml } = load();
  const html = emptyStateHtml("標題", "說明", { label: "建立第一季", href: "x.html" });
  assert.match(html, /<a class="btn btn-primary" href="x.html"/);
});

// Joining and saying which kind of member you are is one decision, so
// nothing is actionable in between. The bug: the question was asked
// after landing in the app, with a ＋1 報名 button live underneath it.
const { viewingOnlyReason } = load("member.html");

test("not having answered yet is not a reason to block anyone", () => {
  // The regression this locks out: an organizer, and every member who
  // joined before the question existed, both carry a null here — and
  // were told "請先回答你是固定成員還是臨打" with no way past it.
  assert.equal(viewingOnlyReason({ role: "member", wants_fixed_membership: null }, false), null);
});

test("the organizer is never waiting on the organizer", () => {
  assert.equal(viewingOnlyReason({ role: "organizer", wants_fixed_membership: null }, false), null);
  assert.equal(viewingOnlyReason({ role: "organizer", wants_fixed_membership: true }, false), null);
});

test("a fixed member waiting on the roster is told exactly that", () => {
  const reason = viewingOnlyReason({ role: "member", wants_fixed_membership: true }, false);
  assert.match(reason, /等待主揪/);
});

test("being on the roster settles it, whatever the column says", () => {
  // Everyone added before the question existed has a null.
  for (const wants of [true, false, null]) {
    assert.equal(viewingOnlyReason({ role: "member", wants_fixed_membership: wants }, true), null);
  }
});

test("someone who said they're only a drop-in acts normally", () => {
  assert.equal(viewingOnlyReason({ role: "member", wants_fixed_membership: false }, false), null);
});

test("a non-member isn't blocked by this rule — the invite screen has them", () => {
  assert.equal(viewingOnlyReason(undefined, false), null);
});

// Only shared.js and shared.css carried a version, so a cached HTML file
// served with a fresh script was a normal state — and any change living
// in the markup was invisible until the page happened to expire. Twice
// reported as "I can't see the thing you said you built".
test("a page whose markup is older than its script reloads itself", () => {
  const { assertFreshBuild } = load();
  const replaced = [];
  globalThis.location = { href: "https://x/organizer-settings.html", replace: (u) => replaced.push(u) };
  globalThis.document.querySelector = (sel) =>
    sel.includes("meta")
      ? { getAttribute: () => "aaaaaaaa" }
      : { getAttribute: () => "shared.js?v=bbbbbbbb" };

  assertFreshBuild();

  assert.equal(replaced.length, 1);
  assert.match(replaced[0], /v=bbbbbbbb/);
});

test("a page already on the current build is left alone", () => {
  const { assertFreshBuild } = load();
  let replaced = 0;
  globalThis.location = { href: "https://x/a.html", replace: () => (replaced += 1) };
  globalThis.document.querySelector = (sel) =>
    sel.includes("meta")
      ? { getAttribute: () => "aaaaaaaa" }
      : { getAttribute: () => "shared.js?v=aaaaaaaa" };

  assertFreshBuild();

  assert.equal(replaced, 0);
});

test("a mismatch that survives the reload doesn't loop forever", () => {
  // A missing meta or a half-finished deploy would otherwise refresh the
  // page over and over.
  const { assertFreshBuild } = load();
  let replaced = 0;
  globalThis.location = { href: "https://x/a.html", replace: () => (replaced += 1) };
  globalThis.document.querySelector = (sel) =>
    sel.includes("meta")
      ? { getAttribute: () => "aaaaaaaa" }
      : { getAttribute: () => "shared.js?v=bbbbbbbb" };

  assertFreshBuild();
  assertFreshBuild();
  assertFreshBuild();

  assert.equal(replaced, 1);
});

test("a page served without the stamp is left alone", () => {
  // Opening the file locally, or before the deploy step adds the meta.
  const { assertFreshBuild } = load();
  let replaced = 0;
  globalThis.location = { href: "https://x/a.html", replace: () => (replaced += 1) };
  globalThis.document.querySelector = () => null;

  assertFreshBuild();

  assert.equal(replaced, 0);
});

// Dev login: a way to be somebody on a laptop, where there is no LIFF.
// Three locks, and these cover the one that lives in the browser — the
// other two are on the server (see tests/test_auth.py).
test("?as= only works on a laptop, never on the real site", () => {
  const { devIdentityName } = load();

  globalThis.location = { hostname: "localhost", search: "?as=蘇懂" };
  assert.equal(devIdentityName(), "蘇懂");

  globalThis.location = { hostname: "marksu1104.github.io", search: "?as=蘇懂" };
  assert.equal(devIdentityName(), null, "the shipped site must never offer this");
});

test("the identity sticks across pages without carrying the parameter", () => {
  const { devIdentityName } = load();
  globalThis.location = { hostname: "127.0.0.1", search: "?as=楊于嫺" };
  devIdentityName();

  globalThis.location = { hostname: "127.0.0.1", search: "" };
  assert.equal(devIdentityName(), "楊于嫺");
});

test("?as= with nothing after it drops the identity", () => {
  const { devIdentityName } = load();
  globalThis.location = { hostname: "localhost", search: "?as=蘇懂" };
  devIdentityName();

  globalThis.location = { hostname: "localhost", search: "?as=" };
  assert.equal(devIdentityName(), null);
});

test("a dev identity is sent as a token nothing else could produce", () => {
  // The server only accepts this shape, and only with an environment
  // variable production doesn't set.
  const { devIdentityName, authHeader } = load();
  globalThis.location = { hostname: "localhost", search: "?as=蘇懂" };
  devIdentityName();

  assert.equal(authHeader().Authorization, "Bearer dev:%E8%98%87%E6%87%82");
});

test("with no dev identity the header is whatever LIFF gives", () => {
  const { authHeader } = load();
  globalThis.location = { hostname: "localhost", search: "" };
  globalThis.liff = { isLoggedIn: () => true, getIDToken: () => "real-token" };

  assert.equal(authHeader().Authorization, "Bearer real-token");
});
