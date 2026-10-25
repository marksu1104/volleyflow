// Talking to the API: who the user is, requests, and the response cache.

/** Whether the page is served from a development machine (localhost or a
 * private LAN address, so a phone on the same wifi works) rather than the
 * real site. Not a security boundary: it only means the real site never
 * offers a dev token. The real check is on the server
 * (auth.verify_id_token).
 */
function isLocalDev() {
  const h = location.hostname;
  if (["localhost", "127.0.0.1", "[::1]"].includes(h)) return true;
  // The three private IPv4 ranges (RFC 1918).
  return (
    /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h) ||
    /^192\.168\.\d{1,3}\.\d{1,3}$/.test(h) ||
    /^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(h)
  );
}

/** Who to sign in as, from `?as=<name>`, for local work without LINE:
 *
 *     http://localhost:5500/member.html?as=小明
 *
 * Remembered for the session; `?as=` with no name clears it.
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

/** How long before a LINE ID token expires it is treated as spent, so a
 * request cannot arrive just after it lapsed.
 */
const LINE_TOKEN_MARGIN_SECONDS = 120;

/** Whether the LINE ID token this page would send is still valid. LINE ID
 * tokens last an hour and the SDK does not renew them, so a page left
 * open goes stale. True whenever there is nothing to check (local
 * sign-in, no LIFF, an undecodable token).
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

/** Starts the LINE session over to get a new ID token: LINE has no way to
 * renew one in place, but logging out and reloading signs in again.
 *
 * `signOut` is false when the token is fine but our record of the person
 * is gone; then only the cached identity is forgotten. At most once every
 * few minutes, so a server that keeps refusing does not cause a reload
 * loop. Returns whether it restarted.
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
      // Reloading still helps if logout is unavailable.
    }
  }
  location.reload();
  return true;
}

/** Whether a refusal means the session has lapsed (and should be renewed)
 * rather than an answer to show.
 */
function identityLapsed(status, detail) {
  if (status === 401) {
    return /^(LINE rejected this ID token|Missing bearer token)$/.test(detail || "");
  }
  if (status === 404) return detail === "No player identified for this LINE account yet";
  return false;
}

let _watchingLineToken = false;

/** Checks the token when the page comes back into view, before anything is
 * tapped.
 */
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

/** The Authorization header for the current session, or {} without one.
 */
function authHeader() {
  const dev = devIdentityName();
  // Header values must be ASCII; auth.verify_id_token decodes the name.
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

/** Starts LIFF (signing in by redirect if needed) and resolves the user
 * through POST /players/identify. Works in the LINE app and in a desktop
 * browser.
 *
 * Returns the player ({id, name, avatar_url, gender}), or a falsy value saying which side failed:
 *   null  - LINE's side (no LIFF, login declined, token rejected): open it in LINE.
 *   false - our server did not answer after three tries, almost always a sleeping free-tier instance: offer to reload.
 */
async function initLiffIdentity(apiBase, liffId) {
  // Skips LINE (see devIdentityName) but goes through the same identify
  // call, so everything after behaves as for a real user.
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
    // The session may hand back an expired token; see lineTokenIsFresh.
    if (!lineTokenIsFresh() && restartLineSession()) return null;
    watchLineToken();
    // Identified once per session rather than on every page load. The ID
    // token itself is still read fresh for each request. profile.html
    // clears this after a rename.
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
    // LINE's side failed: retrying cannot help.
    console.warn("LIFF unavailable:", e);
    return null;
  }

  // Our own server: the first request of the day may hit a sleeping free
  // instance (about 30 seconds, sometimes a 502), so retry, and report a
  // server failure rather than a login failure.
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
        // Private mode: identify again on the next page.
      }
      return identified;
    } catch (e) {
      // A rejected token is an answer, not a cold start.
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

/** POSTs or PUTs a JSON body with the caller's token and returns the
 * parsed response, or throws with the API's error detail translated.
 */
async function postJson(apiBase, path, body, method) {
  // Never send a token known to be expired: restart the session instead.
  if (!lineTokenIsFresh() && restartLineSession()) {
    const error = new Error("登入已過期，正在重新登入…");
    error.status = 401;
    throw error;
  }
  // The pressed control shows the wait and stops taking taps (see
  // markBusy); the page marker covers optimistic actions whose button is
  // already gone (see beginPendingWrite).
  const done = markBusy(_pressed);
  // Identifying writes nothing, so it shows no saving marker.
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
    // Lets callers tell "no answer" from "answered no".
    error.status = res.status;
    // The untranslated detail, for callers that check for a specific
    // condition.
    error.rawMessage = raw;
    throw error;
  }
  // Any write invalidates every cached read; dropping them all is cheap
  // and cannot get invalidation wrong. Except identify, which writes
  // nothing and runs first on every page load.
  if (!identifiesOnly(path)) {
    clearResponseCache({ keepPeople: keepsPeople(path) });
  }
  return data;
}

/** Whether a write leaves the in-memory roster and guest lists valid: the
 * most common actions (an absence, undoing one, leaving the queue)
 * introduce, rename or remove nobody. Anything not obviously safe stays
 * off the list.
 */
function keepsPeople(path) {
  return (
    /^\/absences$/.test(path) ||
    /^\/absences\/\d+\/cancel$/.test(path) ||
    /^\/waitlist\/\d+\/cancel$/.test(path)
  );
}

/** Identifying the caller, which changes nothing any cached read returns.
 */
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
    // Quota or private mode: caching is optional.
  }
}

/** Stale-while-revalidate GET: calls `onData(data, { fresh })` with this
 * device's cached copy at once (`fresh: false`), then with the network's
 * copy if it differs (`fresh: true`). Every page load chains several
 * sequential requests, so painting from the cache first hides the wait.
 * Renderers rebuild their container, so being called twice is safe.
 *
 * Anything irreversible must wait for `fresh`: a cached copy is a guess.
 * If the network fails after a cached copy was shown, the failure is
 * swallowed; with no cache, it throws.
 */
async function getJsonSWR(url, onData, options) {
  const cached = readCache(url);
  const hadCache = cached !== null;
  if (hadCache) onData(cached, { fresh: false });

  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      // A lapsed identity restarts the session instead of hiding behind
      // the cache.
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

/** This club's people, kept for the life of the page. Declared before
 * clearResponseCache, which clears it.
 */
const _clubMemberCache = {};
const _myGuestCache = {};

/** People this viewer has brought to this club before, offered as a tap so
 * the same friend is not typed in as a new person each week. Returns []
 * on failure; the text field still works.
 */
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

/** Drops every cached response; called after every write. */
function clearResponseCache(options) {
  // The in-memory roster and guest list go too, unless the write
  // introduced nobody (see keepsPeople).
  if (!(options && options.keepPeople)) {
    for (const key of Object.keys(_clubMemberCache)) delete _clubMemberCache[key];
    for (const key of Object.keys(_myGuestCache)) delete _myGuestCache[key];
  }
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(CACHE_PREFIX)) localStorage.removeItem(key);
    }
  } catch (e) {
    // A stale cache corrects itself on the next revalidate.
  }
}

/** DELETE with the caller's token; the mirror of postJson, including
 * clearing the cache.
 */
async function deleteJson(apiBase, path) {
  // As in postJson: never send an expired token.
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

/** Everyone in a club, for the 指定代打 picker before falling back to typing a
 * name. Members only. Returns [] on failure, leaving the text field.
 */
async function fetchClubMembers(apiBase, clubId) {
  // Kept for the life of the page instead of refetched on every reload;
  // clearResponseCache drops it after writes that change people.
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

/** Lets a loader discard its result when a newer run has started.
 * getJsonSWR answers twice, so two loads are often in flight and may
 * finish out of order; the older must not paint over the newer.
 *
 * Take a ticket at the top of the loader and check it after every await,
 * before touching the DOM or shared state.
 */
function staleGuard() {
  let latest = 0;
  return {
    take: () => ++latest,
    current: (ticket) => ticket === latest,
  };
}

/** How many times this page has changed its own copy of the data without
 * waiting for the server.
 *
 * A read that left before the latest local change describes the roster as
 * it was and must be dropped rather than painted, or a tap appears to
 * undo itself until the next refresh. staleGuard orders responses against
 * each other; this orders them against what the person has already done.
 */
let _localRevision = 0;

function bumpLocalRevision() {
  _localRevision += 1;
  return _localRevision;
}

function localRevision() {
  return _localRevision;
}

/** Reloads the page once when its HTML is older than its scripts.
 *
 * The scripts carry a `?v=` build stamp and the HTML does not, and LINE's
 * browser caches HTML for a long time, so cached HTML with fresh scripts
 * is normal and would hide markup changes. The deploy writes the build
 * into a <meta> and every script URL; on a mismatch the page reloads with
 * a cache-busting parameter, once (`vf_reloaded` prevents a loop).
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

/** Which API this page talks to: production on the real site, otherwise
 * port 8000 on whatever host served the page (on a phone, localhost would
 * be the phone).
 */
function apiBase() {
  if (!isLocalDev()) return "https://volleyflow.onrender.com";
  return `http://${location.hostname}:8000`;
}
