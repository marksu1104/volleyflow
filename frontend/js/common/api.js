// Talking to the API: who the user is, requests, and the response cache.

/** POST or PUT a JSON body, returning the parsed response or throwing
 * with the API's own error detail. */
/** Whether this page is being served from a development machine rather
 * than the real site. Not a security boundary by itself — this runs in
 * the visitor's browser — but it means the shipped site never even
 * offers to send a dev token. The real locks are on the server (see
 * auth.verify_id_token).
 *
 * Private LAN addresses count, not just localhost. A desktop browser is
 * not a phone: it can't show real Safari rendering, touch targets or the
 * keyboard, and three bugs have reached a phone that every static check
 * passed. Reaching the laptop's server from the phone over wifi is what
 * makes "change it and look at it on the actual device" possible, and
 * that address is 192.168.x.x, never localhost.
 */
function isLocalDev() {
  const h = location.hostname;
  if (["localhost", "127.0.0.1", "[::1]"].includes(h)) return true;
  // The three private IPv4 ranges (RFC 1918) — home and office wifi.
  return (
    /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h) ||
    /^192\.168\.\d{1,3}\.\d{1,3}$/.test(h) ||
    /^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(h)
  );
}

/** Who you're pretending to be, from `?as=<name>` — local only.
 *
 * Nothing in this app is reachable without a verified identity, and on a
 * laptop there is no LIFF to get one from, so every page stopped at
 * "open this in LINE". Testing a member's view meant picking up a phone
 * and using the live club's real data. This is the way in:
 *
 *     http://localhost:5500/member.html?as=小明
 *
 * Remembered for the session, so links inside the app keep the identity
 * without carrying the parameter around. `?as=` with no name clears it.
 */
function devIdentityName() {
  if (!isLocalDev()) return null;
  const asked = new URLSearchParams(location.search).get("as");
  try {
    if (asked !== null) {
      if (asked.trim()) sessionStorage.setItem("vf_dev_as", asked.trim());
      else sessionStorage.removeItem("vf_dev_as");
    }
    return sessionStorage.getItem("vf_dev_as");
  } catch (e) {
    return asked && asked.trim() ? asked.trim() : null;
  }
}

/** How long before a LINE ID token runs out it is already treated as
 * spent. A request sent with seconds to spare can reach the server after
 * the token has lapsed, and gets the same refusal as one sent late. */
const LINE_TOKEN_MARGIN_SECONDS = 120;

/** Whether the LINE ID token this page would send can still be used.
 *
 * LINE's LIFF reference says an ID token "is valid for one hour after it
 * is issued", and says nothing about the SDK replacing it. So a page left
 * open in LINE for an hour — which on a phone is simply how pages get
 * left — went on sending a token LINE refuses, and every action failed
 * as 「操作失敗（LINE rejected this ID token）」. Found 2026-09-15, when
 * creating a club failed on the production site.
 *
 * This file used to say the opposite: that liff.getIDToken() "already
 * handles refreshing it". Nobody had checked, and the one thing the
 * documentation does say contradicts it.
 *
 * True whenever there is nothing to check — local sign-in, no LIFF, no
 * decodable token. It only says no when it knows the token is spent.
 */
function lineTokenIsFresh(nowMs) {
  if (devIdentityName()) return true;
  try {
    if (typeof liff === "undefined" || !liff.isLoggedIn()) return true;
    const decoded = liff.getDecodedIDToken();
    if (!decoded || typeof decoded.exp !== "number") return true;
    const now = (nowMs === undefined ? Date.now() : nowMs) / 1000;
    return decoded.exp - LINE_TOKEN_MARGIN_SECONDS > now;
  } catch (e) {
    return true;
  }
}

const _RESTART_KEY = "vf_line_restart_at";
const _RESTART_COOLDOWN_MS = 3 * 60 * 1000;

/** Starts the page's LINE session over, which is how it gets a new
 * identity.
 *
 * LINE documents no way to renew an ID token in place. What it does
 * document is that logging out discards the tokens and that initialising
 * LIFF signs you in — automatically inside the LINE app, by redirect in a
 * browser — so logging out and reloading yields a new one either way.
 *
 * `signOut` is false when the token is fine and it is *our* record of the
 * person that has gone (a database reset while the page was open): then
 * only the cached identity needs forgetting, and the reload re-identifies.
 *
 * At most once every few minutes. If the server refuses again straight
 * after a fresh sign-in, age is not the problem — a misconfigured server,
 * say — and reloading forever would hide that behind a page that never
 * stops flashing. Returns whether it restarted.
 */
function restartLineSession(signOut = true) {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(_RESTART_KEY)) || 0;
  } catch (e) {
    // No storage: try each time rather than never.
  }
  if (Date.now() - last < _RESTART_COOLDOWN_MS) return false;
  try {
    sessionStorage.setItem(_RESTART_KEY, String(Date.now()));
    sessionStorage.removeItem("vf_identity");
  } catch (e) {
    // Nothing to clear.
  }
  toast("登入已過期，正在重新登入…");
  if (signOut) {
    try {
      if (typeof liff !== "undefined" && liff.isLoggedIn()) liff.logout();
    } catch (e) {
      // Reloading still helps if logout itself isn't available.
    }
  }
  location.reload();
  return true;
}

/** Whether a refusal means "who you are has lapsed" rather than "no".
 * Everything else a server says is an answer to show; these three are a
 * session to renew. */
function identityLapsed(status, detail) {
  if (status === 401) {
    return /^(LINE rejected this ID token|Missing bearer token)$/.test(detail || "");
  }
  if (status === 404) return detail === "No player identified for this LINE account yet";
  return false;
}

let _watchingLineToken = false;

/** Checks the token whenever the page comes back into view — the moment
 * a phone user returns to a page they left open, and before they have
 * tapped anything, which is what makes reloading then harmless. */
function watchLineToken() {
  if (_watchingLineToken) return;
  _watchingLineToken = true;
  const check = () => {
    if (document.visibilityState === "hidden") return;
    if (!lineTokenIsFresh()) restartLineSession();
  };
  document.addEventListener("visibilitychange", check);
  if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("pageshow", check);
  }
}

/** Authorization header for the current LIFF session, or {} if there
 * isn't one — every request that might need identity spreads this in.
 * Read afresh on every call, but that alone never made it fresh: see
 * lineTokenIsFresh for why the token can be an hour old, and
 * restartLineSession for what happens then. */
function authHeader() {
  const dev = devIdentityName();
  // encodeURIComponent because an Authorization header has to be ASCII:
  // a browser throws outright on `Bearer dev:小明`. auth.verify_id_token
  // decodes it back.
  if (dev) return { Authorization: `Bearer dev:${encodeURIComponent(dev)}` };
  try {
    if (typeof liff !== "undefined" && liff.isLoggedIn()) {
      return { Authorization: `Bearer ${liff.getIDToken()}` };
    }
  } catch (e) {
    console.warn("No LIFF identity available:", e);
  }
  return {};
}

/** Initializes LIFF — logging in via redirect if this isn't already a
 * logged-in LIFF session — then resolves this device's real identity
 * through /players/identify. Works the same whether opened inside the
 * LINE app or in a plain desktop browser: liff.login() falls back to a
 * LINE Login web redirect outside the app, so this one flow covers both
 * member.html and the organizer pages.
 *
 * Returns the identified player ({id, name, avatar_url, gender}), or a
 * falsy value that says which half failed, because the two need
 * different words on screen and only one is worth retrying:
 *   null  — LINE's side. No LIFF, login declined, token rejected.
 *           Nothing to retry; tell them to open it in LINE.
 *   false — our backend didn't answer after three tries. Almost always
 *           a sleeping free-tier instance. Tell them that, and offer
 *           to reload.
 * Callers that don't care can keep testing falsiness and degrade to a
 * read-only view either way.
 */
async function initLiffIdentity(apiBase, liffId) {
  // Skips LINE entirely — see devIdentityName. Goes through the same
  // /players/identify call as a real login, so everything downstream
  // (clubs, roles, the ledger) behaves exactly as it does for a real
  // person; only where the identity came from is different.
  const devName = devIdentityName();
  if (devName) {
    try {
      return await postJson(apiBase, "/players/identify", {
        id_token: `dev:${encodeURIComponent(devName)}`,
        display_name: devName,
      });
    } catch (e) {
      console.warn("dev login failed:", e);
      return false;
    }
  }

  let profile;
  try {
    await liff.init({ liffId });
    if (!liff.isLoggedIn()) {
      liff.login();
      return null; // page reloads after LINE login redirects back
    }
    // A session can hand back a token that has already run out — see
    // lineTokenIsFresh. Identifying with it could only fail.
    if (!lineTokenIsFresh() && restartLineSession()) return null;
    watchLineToken();
    // Resolved once per LIFF session rather than on every page load:
    // navigating between the four organizer pages is a full document
    // load each time, and this is a whole network round trip before
    // anything else can start. The ID token itself still comes fresh
    // from liff.getIDToken() on every request (see authHeader) — only
    // the "who is this" lookup is reused. Cleared by profile.html when
    // the player renames themselves.
    const cached = sessionStorage.getItem("vf_identity");
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch (e) {
        sessionStorage.removeItem("vf_identity");
      }
    }
    profile = await liff.getProfile();
  } catch (e) {
    // Everything above is LINE's side: no LIFF SDK, a bad liff id, login
    // declined. Nothing we retry will change that, and the caller should
    // say "open this in LINE".
    console.warn("LIFF unavailable:", e);
    return null;
  }

  // Identifying is our own backend, and on a free Render instance the
  // first request of the day wakes it from sleep — 30 seconds, often a
  // 502 on the way. Treating that like a LINE login failure is what put
  // "請用 LINE 開啟" on screen for someone who was perfectly logged in,
  // and it happened constantly. So: retry a cold start, and if it still
  // won't answer, report a *server* failure (false), not a login one.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const identified = await postJson(apiBase, "/players/identify", {
        id_token: liff.getIDToken(),
        display_name: profile.displayName,
        picture_url: profile.pictureUrl,
      });
      try {
        sessionStorage.setItem("vf_identity", JSON.stringify(identified));
      } catch (e) {
        // Private mode — just means we identify again next page.
      }
      return identified;
    } catch (e) {
      // A rejected token is a real answer, not a cold start; retrying it
      // just delays the same 401 three times over.
      if (e.status === 401 || e.status === 403) {
        console.warn("Identity rejected:", e);
        return null;
      }
      console.warn(`identify attempt ${attempt + 1} failed:`, e);
      if (attempt < 2) await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
    }
  }
  return false;
}

async function postJson(apiBase, path, body, method) {
  // Never send a request already known to fail. The session restarts
  // instead, and the tap is made again with a token LINE will accept.
  if (!lineTokenIsFresh() && restartLineSession()) {
    const error = new Error("登入已過期，正在重新登入…");
    error.status = 401;
    throw error;
  }
  // Whichever control was pressed shows the wait, and stops taking
  // taps, for as long as this takes — see markBusy. Claimed here rather
  // than at the call sites so no write can forget to do it. The page-level
  // marker beside it covers the optimistic actions, whose button is gone
  // before markBusy can reach it — see beginPendingWrite.
  const done = markBusy(_pressed);
  // Identity resolution is a POST because it carries the LINE token, not
  // because the visitor changed anything. Showing "儲存中…" while merely
  // opening or refreshing a page makes a passive read look like a write.
  const settled = identifiesOnly(path) ? () => {} : beginPendingWrite();
  let res;
  let data;
  try {
    res = await fetch(`${apiBase}${path}`, {
      method: method || "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify(body),
    });
    data = await res.json().catch(() => ({}));
  } finally {
    done();
    settled();
  }
  if (!res.ok) {
    const raw = data.detail || res.statusText;
    if (identityLapsed(res.status, raw)) restartLineSession(res.status === 401);
    const error = new Error(translateApiError(raw));
    // Callers that retry need to tell "the server didn't answer" from
    // "the server answered no" — see initLiffIdentity, which must not
    // retry a rejected token three times over.
    error.status = res.status;
    // The untranslated detail, for the few callers that need to detect a
    // specific condition programmatically rather than just display it —
    // see e.g. offerToRaiseCapacity, which looks for "capacity" in this.
    error.rawMessage = raw;
    throw error;
  }
  // Anything that writes invalidates every cached read: taking leave
  // changes the season detail, adding a member changes the season list,
  // creating a club changes the club list. Dropping the lot is cheap
  // (a handful of small keys) and can't get the invalidation wrong.
  //
  // Except identify, which writes nothing anybody reads — and which is
  // the *first* request of every single page load. Clearing the cache
  // there emptied it before one cached read could ever be served, so
  // stale-while-revalidate never once did the job it exists for:
  // measured at 2.2s to first content cold and 1.9s warm, which is no
  // cache at all. See identifiesOnly.
  if (!identifiesOnly(path)) {
    clearResponseCache({ keepPeople: keepsPeople(path) });
  }
  return data;
}

/** Whether a write can leave the in-memory roster and guest lists alone.
 *
 * Those two lists only change when somebody is introduced, renamed or
 * removed, and they were being thrown away after *every* write — so each
 * 請假 cost two extra round trips re-reading lists that could not have
 * changed. Recording an absence, undoing one, and giving up a queue place
 * are the three most-used actions in the app and none of them touch a
 * person, so they are named here and everything else keeps the old
 * behaviour. The list is deliberately short: being wrong in this
 * direction shows a stale picker, being wrong the other way costs one
 * request, so anything not obviously safe stays off it.
 */
function keepsPeople(path) {
  return (
    /^\/absences$/.test(path) ||
    /^\/absences\/\d+\/cancel$/.test(path) ||
    /^\/waitlist\/\d+\/cancel$/.test(path)
  );
}

/** Resolving who the caller is, which changes nothing any cached read
 * returns. It is a POST only because it carries a LINE token in a body
 * rather than a URL, and it runs first on every page load — so treating
 * it like an ordinary write meant the response cache was wiped before it
 * could serve anything, on every load, forever. */
function identifiesOnly(path) {
  return /^\/players\/identify$/.test(path);
}

const CACHE_PREFIX = "vf_cache:";

function readCache(url) {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + url);
    return raw === null ? null : JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function writeCache(url, data) {
  try {
    localStorage.setItem(CACHE_PREFIX + url, JSON.stringify(data));
  } catch (e) {
    // Quota or private mode — caching is an optimisation, never required.
  }
}

/**
 * GETs JSON, calling `onData(data, { fresh })` up to twice: once
 * immediately with the cached copy if there is one (`fresh: false`),
 * then again with the network's copy if it differs (`fresh: true`).
 * Renderers must therefore be idempotent — they all are, since each one
 * rebuilds its container from scratch.
 *
 * The `fresh` flag matters for anything irreversible. A cached copy is a
 * guess about the world; only the network's answer is authoritative, so
 * "there are no clubs, forget the remembered one" must key off `fresh`,
 * not off a stale copy that happened to be empty.
 *
 * If the network fails but a cached copy was served, the failure is
 * swallowed: the page is already showing something usable. With no cache
 * to fall back on, it throws, and the caller shows its error state.
 */
async function getJsonSWR(url, onData, options) {
  const cached = readCache(url);
  const hadCache = cached !== null;
  if (hadCache) onData(cached, { fresh: false });

  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      // With a cached copy on hand, a refused read used to be swallowed
      // below and the old copy kept — so an expired session showed no
      // error at all, only data that quietly stopped changing. A lapsed
      // identity restarts the session instead of hiding behind the cache.
      if (res.status === 401 || res.status === 404) {
        const data = await res.json().catch(() => ({}));
        if (identityLapsed(res.status, data.detail)) restartLineSession(res.status === 401);
      }
      throw new Error(res.statusText);
    }
    const fresh = await res.json();
    const changed = JSON.stringify(fresh) !== JSON.stringify(cached);
    writeCache(url, fresh);
    if (!hadCache || changed) onData(fresh, { fresh: true });
    return fresh;
  } catch (e) {
    if (hadCache) {
      console.warn("Revalidation failed, keeping cached copy:", url, e);
      return cached;
    }
    throw e;
  }
}

/** This club's people, kept for the life of the page. Declared here
 * rather than beside fetchClubMembers so it is initialised before
 * clearResponseCache can reach for it. */
const _clubMemberCache = {};
const _myGuestCache = {};

/** People this viewer has brought to this club before, for the pickers.
 *
 * Retyping a friend's name every week is how one person becomes three
 * rows in the players table, each carrying its own money — so the app
 * offers the ones already brought as a tap. Returns [] on any failure:
 * a picker that can't load is a picker that isn't there, not a broken
 * page, and the text field beside it still works. */
async function fetchMyGuests(apiBase, clubId) {
  if (Object.prototype.hasOwnProperty.call(_myGuestCache, clubId)) {
    return _myGuestCache[clubId];
  }
  try {
    const res = await fetch(`${apiBase}/clubs/${clubId}/my-guests`, {
      headers: authHeader(),
    });
    const guests = res.ok ? await res.json() : [];
    if (res.ok) _myGuestCache[clubId] = guests;
    return guests;
  } catch (e) {
    console.warn("Could not load previous guests:", e);
    return [];
  }
}

/** Drops every cached response. Called after anything that writes, so
 * the next page doesn't paint from a copy we just invalidated. */
function clearResponseCache(options) {
  // The in-memory club roster goes with them, or adding a guest would
  // leave the substitute picker offering the list from before. Same for
  // the "people I've brought" list, which grows the moment somebody
  // brings a new one. A write that provably introduces nobody keeps them
  // — see keepsPeople.
  if (!(options && options.keepPeople)) {
    for (const key of Object.keys(_clubMemberCache)) delete _clubMemberCache[key];
    for (const key of Object.keys(_myGuestCache)) delete _myGuestCache[key];
  }
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(CACHE_PREFIX)) localStorage.removeItem(key);
    }
  } catch (e) {
    // Nothing to do — a stale cache self-corrects on the next revalidate.
  }
}

/** DELETE with the caller's token, throwing the API's own error detail —
 * the mirror of postJson, including clearing the response cache so the
 * next read doesn't paint from a copy of something just deleted. */
async function deleteJson(apiBase, path) {
  // Same as postJson: not a request that is already known to fail.
  if (!lineTokenIsFresh() && restartLineSession()) {
    const error = new Error("登入已過期，正在重新登入…");
    error.status = 401;
    throw error;
  }
  const done = markBusy(_pressed);
  const settled = beginPendingWrite();
  let res;
  try {
    res = await fetch(`${apiBase}${path}`, {
      method: "DELETE",
      headers: authHeader(),
    });
  } finally {
    done();
    settled();
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const raw = data.detail || res.statusText;
    if (identityLapsed(res.status, raw)) restartLineSession(res.status === 401);
    const error = new Error(translateApiError(raw));
    error.status = res.status;
    error.rawMessage = raw;
    throw error;
  }
  clearResponseCache();
}

/** Everyone in a club (fixed member or not) — the pool the 指定代打
 * picker offers before falling back to typing a name. Public endpoint,
 * no auth needed. Returns [] rather than throwing on failure, since a
 * missing picker list should just fall back to the manual text input,
 * not break the page. */
async function fetchClubMembers(apiBase, clubId) {
  // Held for the life of the page, because every action ends in a
  // reload and each reload was fetching this list again — a whole
  // network round trip, on every tap, for a list that only changes when
  // somebody joins the club. clearResponseCache() drops it, and that
  // runs after every write, so a roster change is still picked up.
  if (Object.prototype.hasOwnProperty.call(_clubMemberCache, clubId)) {
    return _clubMemberCache[clubId];
  }
  try {
    const res = await fetch(`${apiBase}/clubs/${clubId}/members`, {
      headers: authHeader(),
    });
    const members = res.ok ? await res.json() : [];
    if (res.ok) _clubMemberCache[clubId] = members;
    return members;
  } catch (e) {
    console.warn("Could not load club members:", e);
    return [];
  }
}

/** Lets a loader discard its own result when a newer run has started.
 *
 * getJsonSWR calls its callback twice — once from cache, once from the
 * network — and the season picker turns each of those into a fresh
 * `onSeasonChange`, so two loads are routinely in flight at once. They
 * are not guaranteed to finish in order: the older one finishing last
 * paints its stale copy over the newer one's. That is what made a
 * recorded payment flick back to 收款 a second after it had settled,
 * and it can put any screen a whole request behind reality.
 *
 * Usage: take a ticket at the top of the loader, and check it after
 * every await, before touching the DOM or shared state.
 */
function staleGuard() {
  let latest = 0;
  return {
    take: () => ++latest,
    current: (ticket) => ticket === latest,
  };
}

/** How many times this page's own copy of the data has been changed
 * locally, without waiting for the server.
 *
 * This is the answer to 「按了成功結果又切回去又切回來」. Refreshing after
 * a change used to fire a GET that overtook the *next* change still
 * sitting in the write queue: the read went out at 676ms and the write it
 * was meant to report on at 677ms. The answer therefore described the
 * roster as it was before that tap, and render() trusted it and painted
 * the tap away — then the next refresh put it back, half a second later.
 *
 * staleGuard orders responses against each other. This orders them
 * against what the person has already done, which is the thing nothing
 * was checking: a read that set off before the latest local change is
 * answering a question about a world that no longer exists, and must be
 * dropped rather than painted.
 */
let _localRevision = 0;

function bumpLocalRevision() {
  _localRevision += 1;
  return _localRevision;
}

function localRevision() {
  return _localRevision;
}

/** Reloads the page once when its markup is older than its scripts.
 *
 * GitHub Pages serves HTML with `Cache-Control: max-age=600`, and LINE's
 * in-app browser holds it a good deal longer than that. The scripts and
 * the stylesheet carry a `?v=` cache-buster and the HTML does not, so
 * "cached HTML, fresh script" is a normal state, and a change that lives
 * in the markup (a new form field, a new section) stays invisible until
 * the page expires.
 *
 * The deploy stamps the same build id into a <meta> and onto every
 * script's URL. If they disagree, the HTML is stale: reload it with
 * a cache-busting parameter, exactly once — the `vf_reloaded` guard
 * stops a mismatch we can't fix (a missing meta, a failed deploy) from
 * becoming a refresh loop.
 */
function assertFreshBuild() {
  const meta = document.querySelector('meta[name="vf-build"]');
  const script = document.querySelector('script[src^="js/common/"]');
  if (!meta || !script) return; // local file, or before the deploy stamps it
  const wanted = (script.getAttribute("src").split("?v=")[1] || "").trim();
  const have = (meta.getAttribute("content") || "").trim();
  if (!wanted || !have || wanted === have) return;

  try {
    if (sessionStorage.getItem("vf_reloaded") === wanted) return;
    sessionStorage.setItem("vf_reloaded", wanted);
  } catch (e) {
    return; // private mode: better a stale page than a reload loop
  }
  const url = new URL(location.href);
  url.searchParams.set("v", wanted);
  location.replace(url.toString());
}

assertFreshBuild();

/** Which API this page talks to.
 *
 * Every page had the production URL hardcoded, so a page served from a
 * laptop still called the live server — which is worse than it sounds in
 * both directions: local sign-in is off there so nothing worked, and
 * anything that *did* work was writing to the real club's data.
 *
 * The API is taken from whatever host served the page, not written down
 * as "localhost": on a phone opening 192.168.1.101:5500, localhost is
 * the *phone*, so a fixed name would send every request into a device
 * that isn't running anything. Same host, port 8000.
 */
function apiBase() {
  if (!isLocalDev()) return "https://volleyflow.onrender.com";
  return `http://${location.hostname}:8000`;
}
