// The backend's HTTPException details are deliberately English (see
// CLAUDE.md's language policy); translateApiError is the one place that
// puts the reader-facing reason in Chinese, so every toast(prefix +
// e.message) call site gets it for free rather than needing forty raise
// sites individually rewritten. See its own doc comment in shared.js for
// why this is a curated table rather than backend error codes.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./harness.js");

const { translateApiError } = load();

function isChinese(text) {
  return /[一-鿿]/.test(text);
}

// Every one of these is a literal detail string this project's own
// routes.py actually raises — see the grep this table was built from.
// If a raise site's wording changes, this is what should catch it: not
// matching any more falls back to the "操作失敗（…）" wrapper, which
// still contains English but no longer speaks Chinese *about* it.
const KNOWN_MESSAGES = [
  "Already a member of this club",
  "Already a member of this season",
  "Not a member of this club",
  "Not a member of this season",
  "Player is not a member of this club",
  "Player is not a fixed member of this game's season",
  "You are not a member of this club",
  "Only this club's organizer can do that",
  "This is the club's only organizer",
  "Still a fixed member of a season — remove them from that season first",
  "You can only do that for yourself, unless you're the organizer",
  "You can only list your own clubs",
  "You can only rename yourself, or someone you added who has no account",
  "You can only set your own gender",
  "That person has their own account — they need to sign themselves up",
  "Those are the same player",
  "阿哲 is already signed up for this game",
  "That player is already signed up for this game",
  "阿哲 is already on the waitlist for this game",
  "阿哲 is a fixed member of this season — no need to sign up",
  "阿哲 is a fixed member of this season and is already expected — they can't stand in for somebody else",
  "This player already has an absence recorded for this game",
  "This absence was cancelled",
  "Already cancelled",
  "Past this season's change deadline for this game",
  "No room for another member: 2026-08-18, 2026-08-25 already 18 on court. Cancel a signup on those games, or raise the capacity first.",
  "19 fixed members is more than the capacity of 18",
  "Can't lower capacity to 5 — 8 people are already on the roster or on a game's court",
  "This game is full (18) and everyone in it was personally arranged — cancel one of them first.",
  "This game is full (18). Name who comes out, or raise the capacity first.",
  "That roster entry is already linked to a LINE account",
  "That player has no LINE account to link",
  "That LINE account already has ledger entries of its own — remove the duplicate roster entry instead",
  "Season is already settled — venue cost can't change now",
  "Season is already settled",
  "Season already settled",
  "This season is settled — its books can't be deleted",
  "A season in this club is settled — its books can't be deleted",
  "That would make the season's venue cost negative",
  "No club with id 42",
  "No season with id 42",
  "No game with id 42",
  "No absence with id 42",
  "No drop-in with id 42",
  "No waitlist entry with id 42",
  "No player with id 42",
  "No player named 'Ricky' in this club",
  "This club no longer exists",
  "Invite link not recognised",
  "Missing bearer token",
  "Invalid or expired LINE ID token",
  "No player identified for this LINE account yet",
  "Name can't be empty",
  "Problem reporting isn't configured yet",
  "Nothing to report",
  "Couldn't send the report — please tell the organizer directly",
  "Screenshot is too large",
  "Screenshot isn't valid base64",
  "Screenshot must be a PNG, JPEG or WebP",
  "No such screenshot",
];

test("every known backend message translates to Chinese", () => {
  const untranslated = KNOWN_MESSAGES.filter((msg) => !isChinese(translateApiError(msg)));
  assert.deepEqual(untranslated, [], "these fell through to the English fallback");
});

test("a name interpolated into the message survives translation", () => {
  const out = translateApiError("阿哲 is already signed up for this game");
  assert.match(out, /阿哲/);
});

test("numbers interpolated into a capacity message survive translation", () => {
  const out = translateApiError("19 fixed members is more than the capacity of 18");
  assert.match(out, /19/);
  assert.match(out, /18/);
});

test("an unrecognised message still reads as Chinese, with the reason kept", () => {
  const out = translateApiError("Something nobody has ever seen before");
  assert.match(out, /操作失敗/);
  assert.match(out, /Something nobody has ever seen before/);
});

test("no detail at all doesn't throw", () => {
  assert.doesNotThrow(() => translateApiError(undefined));
  assert.doesNotThrow(() => translateApiError(null));
  assert.doesNotThrow(() => translateApiError(""));
});

test("postJson exposes both the translated and the raw message", async () => {
  const { postJson } = load();
  globalThis.fetch = async () => ({
    ok: false,
    status: 400,
    json: async () => ({ detail: "Already a member of this season" }),
  });
  await assert.rejects(
    postJson("http://api", "/clubs/1/join", {}),
    (err) => {
      assert.match(err.message, /已經是這裡的成員了/);
      assert.equal(err.rawMessage, "Already a member of this season");
      return true;
    }
  );
});

test("deleteJson exposes both the translated and the raw message", async () => {
  const { deleteJson } = load();
  globalThis.fetch = async () => ({
    ok: false,
    status: 400,
    json: async () => ({ detail: "Season is already settled" }),
  });
  await assert.rejects(
    deleteJson("http://api", "/seasons/1/members/1"),
    (err) => {
      assert.match(err.message, /已經結算/);
      assert.equal(err.rawMessage, "Season is already settled");
      return true;
    }
  );
});
