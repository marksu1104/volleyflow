// Feedback while something is happening: busy controls, the saving marker,
// loading and empty states, and toasts.

/** Disables a button and swaps its label while an async action runs,
 * restoring it on failure (a successful action usually re-renders the
 * whole page anyway). Makes a tap feel acknowledged immediately instead
 * of sitting dead until the network call resolves. */
/* ---- "this is working" ----
 *
 * Every control that waits on the network says so, and none of them had
 * to be changed one at a time to do it. Marking them by hand meant
 * remembering at twenty-odd call sites, and the ones that got forgotten
 * are exactly the ones reported as unresponsive.
 *
 * Instead: note which control was pressed, and let the request itself
 * mark it. Every write in this app goes through postJson or deleteJson,
 * so that is the one place that knows a wait has begun and the one place
 * that knows it has ended.
 */
let _pressed = null;

if (typeof document !== "undefined" && document.addEventListener) {
  // Capture phase, so it runs before the handler that fires the request.
  document.addEventListener(
    "click",
    (e) => {
      _pressed = e.target && e.target.closest ? e.target.closest("button, .pick-row") : null;
    },
    true
  );
}

/** Held so a control stays busy through several requests from one tap
 * (adding a drop-in also sets their gender, for instance) and only
 * clears when the last of them finishes. */
const _busyDepth = new WeakMap();

/** Long enough that a fast action just happens, short enough that a slow
 * one never feels ignored. Without it every request flashed a spinner
 * for 40ms, which reads as a glitch rather than as progress. */
const BUSY_AFTER_MS = 140;

function markBusy(el) {
  if (!el || !el.classList) return () => {};
  const depth = (_busyDepth.get(el) || 0) + 1;
  _busyDepth.set(el, depth);
  if ("disabled" in el) el.disabled = true;
  el.setAttribute("aria-busy", "true");
  const timer = setTimeout(() => el.classList.add("is-busy"), BUSY_AFTER_MS);
  return () => {
    clearTimeout(timer);
    const left = (_busyDepth.get(el) || 1) - 1;
    _busyDepth.set(el, left);
    if (left > 0) return;
    el.classList.remove("is-busy");
    el.removeAttribute("aria-busy");
    if ("disabled" in el) el.disabled = false;
  };
}

/** Says "still saving" once, for the page, for changes that are already
 * on screen.
 *
 * An optimistic action cannot use markBusy and never could: the repaint
 * that makes the tap feel instant replaces the button in the same frame,
 * so by the time the spinner is due, 140ms later, it is being put on a
 * node that has been off the page for 138ms. That is why the busy state
 * was invisible on exactly the two controls it was being judged by,
 * 請假 and 取消請假.
 *
 * Marking the *replacement* button instead would be worse: it would grey
 * out 取消請假 for half a second immediately after 請假 succeeded, which
 * says the opposite of what happened. The honest signal is that the
 * change is made and the server is still being told, so that is what
 * this shows — small, out of the way, and only once however many
 * requests are outstanding.
 */
let _pendingWrites = 0;
let _pendingTimer = null;

/** Long enough that the common case — a change that saves in under half
 * a second — never mentions itself at all. */
/** A write that finishes in about a second needs no announcement at all
 * — the change is already on screen, and saying "儲存中" after the fact
 * reads as "that hasn't taken effect yet", which is the opposite of what
 * happened. At 450ms this appeared on nearly every action and vanished
 * ~300ms later: too brief to read, long enough to notice, and reported
 * as exactly that noise.
 *
 * So it now waits for a wait worth mentioning, and once it does appear
 * it stays long enough to be read rather than blinking. The pair is what
 * matters: a high threshold alone would still flash when a write happens
 * to land just past it. */
const PENDING_AFTER_MS = 1200;
const PENDING_MIN_VISIBLE_MS = 700;
let _pendingShownAt = 0;

function pendingIndicator() {
  let el = document.getElementById("vf-pending");
  if (!el) {
    el = document.createElement("div");
    el.id = "vf-pending";
    el.className = "pending-chip";
    el.setAttribute("role", "status");
    el.textContent = "儲存中…";
    document.body.appendChild(el);
  }
  return el;
}

function beginPendingWrite() {
  if (typeof document === "undefined" || !document.body) return () => {};
  _pendingWrites += 1;
  if (_pendingWrites === 1 && !_pendingTimer) {
    _pendingTimer = setTimeout(() => {
      _pendingTimer = null;
      if (_pendingWrites > 0) {
        _pendingShownAt = Date.now();
        pendingIndicator().classList.add("shown");
      }
    }, PENDING_AFTER_MS);
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    _pendingWrites = Math.max(0, _pendingWrites - 1);
    if (_pendingWrites > 0) return;
    clearTimeout(_pendingTimer);
    _pendingTimer = null;
    const el = document.getElementById("vf-pending");
    if (!el || !el.classList.contains("shown")) return;
    // Shown for at least long enough to read. Hiding it the instant the
    // write lands is what turned it into a blink on every action.
    const shownFor = Date.now() - _pendingShownAt;
    const remaining = PENDING_MIN_VISIBLE_MS - shownFor;
    if (remaining <= 0) {
      el.classList.remove("shown");
      return;
    }
    setTimeout(() => {
      // Another write may have started while this one was finishing;
      // that one owns the chip now.
      if (_pendingWrites === 0) el.classList.remove("shown");
    }, remaining);
  };
}

/** Runs something with a named control showing the wait — for the few
 * places that know better than the click does which element to mark. */
async function whileBusy(el, action) {
  const done = markBusy(el);
  try {
    return await action();
  } finally {
    done();
  }
}

let _loadingEscalation = null;

/** Says "still loading" in the same box that will later hold either the
 * content or the empty state, so those three states are never confusable.
 * Escalates its wording after a few seconds: the backend is on a free
 * tier that sleeps, and a silent 30-second wait is indistinguishable
 * from a broken app unless the page says what's happening. */
function showPageLoading(container) {
  if (!container) return;
  container.innerHTML = `
    <div class="page-state">
      <div class="spinner" role="status" aria-label="載入中"></div>
      <p data-loading-text>載入中…</p>
    </div>
  `;
  clearTimeout(_loadingEscalation);
  _loadingEscalation = setTimeout(() => {
    const text = container.querySelector("[data-loading-text]");
    if (text) text.textContent = "伺服器正在啟動，首次開啟約需 30 秒。";
  }, 4000);
}

function blockLoadingHtml(text) {
  return (
    '<div class="block-state"><i class="spinner sm"></i>' +
    `<span>${escapeHtml(text || "載入中…")}</span></div>`
  );
}

function showPageError(container, detail) {
  clearTimeout(_loadingEscalation);
  if (!container) return;
  container.innerHTML = `
    <div class="empty-state">
      <h3>連不上伺服器</h3>
      <p>可能是網路不穩或伺服器正在啟動，請稍後再試。${detail ? `<br><span style="font-size:.75rem;opacity:.7">${escapeHtml(detail)}</span>` : ""}</p>
      <button type="button" class="btn btn-primary" onclick="location.reload()">重新載入</button>
    </div>
  `;
}

function stopLoadingEscalation() {
  clearTimeout(_loadingEscalation);
}

/** The "there's nothing here yet, and here's what to do about it" block.
 * Every page that can legitimately have no data (a club with no season,
 * a player in no club) renders one of these instead of leaving its
 * styled-but-empty cards on screen, which reads as a broken page rather
 * than an empty one. `action` is optional: {label, href}. */
function emptyStateHtml(title, body, action) {
  return `
    <div class="empty-state">
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(body)}</p>
      ${
        !action
          ? ""
          : action.href
            ? `<a class="btn btn-primary" href="${action.href}">${escapeHtml(action.label)}</a>`
            : `<button type="button" class="btn btn-primary" onclick="${escapeHtml(action.onclick)}">${escapeHtml(action.label)}</button>`
      }
    </div>
  `;
}

/** What a management screen shows when there is no season to show, which
 * is two different situations wearing the same shape:
 *
 *   - this club hasn't started a season yet. Each page says what *it*
 *     would show once one exists, so that copy stays on the page;
 *   - there is no club at all — a brand new person, and on launch day,
 *     everybody.
 *
 * The second was only handled on two of the four management screens.
 * Emptying the database to open the app to real users (2026-09-13) is
 * what made that obvious: 帳務 and 名單 both said 「這個球隊尚未開季」
 * and offered to copy an invite link, for a club that did not exist.
 * Saying the wrong thing confidently is worse on somebody's first
 * screen than anywhere else.
 */
function noClubYetHtml() {
  return emptyStateHtml(
    "尚未加入任何球隊",
    "請先到會員頁用 LINE 開啟，建立一個球隊，或用管理員給你的連結加入。",
    { label: "前往會員頁", href: "member.html" }
  );
}

/** What to show when initLiffIdentity came back falsy. Its two failures
 * need different words: one is the reader's to fix by opening the page
 * in LINE, the other is the server being asleep and fixes itself. Every
 * page shows the same thing, so it's written once here. */
function signInFailureHtml(identified) {
  if (identified === false) {
    return emptyStateHtml(
      "連不上伺服器",
      "伺服器正在啟動，約需 30 秒，請稍後重新載入。",
      { label: "重新載入", onclick: "location.reload()" }
    );
  }
  // On a laptop that advice is impossible to follow — LINE cannot open
  // localhost — and it's what a page with no ?as= showed. Say the thing
  // that actually works here instead.
  if (isLocalDev()) {
    return emptyStateHtml(
      "尚未選擇身分",
      "本機沒有 LINE 可以登入，要在網址後面加上 ?as=名字 才知道身分，例如 ?as=小明。"
    );
  }
  return emptyStateHtml(
    "請用 LINE 開啟",
    "名單和帳務只有球隊成員看得到，所以需要先用 LINE 登入。請從 LINE 裡的連結開啟這一頁。"
  );
}

/** A short message that doesn't take the screen away from you.
 *
 * Every failure used to be an alert(), 48 of them, and inside a LIFF
 * webview that is a system modal: it covers the page, it has to be
 * dismissed before anything else can happen, and it looks like the app
 * crashed rather than like a form needing another go. This says the same
 * thing over the page and gets out of the way.
 *
 * Kept for messages, not decisions — confirm() still blocks, because
 * "are you sure you want to remove this member" genuinely must be
 * answered before anything proceeds.
 */
function toast(message, kind, action) {
  let host = document.getElementById("vf-toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "vf-toasts";
    host.className = "toasts";
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = message;
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.onclick = () => {
      el.remove();
      action.onClick();
    };
    el.appendChild(button);
  }
  host.appendChild(el);
  // Long enough to read a failure reason, short enough not to stack up; longer when there's something to tap.
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }, action ? 6000 : kind === "good" ? 1800 : 3600);
}
