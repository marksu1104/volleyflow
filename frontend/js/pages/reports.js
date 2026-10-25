// localhost -> the local API, anywhere else -> production. See apiBase.
const API_BASE = apiBase();
const LIFF_ID = "2011156233-6CouG6VI";

// Reports used to be pushed to the developer over LINE, one or two
// messages each out of the free tier's 200 a month — the same allowance
// the short-roster alert needs. They are stored now and read here, on
// demand, at no cost. The trade is that nothing pings anybody: this
// page, and the unread count on the profile page, are the whole signal.

/** The server stores UTC and doesn't say so; the people reading this
 * are in Taiwan, so the zone is named rather than left to whatever the
 * device happens to be set to. */
// Shared with the ledger's 明細 — see stampTime in js/common/format.js. A
// declaration rather than `const localTime = stampTime`, which is only
// usable after the line it sits on has run and left the test harness
// holding an undefined binding.
function localTime(iso) {
  return stampTime(iso);
}

/** One report. Every field is something a user typed or their browser
 * sent, and the person reading it is the developer — so all of it goes
 * through escapeHtml. A report drawn as markup would be a way to run
 * script in the one browser that can read everybody's reports. */
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

/** Screenshots are fetched with the same credentials as the list rather
 * than linked. They can show somebody's name and balance, and they sat
 * at a public URL only because LINE's servers had to fetch them to draw
 * an image message — which no longer happens. An <img> tag can't send
 * an Authorization header, so each one is fetched and handed to the tag
 * as a local blob. A picture that won't load stays hidden: the words
 * are the report. */
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
  // Marked read once they have been drawn, not when they were fetched:
  // a report the page failed to show hasn't been read.
  if (reports.some((r) => !r.read)) {
    postJson(API_BASE, "/reports/read", {}).catch(() => {});
  }
})();
