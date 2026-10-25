// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
/* This page's own LIFF app: one LIFF app has one endpoint, so each rich
 * menu destination has its own.
 */
const LIFF_ID = "2011156233-SWicoUre";
let me = null;
let myClubs = [];

/* Every club's money on one screen. The balances come with the club list
 * (GET /players/{id}/clubs), so the page costs one request; a club's
 * breakdown costs three more, only when opened.
 */

/** Where 返回 goes: back if there is history (from 個人資料), otherwise
 * somewhere in the app rather than out of it (from the LINE menu).
 */
function goBack() {
  if (document.referrer && document.referrer.includes(location.host)) {
    history.back();
    return;
  }
  location.href = "member.html";
}

/** One awaited read for the chained requests behind a tap (getJsonSWR
 * answers twice). A refusal is thrown, so the sheet says so rather than
 * showing nothing.
 */
async function getJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`伺服器回應 ${res.status}`);
  return res.json();
}

const owes = (c) => {
  const n = Number(c.balance);
  return Number.isFinite(n) ? n : 0;
};

/** What the viewer owes, added up, never netted against what clubs owe
 * them: balances never move between clubs (docs/billing-rules.md
 * "Ledger").
 */
function renderOwedTotal(clubs) {
  const box = document.getElementById("owed-total");
  const owing = clubs.filter((c) => owes(c) < 0);
  // Only when more than one club is owed; otherwise it repeats the row.
  if (owing.length < 2) {
    // Cleared, not just hidden: the page paints twice, and a hidden stale
    // figure must not be shown later.
    box.innerHTML = "";
    box.hidden = true;
    return;
  }
  const total = owing.reduce((t, c) => t + owes(c), 0);
  box.className = "lg-total owe";
  box.innerHTML =
    `<span>全部應繳（${owing.length} 隊）</span>` +
    `<strong>$${Math.abs(total)}</strong>`;
  box.hidden = false;
}

function clubRowHtml(club) {
  const roleTag = club.role === "organizer" ? '<span class="role-tag">管理員</span>' : "";
  // See balanceBadgeHtml; on the money page a cleared balance says 已結清.
  const bal = balanceBadgeHtml(club.balance, { settled: "已結清" });
  return `
    <button type="button" class="club-row" onclick="openDetail(${club.id})">
      <i class="club-dot">${escapeHtml(firstGrapheme(club.name))}</i>
      <span class="cr-name">${escapeHtml(club.name)}</span>
      ${roleTag}${bal}
    </button>
  `;
}

// With one club its breakdown opens on arrival, once only, so closing it
// stays closed.
let openOnlyClub = true;

async function renderClubs() {
  const list = document.getElementById("clubs-list");
  showPageLoading(list);
  try {
    await getJsonSWR(
      `${API_BASE}/players/${me.id}/clubs`,
      (clubs) => {
        stopLoadingEscalation();
        myClubs = clubs;
        document.getElementById("clubs-label").textContent = `我的球隊 ${clubs.length} 個`;
        list.innerHTML =
          clubs.map(clubRowHtml).join("") ||
          '<div class="empty">尚未加入任何球隊</div>';
        renderOwedTotal(clubs);
        if (openOnlyClub && clubs.length === 1) {
          openOnlyClub = false;
          openDetail(clubs[0].id);
        }
      },
      { headers: authHeader() }
    );
  } catch (e) {
    stopLoadingEscalation();
    showPageError(list, e.message);
  }
}

/** One club's breakdown: the season in play, its games and this player's
 * ledger, summarised by summaryRows (js/common/season.js), the same
 * function the member page uses.
 */
async function openDetail(clubId) {
  const club = myClubs.find((c) => String(c.id) === String(clubId));
  document.getElementById("detail-backdrop").hidden = false;
  document.getElementById("dt-club").textContent = club ? club.name : "";
  const rows = document.getElementById("dt-rows");
  const total = document.getElementById("dt-total");
  const goto = document.getElementById("dt-goto");
  goto.hidden = false;
  goto.onclick = () => goToClub(clubId);
  total.className = "lg-total";
  total.textContent = "";
  rows.innerHTML = blockLoadingHtml("載入中");

  try {
    const [seasons, ledger] = await Promise.all([
      getJson(`${API_BASE}/clubs/${clubId}/seasons`, { headers: authHeader() }),
      getJson(`${API_BASE}/clubs/${clubId}/players/${me.id}/ledger`, {
        headers: authHeader(),
      }),
    ]);
    // The season in play (defaultSeasonId), not the newest: a season
    // booked ahead is not due yet. Without a season, summaryRows puts
    // everything under 其他季別.
    const season = seasons.length
      ? await getJson(`${API_BASE}/seasons/${defaultSeasonId(seasons)}`, {
          headers: authHeader(),
        })
      : null;

    // Up to that season; see ledgerUpTo.
    const n = ledgerUpTo(ledger, season).total;
    total.className = "lg-total " + (n === 0 ? "even" : n > 0 ? "owed" : "owe");
    total.innerHTML =
      `<span>${n === 0 ? "目前已結清" : n > 0 ? "應退給你" : "你應繳"}</span>` +
      `<strong>$${Math.abs(n)}</strong>`;

    // The roster name, not the LINE name: summaryRows matches games by
    // player_name.
    rows.innerHTML = summaryRows(ledger, season, ledger.player_name)
      .map(
        (r) => `
          <div class="lg-row">
            <span class="lg-what">${escapeHtml(r.label)}${
              r.detail ? `<span class="lg-detail">${escapeHtml(r.detail)}</span>` : ""
            }</span>
            <span class="lg-amt ${r.amount >= 0 ? "plus" : "minus"}">${
              r.amount >= 0 ? "+" : "−"
            }$${Math.abs(r.amount)}</span>
          </div>`
      )
      .join("");
  } catch (e) {
    rows.innerHTML = `<div class="empty">讀不到這一隊的明細：${escapeHtml(e.message)}</div>`;
  }
}

/** Switches to this club and opens its games. rememberId stores the choice
 * the same way the club picker does.
 */
function goToClub(clubId) {
  rememberId(CLUB_STORAGE_KEY, String(clubId));
  // ?club= tells member.html a club was just chosen, so it does not ask
  // again.
  location.href = "member.html?club=" + encodeURIComponent(clubId);
}

function closeDetail() {
  document.getElementById("detail-backdrop").hidden = true;
}

(async function init() {
  const state = document.getElementById("page-state");
  showPageLoading(state);
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  if (!identified) {
    stopLoadingEscalation();
    state.innerHTML = signInFailureHtml(identified);
    return;
  }
  me = identified;
  state.innerHTML = "";
  document.getElementById("content").hidden = false;
  // The 繳費通知's link names its club: open that breakdown, then strip the
  // parameter.
  const params = new URLSearchParams(location.search);
  const linked = params.get("club");
  if (linked) {
    openOnlyClub = false;
    params.delete("club");
    const rest = params.toString();
    history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
  }
  await renderClubs();
  if (linked && myClubs.some((c) => String(c.id) === String(linked))) openDetail(linked);
})();
