// A LINE ID token lasts an hour, and a page left open on a phone lasts as
// long as the phone does.
//
// LINE's LIFF reference: an ID token "is valid for one hour after it is
// issued" — and nothing about the SDK replacing it. shared.js used to
// assume it did ("liff.getIDToken() already handles refreshing it"), so a
// page left open for an hour sent a token LINE refused, and every action
// on it failed as 「操作失敗（LINE rejected this ID token）」. That is how
// creating a club failed on the production site on 2026-09-15.
//
// None of the other checks could see it: every one of them signs in with
// a local `dev:` token, and a dev token never expires.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const HOUR = 3600;

/** A logged-in LIFF whose token expires `secondsLeft` from now. */
function liffWithToken(secondsLeft, calls) {
  const exp = Math.floor(Date.now() / 1000) + secondsLeft;
  return {
    isLoggedIn: () => true,
    getIDToken: () => "real-token",
    getDecodedIDToken: () => ({ exp }),
    logout: () => calls.push("logout"),
  };
}

/** The reloads and sign-outs a test caused, instead of real ones. */
function watch() {
  const calls = [];
  globalThis.location.reload = () => calls.push("reload");
  return calls;
}

test("a token with most of its hour left is fresh", () => {
  const { lineTokenIsFresh } = load();
  globalThis.liff = liffWithToken(HOUR - 60, []);

  assert.equal(lineTokenIsFresh(), true);
});

test("a token past its hour is not", () => {
  const { lineTokenIsFresh } = load();
  globalThis.liff = liffWithToken(-5, []);

  assert.equal(lineTokenIsFresh(), false);
});

test("a token with seconds to spare counts as spent already", () => {
  // It can reach the server after it has lapsed, and gets the same
  // refusal as one that was sent late.
  const { lineTokenIsFresh } = load();
  globalThis.liff = liffWithToken(30, []);

  assert.equal(lineTokenIsFresh(), false);
});

test("with nothing to check, it never claims a token is spent", () => {
  // No LIFF at all, or a token it can't decode: saying "expired" there
  // would reload a page that has no way to get a better one.
  const { lineTokenIsFresh } = load();
  delete globalThis.liff;
  assert.equal(lineTokenIsFresh(), true);

  globalThis.liff = { isLoggedIn: () => true, getDecodedIDToken: () => null };
  assert.equal(lineTokenIsFresh(), true);
});

test("local sign-in is never expired", () => {
  const { lineTokenIsFresh } = load();
  globalThis.location.hostname = "localhost";
  globalThis.location.search = "?as=%E5%91%A8%E6%81%86";
  globalThis.liff = liffWithToken(-HOUR, []);

  assert.equal(lineTokenIsFresh(), true, "a dev token has no hour to run out of");
});

test("a write with a spent token is never sent — the session restarts", async () => {
  const { postJson } = load();
  const calls = watch();
  globalThis.liff = liffWithToken(-5, calls);
  let sent = false;
  globalThis.fetch = async () => {
    sent = true;
    return { ok: true, status: 200, json: async () => ({}) };
  };

  await assert.rejects(postJson("http://api", "/clubs", { name: "晴光館" }), /登入已過期/);

  assert.equal(sent, false, "it could only have been refused");
  assert.deepEqual(calls, ["logout", "reload"]);
});

test("a refusal for an expired token restarts the session", async () => {
  // The case the freshness check can't see coming: the server's clock and
  // the phone's disagree, or the token expired between check and arrival.
  const { postJson } = load();
  const calls = watch();
  globalThis.liff = liffWithToken(HOUR, calls);
  globalThis.fetch = async () => ({
    ok: false,
    status: 401,
    json: async () => ({ detail: "LINE rejected this ID token" }),
  });

  await assert.rejects(postJson("http://api", "/clubs", { name: "x" }));

  assert.deepEqual(calls, ["logout", "reload"]);
});

test("a person the server has forgotten is re-identified, not signed out", async () => {
  // A database reset while the page was open: the token is fine, and it
  // is only the page's cached "who is this" that is out of date.
  const { deleteJson } = load();
  const calls = watch();
  globalThis.liff = liffWithToken(HOUR, calls);
  sessionStorage.setItem("vf_identity", JSON.stringify({ id: 7 }));
  globalThis.fetch = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: "No player identified for this LINE account yet" }),
  });

  await assert.rejects(deleteJson("http://api", "/seasons/1/members/2"));

  assert.deepEqual(calls, ["reload"], "no sign-out: the token was never the problem");
  assert.equal(sessionStorage.getItem("vf_identity"), null);
});

test("an ordinary refusal is an answer, not a session problem", async () => {
  const { postJson } = load();
  const calls = watch();
  globalThis.liff = liffWithToken(HOUR, calls);
  globalThis.fetch = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ detail: "No season with id 9" }),
  });

  await assert.rejects(postJson("http://api", "/seasons/9/members", {}));

  assert.deepEqual(calls, []);
});

test("it restarts at most once in a few minutes, rather than reload forever", async () => {
  // Refused again right after a fresh sign-in means age isn't the problem
  // — a misconfigured server, say — and a page that reloads endlessly
  // hides that behind flashing.
  const { postJson } = load();
  const calls = watch();
  globalThis.liff = liffWithToken(HOUR, calls);
  globalThis.fetch = async () => ({
    ok: false,
    status: 401,
    json: async () => ({ detail: "LINE rejected this ID token" }),
  });

  await assert.rejects(postJson("http://api", "/a", {}));
  await assert.rejects(postJson("http://api", "/b", {}));

  assert.deepEqual(calls, ["logout", "reload"], "the second refusal is left on screen");
});

test("a cached read no longer hides an expired session", async () => {
  // It used to swallow the refusal and keep showing the old copy: no
  // error, just data that quietly stopped changing.
  const { getJsonSWR, writeCache } = load();
  const calls = watch();
  globalThis.liff = liffWithToken(HOUR, calls);
  writeCache("http://api/seasons/1", { id: 1 });
  globalThis.fetch = async () => ({
    ok: false,
    status: 401,
    statusText: "Unauthorized",
    json: async () => ({ detail: "LINE rejected this ID token" }),
  });

  await getJsonSWR("http://api/seasons/1", () => {});

  assert.deepEqual(calls, ["logout", "reload"]);
});

test("each identity refusal reads as something to do, in Chinese", () => {
  const { translateApiError } = load();

  for (const raw of [
    "LINE rejected this ID token",
    "Missing bearer token",
    "No player identified for this LINE account yet",
    "Invite links aren't configured on this server",
  ]) {
    const shown = translateApiError(raw);
    assert.doesNotMatch(shown, /操作失敗/, `${raw} still arrives as a bracketed English error`);
    // The English itself is what must not reach the screen. "LINE" may:
    // it is the name of the app the person is holding.
    assert.ok(!shown.includes(raw), `${raw} -> ${shown}`);
  }
});
