// Asking to join from an invite link sends the link's token and what the
// person asked to be. Since 2026-09-16 the organizer then lets them in;
// tests/visual/join.js walks the whole flow in a browser.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

test("臨打成員 sends the invite link's token and asks for single games", async () => {
  const { joinFromPrompt } = load("member.html", { search: "?invite=12.abcdef0123456789abcd" });
  const sent = [];
  globalThis.fetch = async (url, opts = {}) => {
    sent.push({ url: String(url), method: opts.method || "GET", body: opts.body });
    return { ok: true, status: 200, json: async () => ({}) };
  };

  await joinFromPrompt(false).catch(() => {});

  const join = sent.find((r) => r.method === "POST" && r.url.endsWith("/join"));
  assert.ok(join, "a join request was sent");
  assert.deepEqual(JSON.parse(join.body), {
    invite: "12.abcdef0123456789abcd",
    wants_fixed_membership: false,
  });
});

test("the token leaves the address bar, and nothing else does", () => {
  // Out of the URL straight away, so a reload doesn't keep snapping back
  // to the invite; only that parameter, so ?as= survives for local work.
  load("member.html", { search: "?invite=12.abcdef0123456789abcd&as=x" });

  const [, , url] = globalThis.history.calls[0] || [];

  assert.equal(url, "/member.html?as=x");
});
