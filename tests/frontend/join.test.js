// Joining a club from an invite link sends the link's token.
//
// Since 2026-09-15 the server refuses a join that carries only a club id
// — an id alone had let anybody join every club in turn (see
// tests/api/test_tenant_isolation.py). So the page has to keep the token
// from the URL all the way to the join button. tests/visual/join.js walks
// the whole flow in a browser, but that runs by hand; this runs on every
// push.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

test("pressing 加入球隊 sends the invite link's token", async () => {
  const { joinFromPrompt } = load("member.html", { search: "?invite=12.abcdef0123456789abcd" });
  const sent = [];
  globalThis.fetch = async (url, opts = {}) => {
    sent.push({ url: String(url), method: opts.method || "GET", body: opts.body });
    return { ok: true, status: 200, json: async () => ({}) };
  };

  await joinFromPrompt().catch(() => {});

  const join = sent.find((r) => r.method === "POST" && r.url.endsWith("/join"));
  assert.ok(join, "a join request was sent");
  assert.deepEqual(JSON.parse(join.body), { invite: "12.abcdef0123456789abcd" });
});

test("the token leaves the address bar, and nothing else does", () => {
  // Out of the URL straight away, so a reload or a club switch doesn't
  // keep snapping back to the invite — while the page itself still holds
  // it for the join. And only that parameter: an earlier version dropped
  // the whole query string, which also took ?as= and silently turned a
  // local sign-in into a real LINE login.
  load("member.html", { search: "?invite=12.abcdef0123456789abcd&as=x" });

  const [, , url] = globalThis.history.calls[0] || [];

  assert.equal(url, "/member.html?as=x");
});
