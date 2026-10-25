// localhost -> the local API, anywhere else -> production. See apiBase.
const API_BASE = apiBase();
/* This page's own LIFF app — see the note in report.html. One LIFF
 * app has one endpoint, so the rich menu needs one app per
 * destination; member.html keeps the original id. */
const LIFF_ID = "2011156233-SWicoUre";
let me = null;
let myClubs = [];

/* Every club's money on one screen.
 *
 * The member page's 我的帳務 sheet only ever showed the club you
 * happened to be looking at, and it had a worse problem than being
 * narrow: with no club remembered — a first visit, a cleared browser,
 * a new phone — it opened on 載入中 and stayed there, because it
 * paints once and does not repaint when the answer arrives. Listing
 * every club removes the state that caused it rather than patching it.
 *
 * The balances arrive with the club list itself (GET
 * /players/{id}/clubs), so this page costs one request. A club's
 * breakdown costs three more, and only for the club actually opened.
 */

/** Where "返回" goes. Arriving from 個人資料 there is history to go
 * back to; arriving from the LINE menu there is none, and
 * history.back() would leave the app entirely. */
function goBack() {
  if (document.referrer && document.referrer.includes(location.host)) {
    history.back();
    return;
  }
  location.href = "member.html";
}

/** One read, awaited. js/common/ deliberately has no such helper —
 * getJsonSWR is callback-based because it answers twice, from cache
 * and then from the network, which is right for a page's main content
 * and wrong for three chained reads behind a tap. The refusal has to
 * surface as a thrown message: a sheet that silently shows nothing is
 * the failure this page exists to remove. */
async function getJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`伺服器回應 ${res.status}`);
  return res.json();
}

const owes = (c) => {
  const n = Number(c.balance);
  return Number.isFinite(n) ? n : 0;
};

/** What you owe, added up — never a net figure.
 *
 * Netting a club that owes you against a club you owe would describe
 * money that does not exist: balances never move between clubs (see
 * docs/billing-rules.md, "Ledger"). So this sums only what is owed,
 * and says plainly that it is spread across clubs. */
function renderOwedTotal(clubs) {
  const box = document.getElementById("owed-total");
  const owing = clubs.filter((c) => owes(c) < 0);
  // Only when it is genuinely adding something up. With one club
  // owing, the headline repeated the row beneath it word for word —
  // the same figure twice, stacked, which is noise rather than a
  // summary. Not "more than one club" but "more than one club that
  // owes": three clubs where only one is unpaid has the same problem.
  if (owing.length < 2) {
    // Cleared, not merely hidden. getJsonSWR paints this page twice —
    // once from cache, once from the network — so a headline written
    // on the first pass can be wrong on the second. A hidden node
    // still holding a figure is one accidental unhide away from
    // showing money that is no longer true, and this is the money
    // screen. Found by a test reading back a stale headline.
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
  // Shared guard and wording — see balanceBadgeHtml in js/common/format.js. This
  // is the money page, so unlike 個人資料 a cleared balance gets said
  // out loud rather than left blank.
  const bal = balanceBadgeHtml(club.balance, { settled: "已結清" });
  return `
    <button type="button" class="club-row" onclick="openDetail(${club.id})">
      <i class="club-dot">${escapeHtml(firstGrapheme(club.name))}</i>
      <span class="cr-name">${escapeHtml(club.name)}</span>
      ${roleTag}${bal}
    </button>
  `;
}

// With one club there is nothing to choose between, so its breakdown
// opens on arrival rather than behind a list of one (proposal 7,
// 2026-10-17). Once only: getJsonSWR paints twice, and closing the
// sheet must leave it closed.
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

/** One club's breakdown.
 *
 * Three reads, and only for the club actually opened: the newest
 * season (the list comes back id-descending, so [0] is current), that
 * season's games, and this player's ledger. The breakdown itself is
 * summaryRows in js/common/season.js — the same function the member page uses,
 * deliberately not a second copy, because two implementations is how
 * two screens start disagreeing about what somebody owes.
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
    // The season in play — the same one every page opens on
    // (defaultSeasonId), not simply the newest: a season booked ahead
    // is newer, and its fee is not due yet. A club with no season yet
    // is normal — someone who joined before the organizer set one up
    // — and the breakdown then has no counts to draw on, which
    // summaryRows handles by putting everything under 其他季別.
    const season = seasons.length
      ? await getJson(`${API_BASE}/seasons/${defaultSeasonId(seasons)}`, {
          headers: authHeader(),
        })
      : null;

    // Up to that season, matching the list behind this sheet and the
    // 繳費通知 that links here — see ledgerUpTo.
    const n = ledgerUpTo(ledger, season).total;
    total.className = "lg-total " + (n === 0 ? "even" : n > 0 ? "owed" : "owe");
    total.innerHTML =
      `<span>${n === 0 ? "目前已結清" : n > 0 ? "應退給你" : "你應繳"}</span>` +
      `<strong>$${Math.abs(n)}</strong>`;

    // The roster name, not the LINE name: summaryRows counts games by
    // matching player_name against the season's absences and drop-ins,
    // and a club where the organizer typed a different spelling would
    // otherwise report 請假 0 次 next to a real refund.
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

/** Switch to this club and open its games.
 *
 * rememberId writes the choice the same way the club picker does
 * (js/common/), so member.html opens on this club rather than on
 * whichever one was looked at last. Writing both session and local
 * storage is deliberate and matches the picker: the per-tab copy is
 * what lets two tabs sit on different clubs. */
function goToClub(clubId) {
  rememberId(CLUB_STORAGE_KEY, String(clubId));
  // ?club= says "a club was already chosen, do not ask again". Without
  // it, member.html's picker — which appears when somebody belongs to
  // more than one club — would open on arrival and ask which club to
  // look at, immediately after they picked one here.
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
  // The 繳費通知's 帳務明細 link names its club, so the breakdown it
  // is about opens straight away. Stripped from the URL after use, the
  // way member.html treats ?club=, so a reload shows the list.
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
