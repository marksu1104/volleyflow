// localhost -> the local API, anywhere else -> production. See apiBase.
const API_BASE = apiBase();
const LIFF_ID = "2011156233-6CouG6VI";
let me = null;

(async function init() {
  // This page edits the Player row itself, so its id must be authoritative.
  // An organizer may have linked this LINE account to an older roster row
  // since the tab cached its identity; using that deleted id makes every
  // profile write miss the real person. Other pages may keep the fast
  // session cache, but profile deliberately re-identifies once on entry.
  sessionStorage.removeItem("vf_identity");
  // Signing in is the slowest step here; say so rather than show a
  // blank page while it runs.
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
  // Wired here rather than hardcoded in the markup so the address
  // lives in exactly one place (js/common/) — member.html's join screen
  // offers the same link.
  document.getElementById("oa-link").href = OFFICIAL_ACCOUNT_URL;
  renderProfile();
  showOfficialAccountState();
  if (me.is_developer) showDeveloperReports();
  await renderClubs();
})();

/** The way in to the problem reports, for the developer only — the
 * server names them in identify, and checks again on every report
 * request, so this only decides whether a link is drawn.
 *
 * The unread count is the whole signal now: reports stopped being
 * pushed over LINE on 2026-09-15, to keep the free tier's 200 monthly
 * messages for the short-roster alert. */
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

/** 已加入 / 未加入 beside the official account, from LINE itself.
 * Nothing at all when LINE could not be asked: guessing either way
 * would be wrong for somebody (see LineReachableOut). */
async function showOfficialAccountState() {
  try {
    const res = await fetch(`${API_BASE}/players/me/line-reachable`, { headers: authHeader() });
    if (!res.ok) return;
    const { reachable } = await res.json();
    const el = document.getElementById("oa-state");
    if (reachable === true) el.innerHTML = '<span class="state-pill ok">已加入</span>';
    else if (reachable === false) el.innerHTML = '<span class="state-pill warn">未加入</span>';
  } catch (e) {
    // The row still opens the account without a state beside it.
  }
}

// 顯示名稱 opens in place, under its own row.
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
  // Both of these used to be silent: pressing 儲存 with the name
  // unchanged returned without a word, and a successful save said
  // nothing unless the name had been taken. A button that does
  // nothing visible is indistinguishable from a broken one.
  if (name === me.name) { toast("名稱未變更"); return; }
  try {
    const updated = await postJson(API_BASE, `/players/${me.id}/name`, { name }, "PUT");
    // initLiffIdentity caches "who am I" for the session; the name in
    // it is now wrong.
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

// The report form, its screenshot downscaler and sendReport all moved
// to report.html on 2026-09-18 — deleted here rather than left behind
// as dead code, so there is exactly one place that can send a report.

/** One club: its name, the role if it is more than member, and where
 * the money stands. The row opens that club's breakdown on the ledger
 * page. 已結清 is said rather than left blank now that the figure is
 * the row's value (2026-10-20); an unknown balance still shows nothing
 * — see balanceBadgeHtml. */
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

/** 我的球隊.
 *
 * Reported as 「每次都會進入網頁後過很久才載入出現」. Measured at 824ms to
 * the heading and 1,085ms to the rows — not slow against the rest of
 * the app, which is why it took three goes to see what was actually
 * wrong: this card had no loading state at all, so that second was a
 * blank space rather than a visibly busy one, and a blank space reads
 * as a page that has finished and found nothing.
 *
 * Something is on screen immediately, and the club names paint from
 * the last visit's copy without waiting for the network (see
 * getJsonSWR).
 *
 * The balances used to arrive separately, one request per club,
 * painted over the rows once they landed. That was the N+1 behind the
 * original complaint, and it also meant the list rendered twice —
 * names, then names-with-figures — so the row content shifted under
 * the reader a moment after appearing. One request now carries both,
 * and the rows paint once. */
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
