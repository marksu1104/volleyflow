// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
const LIFF_ID = "2011156233-6CouG6VI";

// Reports are stored and read here on demand; this page and the unread
// count on the profile page are the whole signal.

// A report's time in Taipei; see stampTime. Declared as a function rather
// than `const localTime = stampTime` so the test harness can see it.
function localTime(iso) {
  return stampTime(iso);
}

/** One report. Every field came from a user or their browser, so all of it
 * is escaped: this page is read by the one account that sees every
 * report.
 */
function reportHtml(r) {
  const meta = [r.page, r.user_agent].filter(Boolean).map(escapeHtml).join("・");
  return `
    <div class="report">
      <div class="report-head">
        ${r.read ? "" : '<span class="report-new">未讀</span>'}
        <strong>${escapeHtml(r.reporter)}</strong>
        ${r.club ? `<span>${escapeHtml(r.club)}</span>` : ""}
        <span>${escapeHtml(localTime(r.created_at))}</span>
      </div>
      <div class="report-body">${escapeHtml(r.message)}</div>
      ${meta ? `<div class="report-meta">${meta}</div>` : ""}
      ${
        r.has_screenshot
          ? `<img class="report-shot" data-shot="${escapeHtml(r.id)}" alt="截圖" hidden>`
          : ""
      }
    </div>
  `;
}

/** Fetches screenshots with the developer's credentials (an <img> cannot
 * send an Authorization header) and shows them as local blobs; they can
 * show names and balances. A picture that fails stays hidden.
 */
async function loadScreenshots() {
  for (const img of document.querySelectorAll("img[data-shot]")) {
    try {
      const res = await fetch(
        `${API_BASE}/reports/${encodeURIComponent(img.dataset.shot)}/image`,
        { headers: authHeader() }
      );
      if (!res.ok) continue;
      img.src = URL.createObjectURL(await res.blob());
      img.hidden = false;
    } catch (e) {
      // Leave it hidden.
    }
  }
}

function renderReports(reports) {
  const unread = reports.filter((r) => !r.read).length;
  document.getElementById("reports-sub").textContent = reports.length
    ? `共 ${reports.length} 則，未讀 ${unread} 則。回報保留 90 天。`
    : "";
  document.getElementById("reports").innerHTML = reports.length
    ? reports.map(reportHtml).join("")
    : emptyStateHtml("目前沒有回報", "使用者送出的問題回報會顯示在這裡。");
}

(async function init() {
  const box = document.getElementById("reports");
  showPageLoading(box);
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  if (!identified) {
    stopLoadingEscalation();
    box.innerHTML = signInFailureHtml(identified);
    return;
  }
  let res;
  try {
    res = await fetch(`${API_BASE}/reports`, { headers: authHeader() });
  } catch (e) {
    stopLoadingEscalation();
    showPageError(box, e.message);
    return;
  }
  stopLoadingEscalation();
  if (res.status === 403) {
    box.innerHTML = emptyStateHtml("沒有查看權限", "這個頁面僅供開發者查看。");
    return;
  }
  if (res.status === 503) {
    box.innerHTML = emptyStateHtml("尚未設定", "伺服器尚未設定開發者身分，暫時無法查看回報。");
    return;
  }
  if (!res.ok) {
    showPageError(box, `讀取失敗（${res.status}）`);
    return;
  }
  const reports = await res.json();
  renderReports(reports);
  loadScreenshots();
  // Marked read once drawn, not when fetched.
  if (reports.some((r) => !r.read)) {
    postJson(API_BASE, "/reports/read", {}).catch(() => {});
  }
})();
