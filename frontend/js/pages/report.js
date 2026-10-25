// localhost -> the local API, anywhere else -> production. See apiBase.
const API_BASE = apiBase();
/* This page's own LIFF app, not the one member.html uses.
 *
 * A LIFF app has exactly one endpoint URL, so one app cannot open
 * three different pages directly. Trying to fake that with a single
 * app — pointing its endpoint at the folder root and letting a
 * redirect page sort out liff.state — took the menu down twice on
 * 2026-09-18: first an infinite login loop, then a page-not-found.
 * The handshake belongs to the SDK and breaks if anything navigates
 * before liff.init() has run.
 *
 * Three apps, one per destination, is how LINE intends this. Each
 * page initialises its own and lands in a single document load, with
 * no liff.state and nothing to race. See docs/dev-log.md. */
const LIFF_ID = "2011156233-LiXoOfF4";

/* A page of its own, rather than a block at the bottom of 個人資料.
 *
 * Reporting something broken is not a profile setting, and it was
 * sitting under the name field, the gender picker and the club list —
 * so anyone wanting to report a problem had to scroll past three
 * things they did not come for. It is also the third button on the
 * LINE rich menu, and a menu entry needs somewhere of its own to land:
 * pointing it at 個人資料 would have opened a page about the reader
 * rather than about the problem.
 *
 * The submit endpoint is unchanged — POST /reports, same payload — so
 * nothing the developer reads on reports.html had to move with it.
 */

/** Where "返回" goes. A reader normally arrives from 個人資料, but from
 * the rich menu they arrive from nowhere at all, and history.back()
 * would then leave the app entirely. Falls back to the app's home. */
function goBack() {
  if (document.referrer && document.referrer.includes(location.host)) {
    history.back();
    return;
  }
  location.href = "member.html";
}

let screenshot = null;

/**
 * Shrinks the picture before it goes anywhere. A phone screenshot is
 * several megabytes, and the person sending it is by definition having
 * a bad time with this app already — quite possibly on the same weak
 * connection that caused whatever they're reporting. 1280px of JPEG is
 * more than enough to read a UI bug from.
 */
document.getElementById("report-shot").addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  const preview = document.getElementById("report-preview");
  if (!file) {
    screenshot = null;
    preview.hidden = true;
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, 1280 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      screenshot = canvas.toDataURL("image/jpeg", 0.7);
      preview.src = screenshot;
      preview.hidden = false;
    };
    img.onerror = () => {
      screenshot = null;
      preview.hidden = true;
      toast("無法讀取此圖片，請改用其他檔案。");
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
});

async function sendReport() {
  const box = document.getElementById("report-text");
  const text = box.value.trim();
  if (!text) {
    toast("請先說明遇到的問題");
    box.focus();
    return;
  }
  const btn = document.getElementById("report-btn");
  try {
    await whileBusy(btn, async () => {
      await postJson(API_BASE, "/reports", {
        message: text,
        // Sent rather than asked for: "it broke" is only actionable
        // with the where, and nobody types a user agent by hand. The
        // page recorded is where the reader *came from*, not this one —
        // this page is never the thing that broke.
        page: reportedPage(),
        user_agent: navigator.userAgent,
        club_id: currentClubId() ? Number(currentClubId()) : null,
        screenshot,
      });
    });
    box.value = "";
    screenshot = null;
    document.getElementById("report-shot").value = "";
    document.getElementById("report-preview").hidden = true;
    btn.disabled = false;
    btn.textContent = "送出回報";
    toast("已送出，謝謝你的回報。", "good");
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "送出回報";
    toast("送出失敗：" + e.message);
  }
}

/** The screen the reader was on before they came here.
 *
 * The old form lived on 個人資料 and recorded its own filename, which
 * was already only half useful. Now that reporting has a page of its
 * own, recording "report.html" would be useless every single time — so
 * the referrer is used, and this page's name is the honest fallback
 * when there isn't one (arriving from the LINE menu, for instance).
 */
function reportedPage() {
  try {
    if (document.referrer && document.referrer.includes(location.host)) {
      return new URL(document.referrer).pathname.split("/").pop() || "report.html";
    }
  } catch (e) {
    // A malformed referrer is not worth failing a bug report over.
  }
  return "report.html";
}

(async function init() {
  const state = document.getElementById("page-state");
  showPageLoading(state);
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  stopLoadingEscalation();
  if (!identified) {
    // Reports carry who sent them, so this needs a verified identity
    // like every other write in the app.
    state.innerHTML = signInFailureHtml(identified);
    return;
  }
  state.innerHTML = "";
  document.getElementById("content").hidden = false;
})();
