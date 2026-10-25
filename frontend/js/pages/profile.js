// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
const LIFF_ID = "2011156233-6CouG6VI";
let me = null;

(async function init() {
  // This page edits the player itself, so it re-identifies on entry
  // rather than trusting the cached identity, which may point at a row
  // merged away since.
  sessionStorage.removeItem("vf_identity");
  // Signing in is the slowest step; show that it is loading.
  showPageLoading(document.getElementById("boot"));
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  document.getElementById("boot").innerHTML = "";
  if (!identified) {
    const notice = document.getElementById("no-liff");
    notice.innerHTML = signInFailureHtml(identified);
    notice.hidden = false;
    return;
  }
  me = identified;
  document.getElementById("content").hidden = false;
  // The address lives in one place (js/common/clubs.js).
  document.getElementById("oa-link").href = OFFICIAL_ACCOUNT_URL;
  renderProfile();
  showOfficialAccountState();
  if (me.is_developer) showDeveloperReports();
  await renderClubs();
})();

/** The way into problem reports, for the developer only, with the unread
 * count. The server checks again on every report request.
 */
async function showDeveloperReports() {
  document.getElementById("dev-reports").hidden = false;
  try {
    const res = await fetch(`${API_BASE}/reports`, { headers: authHeader() });
    if (!res.ok) return;
    const unread = (await res.json()).filter((r) => !r.read).length;
    document.getElementById("dev-unread").textContent = `${unread} 則未讀`;
  } catch (e) {
    // The link still works without a count.
  }
}

/** 已加入 / 未加入 beside the Official Account, from LINE; nothing when LINE
 * could not be asked (see LineReachableOut).
 */
async function showOfficialAccountState() {
  try {
    const res = await fetch(`${API_BASE}/players/me/line-reachable`, { headers: authHeader() });
    if (!res.ok) return;
    const { reachable } = await res.json();
    const el = document.getElementById("oa-state");
    if (reachable === true) el.innerHTML = '<span class="state-pill ok">已加入</span>';
    else if (reachable === false) el.innerHTML = '<span class="state-pill warn">未加入</span>';
  } catch (e) {
    // The row still opens the account without a state.
  }
}

// 顯示名稱 opens in place under its row.
document.getElementById("name-row").addEventListener("click", () => {
  const edit = document.getElementById("name-edit");
  edit.hidden = !edit.hidden;
  document.getElementById("name-row").setAttribute("aria-expanded", String(!edit.hidden));
  if (!edit.hidden) document.getElementById("name-input").focus();
});

function renderProfile() {
  paintSelfAvatar(document.getElementById("avatar-lg"), me);
  document.getElementById("prof-name").textContent = me.name;
  document.getElementById("name-value").textContent = me.name;
  document.getElementById("name-input").value = me.name;
  for (const btn of document.querySelectorAll("#gender-pick [data-gender]")) {
    btn.classList.toggle("selected", me.gender === btn.dataset.gender);
  }
}

async function saveName() {
  const name = document.getElementById("name-input").value.trim();
  if (!name) { toast("名稱不得為空白"); return; }
  // Say something either way, so the button never seems to do nothing.
  if (name === me.name) { toast("名稱未變更"); return; }
  try {
    const updated = await postJson(API_BASE, `/players/${me.id}/name`, { name }, "PUT");
    // The cached identity holds the old name.
    sessionStorage.removeItem("vf_identity");
    me.name = updated.name;
    renderProfile();
    document.getElementById("name-edit").hidden = true;
    document.getElementById("name-row").setAttribute("aria-expanded", "false");
    toast(
      updated.name !== name
        ? `「${name}」已經有人使用，改成了「${updated.name}」`
        : "已儲存"
    );
  } catch (e) {
    toast("儲存失敗：" + e.message);
  }
}

document.getElementById("gender-pick").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-gender]");
  if (!btn) return;
  const previous = me.gender;
  me.gender = btn.dataset.gender;  // flip now, confirm after
  renderProfile();
  try {
    const updated = await postJson(API_BASE, `/players/${me.id}/gender`, { gender: btn.dataset.gender }, "PUT");
    sessionStorage.removeItem("vf_identity");
    me.gender = updated.gender;
    renderProfile();
  } catch (e) {
    me.gender = previous;
    renderProfile();
    toast("設定失敗：" + e.message);
  }
});


/** One club: its name, the role if organizer, and its balance (已結清 when
 * settled, nothing when unknown; see balanceBadgeHtml). Opens that club's
 * breakdown on the ledger page.
 */
function clubRowHtml(club) {
  const roleTag = club.role === "organizer" ? '<span class="role-tag">管理員</span>' : "";
  const balHtml = balanceBadgeHtml(club.balance, { settled: "已結清" });
  return `
    <a class="lrow" href="ledger.html?club=${encodeURIComponent(club.id)}">
      <i class="club-dot">${escapeHtml(firstGrapheme(club.name))}</i>
      <span class="lrow-k"><span class="lrow-name">${escapeHtml(club.name)}</span>${roleTag}</span>
      ${balHtml}<span class="lrow-go" aria-hidden="true"><svg viewBox="0 0 16 16" width="16" height="16"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
    </a>
  `;
}

/** 我的球隊: shows a loading state at once, paints the clubs from the last
 * visit's copy (getJsonSWR), and gets names and balances in one request
 * so the rows paint once.
 */
async function renderClubs() {
  const list = document.getElementById("clubs-list");
  showPageLoading(list);
  try {
    await getJsonSWR(
      `${API_BASE}/players/${me.id}/clubs`,
      (clubs) => {
        stopLoadingEscalation();
        list.innerHTML =
          clubs.map((c) => clubRowHtml(c)).join("") ||
          '<div class="lrow static"><span class="lrow-k">尚未加入任何球隊</span></div>';
      },
      { headers: authHeader() }
    );
  } catch (e) {
    stopLoadingEscalation();
    showPageError(list, e.message);
  }
}
