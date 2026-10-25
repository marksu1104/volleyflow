// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
/* This page's own LIFF app. A LIFF app has one endpoint, so each rich
 * menu destination (member.html, ledger.html, report.html) has its own
 * and loads in a single document, with no liff.state redirect to race the
 * SDK's handshake.
 */
const LIFF_ID = "2011156233-LiXoOfF4";

/* Reporting a problem has its own page, which is also where the rich
 * menu's third button lands.
 */

/** Where 返回 goes: back if there is history, otherwise the app's home
 * rather than out of the app.
 */
function goBack() {
  if (document.referrer && document.referrer.includes(location.host)) {
    history.back();
    return;
  }
  location.href = "member.html";
}

let screenshot = null;

/** Shrinks the screenshot to 1280px JPEG before sending: phone screenshots
 * are several megabytes, and the sender may be on a weak connection.
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
        // Sent automatically: where it happened (the page they came from)
        // and the browser.
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

/** The screen the reader came from (the referrer), or this page's name
 * when there is none.
 */
function reportedPage() {
  try {
    if (document.referrer && document.referrer.includes(location.host)) {
      return new URL(document.referrer).pathname.split("/").pop() || "report.html";
    }
  } catch (e) {
    // A malformed referrer must not fail the report.
  }
  return "report.html";
}

(async function init() {
  const state = document.getElementById("page-state");
  showPageLoading(state);
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  stopLoadingEscalation();
  if (!identified) {
    // Reports carry who sent them, so this needs a verified identity.
    state.innerHTML = signInFailureHtml(identified);
    return;
  }
  state.innerHTML = "";
  document.getElementById("content").hidden = false;
})();
