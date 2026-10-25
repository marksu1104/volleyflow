// Feedback while something is happening: busy controls, the saving marker,
// loading and empty states, and toasts.

/* "This is working": every control that waits on the network shows it.
 * Rather than marking controls by hand, the pressed control is noted here
 * and every request (all writes go through postJson or deleteJson) marks
 * it busy.
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

/** Counts requests per control, so one tap that sends several stays busy
 * until the last finishes.
 */
const _busyDepth = new WeakMap();

/** A fast action shows no spinner at all; a slow one never looks ignored.
 */
const BUSY_AFTER_MS = 140;

/** Shows a control as busy while its requests run: disabled, with a
 * spinner in place of its label. Cleared when the last request from that
 * tap finishes.
 */
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

/** One small "still saving" marker for the whole page, for changes already
 * shown on screen.
 *
 * An optimistic action replaces its button in the same frame, so markBusy
 * cannot mark it, and marking the replacement would grey out the opposite
 * action right after this one succeeded. The marker says what is true:
 * the change is made, and the server is still being told.
 */
let _pendingWrites = 0;
let _pendingTimer = null;

/** Short saves never show the marker. */
/** A save that finishes within about a second needs no announcement; once
 * shown, the marker stays long enough to read rather than blinking.
 */
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
    // Shown for at least long enough to read.
    const shownFor = Date.now() - _pendingShownAt;
    const remaining = PENDING_MIN_VISIBLE_MS - shownFor;
    if (remaining <= 0) {
      el.classList.remove("shown");
      return;
    }
    setTimeout(() => {
      // Another write may have started meanwhile; that one owns the
      // marker now.
      if (_pendingWrites === 0) el.classList.remove("shown");
    }, remaining);
  };
}

/** Runs an action with a named control showing the wait, where the click
 * does not identify the right element.
 */
async function whileBusy(el, action) {
  const done = markBusy(el);
  try {
    return await action();
  } finally {
    done();
  }
}

let _loadingEscalation = null;

/** Shows "loading" in the box that will later hold the content or the
 * empty state. The wording escalates after a few seconds, because the
 * free-tier backend sleeps and a silent wait looks broken.
 */
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

/** What one section shows while its own request is still out: a small
 * spinner, rather than a blank space that later pushes everything down.
 */
function blockLoadingHtml(text) {
  return (
    '<div class="block-state"><i class="spinner sm"></i>' +
    `<span>${escapeHtml(text || "載入中…")}</span></div>`
  );
}

/** The request failed: say so and offer to retry. */
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

/** "Nothing here yet, and what to do about it": shown instead of empty
 * cards, which read as a broken page. `action` is optional: {label, href}
 * or {label, onclick}.
 */
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

/** What a management screen shows when there is no club at all (a new
 * user). A club without a season is each page's own empty state.
 */
function noClubYetHtml() {
  return emptyStateHtml(
    "尚未加入任何球隊",
    "請先到會員頁用 LINE 開啟，建立一個球隊，或用管理員給你的連結加入。",
    { label: "前往會員頁", href: "member.html" }
  );
}

/** What to show when sign-in failed: open the page in LINE, or wait for a
 * sleeping server, depending on the cause.
 */
function signInFailureHtml(identified) {
  if (identified === false) {
    return emptyStateHtml(
      "連不上伺服器",
      "伺服器正在啟動，約需 30 秒，請稍後重新載入。",
      { label: "重新載入", onclick: "location.reload()" }
    );
  }
  // On a laptop LINE cannot open localhost, so explain local sign-in
  // instead.
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

/** A short message over the page that does not block it, unlike alert()
 * inside the LINE webview. Messages only; decisions still use confirm().
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
  // Long enough to read; longer when there is something to tap.
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }, action ? 6000 : kind === "good" ? 1800 : 3600);
}
