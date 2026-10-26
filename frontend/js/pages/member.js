// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
const LIFF_ID = "2011156233-6CouG6VI";
let currentSeasonId = null;
let currentSeason = null;
let selectedGameId = null;
let myPlayerId = null;
let allClubs = [];
let myClubs = [];
/** True while 要看哪一隊 is on screen. Anything keyed to "the current club"
 * stays quiet until one is chosen, so a club remembered from an earlier
 * visit does not show in the top bar during the choice.
 */
let pickingClub = false;
// Whether myClubs came from the server on this load: "no clubs" is only
// worth acting on when the server said so.
let myClubsAreCurrent = false;
let clubMembers = [];
// People this viewer has brought before, for the quick picks; see
// fetchMyGuests.
let myGuests = [];
// Set once an ?invite= token has been looked at, resolved or not: the
// page answers the invite before the ordinary pickers run. See
// renderInviteScreen.
let pendingInviteToken = null;
// Set only when the token resolved; holds the page on the invite.
let pendingInviteClubId = null;
let pendingInviteClubName = null;
// False until it is known whether there is a season and who is on it;
// guards renderIntentAsk.
let seasonLoaded = false;

// A club's invite link carries ?invite=<token>; LIFF passes the query
// through. The token stands in for the club id (see api/invites.py) and
// is resolved before sign-in, which GET /invites/{token} allows. It is
// stripped from the URL at once so a reload does not return here, and the
// page shows 載入中 before any request goes out.
showPageLoading(document.getElementById("hero-wrap"));

const inviteResolved = (async () => {
  const params = new URLSearchParams(location.search);
  const token = params.get("invite");
  if (!token) return;
  // Only this parameter: ?as= lives in the query string too (see
  // devIdentityName).
  params.delete("invite");
  const rest = params.toString();
  history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
  pendingInviteToken = token;
  try {
    const res = await fetch(`${API_BASE}/invites/${encodeURIComponent(token)}`);
    if (!res.ok) return; // renderInviteScreen shows "找不到這個球隊"
    const invite = await res.json();
    pendingInviteClubId = String(invite.club_id);
    pendingInviteClubName = invite.club_name;
    rememberId(CLUB_STORAGE_KEY, pendingInviteClubId);
  } catch (e) {
    // Same as an invalid token: nothing to join.
  }
})();

const identityReady = (async function initIdentity() {
  const identityEl = document.getElementById("identity");
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  if (!identified) {
    // Nothing here is readable without a verified identity: a club is
    // visible only to its members.
    identityEl.textContent = "";
    document.getElementById("name-row").hidden = true;
    return null;
  }
  myPlayerId = identified.id;
  document.getElementById("name-row").hidden = true;
  document.getElementById("player-name").value = identified.name;
  paintSelfAvatar(document.getElementById("avatar-btn"), identified);
  identityEl.textContent = "";
  await refreshMyClubs();
  renderClubChip();
  renderManageButton();
  // Fetched alongside the season but painted with everything else in
  // render(), so the chip neither lags behind nor appears first.
  loadMyLedger();
  // renderIntentAsk runs later, from loadSeason/renderNoSeason, once it
  // is known whether they are already on the roster.
  return identified;
})();

document.getElementById("player-name").addEventListener("change", () => {
  if (currentSeasonId) loadSeason(currentSeasonId);
  else renderNoSeason(); // now that allClubs/myClubs are known, name the club
});

/* Where the LINE rich menu's buttons land. Each destination
 * (member.html, ledger.html, report.html) is its own LIFF app, so no
 * parameter chooses the page. `?pick=1` on the 場次與報名 column asks
 * which club to look at even when this device remembers one, and is
 * stripped from the URL after use so a reload does not ask again.
 */

identityReady.then((identified) => {
  if (!identified) {
    stopLoadingEscalation();
    document.getElementById("hero-wrap").innerHTML = signInFailureHtml(identified);
    return;
  }
  if (pendingInviteToken) {
    renderInviteScreen();
    return;
  }
  // Asked to join and not let in yet: that club is all there is to show.
  const active = myClubs.filter((c) => c.status !== "pending");
  const current = myClubs.find((c) => String(c.id) === String(currentClubId()));
  if ((current && current.status === "pending") || (!active.length && myClubs.length)) {
    if (!current || current.status !== "pending") {
      rememberId(CLUB_STORAGE_KEY, String(myClubs[0].id));
    }
    renderPendingScreen();
    return;
  }
  /* Choose the club before showing one, but only when there is a choice:
   * one club goes straight through, and so does ?club= (set by the money
   * page's 前往這個球隊). Stripped from the URL so a reload does not repeat it.
   */
  const clubParams = new URLSearchParams(location.search);
  const namedClub = clubParams.get("club");
  // The rich menu's 場次與報名 asks which club even when one is remembered; an
  // explicit ?club= still wins.
  const forceClubPicker = clubParams.get("pick") === "1";
  clubParams.delete("pick");
  if (namedClub) {
    clubParams.delete("club");
    const rest = clubParams.toString();
    history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
    rememberId(CLUB_STORAGE_KEY, namedClub);
  } else if (forceClubPicker) {
    // `pick` applies to this arrival only, so a refresh does not ask
    // again.
    const rest = clubParams.toString();
    history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : ""));
  }
  /* A club already chosen on this device is used rather than asked again
   * on every reload; the top bar's club button opens the same list.
   * Checked against `active`, so a club they have left is not opened.
   */
  const remembered = currentClubId();
  const remembersOneOfThese = active.some(
    (c) => String(c.id) === String(remembered)
  );
  if (
    !namedClub &&
    (forceClubPicker || !remembersOneOfThese) &&
    active.length > 1
  ) {
    renderClubPicker(active);
    return;
  }
  startPickers();
});

/** An invite to a club the viewer is not in: the whole screen until
 * answered by asking to join as a fixed member or for single games.
 */
function renderInviteScreen() {
  stopLoadingEscalation();
  document.getElementById("season-picker").hidden = true;
  const wrap = document.getElementById("hero-wrap");
  if (!pendingInviteClubId) {
    wrap.innerHTML = emptyStateHtml(
      "找不到這個球隊",
      "這個邀請連結可能已經失效，或球隊已經被刪除。請向管理員確認。",
      { label: "回到我的球隊", onclick: "forgetInvite()" }
    );
    return;
  }
  wrap.innerHTML = `
    <div class="empty-state" style="text-align:left">
      <h3>加入「${escapeHtml(pendingInviteClubName)}」？</h3>
      <p>選一個送出申請。管理員核准之後，就能看到場次、請假和帳務。</p>
      <div class="btn-row" style="margin-top:12px">
        <button type="button" class="btn btn-primary" onclick="joinFromPrompt(true, this)">固定成員</button>
        <button type="button" class="btn btn-quiet" onclick="joinFromPrompt(false, this)">臨打成員</button>
      </div>
    </div>
  `;
}

/** Asked to join, not let in yet: nothing in the club is readable until
 * then, so this is the whole screen.
 */
function renderPendingScreen() {
  stopLoadingEscalation();
  const club = myClubs.find((c) => String(c.id) === String(currentClubId()));
  document.getElementById("season-picker").hidden = true;
  document.getElementById("hero-wrap").innerHTML =
    emptyStateHtml(
      `已送出申請：${club ? club.name : ""}`,
      "管理員核准之後，這裡就會出現本季的場次。可以先關掉，之後從同一個連結再打開。",
      { label: "重新整理", onclick: "location.reload()" }
    ) +
    /* Ask them to add the Official Account now, while nothing else
     * competes for attention (profile.html keeps a permanent prompt).
     * LINE delivers private messages only to people who have added it. A
     * plain link, not liff.getFriendship(), whose behaviour cannot be
     * tested outside the LINE app.
     */
    `<div class="empty-state" style="margin-top:10px">
      <p>通知只會發給已加入官方帳號的人。</p>
      <a class="btn btn-quiet" href="${OFFICIAL_ACCOUNT_URL}">加入官方帳號</a>
    </div>`;
}

/** The invite went nowhere: drop it and start normally. */
function forgetInvite() {
  forgetId(CLUB_STORAGE_KEY);
  location.href = location.pathname;
}

/** Which club to look at, for somebody in several. The rows go up at once
 * (the club list carries name, role and balance); each club's next game
 * fills in behind a spinner.
 */
function renderClubPicker(clubs) {
  stopLoadingEscalation();
  // Set before anything paints; renderManageButton and renderClubChip
  // read it on every call.
  pickingClub = true;
  // Repainted here because they already ran before this screen was
  // chosen, and nothing else would run them again.
  renderClubChip();
  renderManageButton();
  document.getElementById("season-picker").hidden = true;
  document.getElementById("cal-label").hidden = true;
  document.getElementById("month-cal").hidden = true;
  const wrap = document.getElementById("hero-wrap");
  wrap.innerHTML =
    '<p class="section-label" style="margin-top:0">選擇球隊</p>' +
    clubs
      .map(
        (c) => `
        <button type="button" class="club-row" data-pick-club="${c.id}">
          <i class="club-dot">${escapeHtml(firstGrapheme(c.name))}</i>
          <span class="cr-name">
            <!-- The name gets its own element so it can be truncated
                 on its own. As a bare text node beside a block child
                 it shared a box with the sub-line, and text-overflow
                 has nothing to act on there. -->
            <span class="cr-title">${escapeHtml(c.name)}</span>
            <!-- The spinner inline rather than blockLoadingHtml():
                 that returns a <div>, and a div inside this span is
                 invalid nesting. It needs .spinner's display:inline-block
                 to have any size at all — see shared.css. -->
            <span class="cr-sub" id="pick-when-${c.id}"><i class="spinner sm"></i> 讀取中…</span>
          </span>
          ${c.role === "organizer" ? '<span class="role-tag">管理員</span>' : ""}
          ${balanceBadgeHtml(c.balance)}
        </button>`
      )
      .join("");

  wrap.querySelectorAll("[data-pick-club]").forEach((btn) => {
    btn.addEventListener("click", () => {
      rememberId(CLUB_STORAGE_KEY, btn.dataset.pickClub);
      // Cleared before startPickers, whose onSeasonChange repaints the
      // top bar.
      pickingClub = false;
      startPickers();
    });
  });

  clubs.forEach((c) => fillClubNextGame(c.id));
}

/** One club's next game, read after the picker is drawn: its seasons
 * (newest first) and that season's games. Says 已請假 or 已報名 only when the
 * records support it; otherwise blank.
 */
async function fillClubNextGame(clubId) {
  const el = document.getElementById(`pick-when-${clubId}`);
  if (!el) return;
  try {
    const headers = authHeader();
    const seasons = await (
      await fetch(`${API_BASE}/clubs/${clubId}/seasons`, { headers })
    ).json();
    if (!Array.isArray(seasons) || !seasons.length) {
      el.textContent = "尚未排定場次";
      return;
    }
    const season = await (
      await fetch(`${API_BASE}/seasons/${seasons[0].id}`, { headers })
    ).json();

    // Local date: toISOString() would give the UTC date.
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const next = (season.games || []).find(
      (g) => g.date >= today && g.status !== "cancelled"
    );
    if (!next) {
      el.textContent = "本季已結束";
      return;
    }

    const me = playerName();
    const away = (next.absences || []).some((a) => a.player_name === me);
    const playing =
      (next.confirmed_drop_ins || []).some((d) => d.player_name === me) ||
      (season.members || []).some((m) => m.name === me);
    const mark = away ? "・已請假" : playing ? "・已報名" : "";
    el.textContent = describeGameWhen(season, next) + mark;
  } catch (e) {
    // A club whose games fail to load keeps its row.
    el.textContent = "";
  }
}

function startPickers() {
initClubAndSeasonPickers(
  API_BASE,
  (function () {
    // initClubAndSeasonPickers needs real <select> elements; a hidden one
    // drives it while the club chip is the visible control.
    const el = document.createElement("select");
    el.hidden = true;
    document.body.appendChild(el);
    return el;
  })(),
  document.getElementById("season-picker"),
  "vf_season",
  (id) => {
    stopLoadingEscalation();
    renderClubChip();
    renderManageButton();
    // The club may have been chosen after the ledger was first loaded.
    if (String(currentClubId()) !== String(myLedgerClubId)) loadMyLedger();
    if (id) loadSeason(id);
    else renderNoSeason();
  },
  (e) => showPageError(document.getElementById("hero-wrap"), e.message),
  null,
  // refreshMyClubs already read these clubs (with the fixed-member
  // answer), so they are handed over rather than fetched again.
  { list: myClubs.filter((c) => c.status !== "pending"), fresh: myClubsAreCurrent }
);
}

/** No season to show: the player is in no club, or the club has not
 * started one. Both say what happens next.
 */
function renderNoSeason() {
  currentSeason = null;
  seasonLoaded = true;
  document.getElementById("cal-label").hidden = true;
  document.getElementById("month-cal").hidden = true;
  document.getElementById("month-cal").innerHTML = "";
  document.getElementById("balance-chip").hidden = true;
  document.getElementById("season-picker").hidden = true;

  const clubId = currentClubId();
  const club = allClubs.find((c) => String(c.id) === String(clubId));
  const mine = myClubs.find((c) => String(c.id) === String(clubId));

  let html;
  if (!club) {
    html = emptyStateHtml(
      "尚未加入任何球隊",
      "可由左上角的球隊按鈕建立球隊，或使用管理員提供的連結加入。"
    );
  } else if (mine && mine.role === "organizer") {
    html = emptyStateHtml(
      `「${club.name}」尚未開季`,
      "先建立第一季：輸入場地費、場次日期和固定成員，系統會自動算出每個人的季費。",
      { label: "建立第一季", href: "organizer-settings.html" }
    );
  } else {
    html = emptyStateHtml(
      `「${club.name}」尚未開季`,
      "管理員開季之後，這裡就會出現本季的場次、你的請假狀態和帳務。"
    );
  }
  document.getElementById("hero-wrap").innerHTML = html;
  document.getElementById("hero-wrap").onclick = null;
}

/** The caller's clubs, with their role in each. The only other club that
 * can be listed is the one an invite link names.
 */
async function refreshMyClubs() {
  if (!myPlayerId) {
    myClubs = [];
    allClubs = [];
    myClubsAreCurrent = false;
    return;
  }
  // Everything waits on this request; getJsonSWR answers from the last
  // known list at once, and the pickers are handed it.
  try {
    await getJsonSWR(
      `${API_BASE}/players/${myPlayerId}/clubs`,
      (clubs) => {
        myClubs = clubs;
        allClubs = myClubs.slice();
      },
      { headers: authHeader() }
    );
    // The server answered, so an empty list can be acted on.
    myClubsAreCurrent = true;
  } catch (e) {
    myClubs = [];
    allClubs = [];
    myClubsAreCurrent = false;
  }

  // An invite counts only for a club the viewer is not in; their own
  // club's link just opens it. An unresolved token leaves
  // pendingInviteClubId unset (找不到這個球隊).
  await inviteResolved;
  if (pendingInviteClubId && myClubs.some((c) => String(c.id) === String(pendingInviteClubId))) {
    pendingInviteClubId = null;
    // Already a member: open the club.
    pendingInviteToken = null;
  }
  // The invited club is listed too, so the club sheet can show it as
  // 邀請連結・尚未加入.
  if (pendingInviteClubId) {
    allClubs.push({ id: Number(pendingInviteClubId), name: pendingInviteClubName });
  }
}

/** The way into 球隊管理, in the top bar for organizers only. */
function renderManageButton() {
  const clubId = currentClubId();
  const mine = myClubs.find((c) => String(c.id) === String(clubId));
  // A remembered club is not a chosen one; see pickingClub.
  const organizes = !pickingClub && !!(mine && mine.role === "organizer");
  document.getElementById("context-switch").hidden = !organizes;
  // How many people are waiting to be let in.
  const waiting = organizes ? mine.pending_count || 0 : 0;
  const badge = document.getElementById("manage-badge");
  badge.hidden = waiting === 0;
  badge.textContent = String(waiting);
  document.getElementById("manage-link").href = waiting ? "organizer-members.html" : "organizer.html";
}

function renderClubChip() {
  const clubId = currentClubId();
  // As in renderManageButton: nothing while 要看哪一隊 is up.
  const club = pickingClub
    ? null
    : allClubs.find((c) => String(c.id) === String(clubId));
  document.getElementById("club-chip-name").textContent = club ? club.name : "選擇球隊";
  document.getElementById("club-dot").textContent = club ? firstGrapheme(club.name) : "-";
}


/** Why this viewer may look but not act, or null if they may act. A reason
 * rather than a boolean, because each case needs its own words.
 */
function viewingOnlyReason(membership, onRoster) {
  if (!membership) return null; // not in this club at all
  // The organizer is never waiting for the roster.
  if (membership.role === "organizer") return null;
  // On the roster: a fixed member, whatever the column says.
  if (onRoster) return null;
  // They asked to be a fixed member and are not on the roster yet.
  if (membership.wants_fixed_membership === true) {
    return "等待管理員把你加入本季名單，在那之前只能查看";
  }
  // An unanswered question does not block anything: signing up a moment
  // early is harmless, locking out a real member is not.
  return null;
}

function currentViewingOnlyReason() {
  const mine = myClubs.find((c) => String(c.id) === String(currentClubId()));
  const onRoster = !!(
    currentSeason && currentSeason.members.some((m) => m.name === playerName())
  );
  return viewingOnlyReason(mine, onRoster);
}

async function joinFromPrompt(wantsFixed, btn) {
  const done = btn ? markBusy(btn) : () => {};
  try {
    await postJson(API_BASE, `/clubs/${currentClubId()}/join`, {
      invite: pendingInviteToken,
      wants_fixed_membership: wantsFixed,
    });
  } catch (e) {
    // Already asked (a second tap or device): the request is in.
    if (!/^Already asked to join/.test(e.rawMessage || "")) {
      done();
      toast("申請沒有送出：" + e.message);
      return;
    }
  }
  pendingInviteClubId = null;
  pendingInviteToken = null;
  await refreshMyClubs();
  renderClubChip();
  renderPendingScreen();
  done();
}

function openClubSheet() {
  renderClubRows();
  document.getElementById("club-sheet-backdrop").hidden = false;
}
function closeClubSheet() {
  document.getElementById("club-sheet-backdrop").hidden = true;
}
document.getElementById("club-chip").addEventListener("click", openClubSheet);

function renderClubRows() {
  const clubId = currentClubId();
  const rows = allClubs.map((c) => {
    const mine = myClubs.find((m) => String(m.id) === String(c.id));
    const roleTag = mine && mine.role === "organizer" ? '<span class="role-tag">管理員</span>' : "";
    const manageLink = mine && mine.role === "organizer"
      ? `<button type="button" class="club-row-manage" data-manage="${c.id}">管理 →</button>`
      : "";
    return `
      <button type="button" class="club-row${String(c.id) === String(clubId) ? " on" : ""}" data-pick-club="${c.id}">
        <i class="club-dot">${escapeHtml(firstGrapheme(c.name))}</i>
        <span class="cr-name">${escapeHtml(c.name)}${
          !mine
            ? '<span class="cr-sub">邀請連結・尚未加入</span>'
            : mine.status === "pending"
              ? '<span class="cr-sub">等待管理員核准</span>'
              : ""
        }</span>
        ${roleTag}${manageLink}
      </button>
    `;
  });
  document.getElementById("club-rows").innerHTML =
    rows.join("") || '<div class="empty">尚未加入任何球隊</div>';
}

document.getElementById("club-rows").addEventListener("click", (e) => {
  const manage = e.target.closest("[data-manage]");
  if (manage) {
    rememberId(CLUB_STORAGE_KEY, manage.dataset.manage);
    location.href = "organizer.html";
    return;
  }
  const row = e.target.closest("[data-pick-club]");
  if (row) {
    const id = row.dataset.pickClub;
    rememberId(CLUB_STORAGE_KEY, id);
    closeClubSheet();
    // A club that is not theirs yet can only be this load's invite, so
    // return through ?invite= with its token.
    const mine = myClubs.some((c) => String(c.id) === String(id));
    location.href =
      mine || !pendingInviteToken
        ? location.pathname
        : `${location.pathname}?invite=${encodeURIComponent(pendingInviteToken)}`;
  }
});

function promptJoinClub() {
  toast(
    "請向管理員索取「連結加入」的網址，用 LINE 打開即可自動加入該球隊。"
  );
}

async function createClub() {
  const name = prompt("請輸入球隊名稱");
  if (!name || !name.trim()) return;
  if (name.trim().length > CLUB_NAME_MAX) {
    toast(`球隊名稱最多 ${CLUB_NAME_MAX} 個字`);
    return;
  }
  try {
    const club = await postJson(API_BASE, "/clubs", { name: name.trim() });
    rememberId(CLUB_STORAGE_KEY, String(club.id));
    location.reload();
  } catch (e) {
    toast("建立失敗：" + e.message);
  }
}

document.getElementById("avatar-btn").addEventListener("click", () => {
  location.href = "profile.html";
});

[
  ["club-sheet-backdrop", closeClubSheet],
  ["game-sheet-backdrop", closeGameSheet],
].forEach(([id, close]) => {
  const backdrop = document.getElementById(id);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) close();
  });
});
document.querySelectorAll("[data-close-sheet]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.closeSheet === "club") closeClubSheet();
    else if (btn.dataset.closeSheet === "signup") closeSignup();
    else if (btn.dataset.closeSheet === "ledger") closeLedger();
    else closeGameSheet();
  });
});

const seasonLoad = staleGuard();

async function loadSeason(seasonId) {
  // The picker calls this twice (cache, then network); see staleGuard.
  const ticket = seasonLoad.take();
  // What the screen knew when this read left; a newer local change makes
  // the answer stale. See localRevision.
  const askedAt = localRevision();
  currentSeasonId = seasonId;
  const errorEl = document.getElementById("error");
  errorEl.textContent = "";
  try {
    // Paints from the last copy, then corrects it; render() is safe to
    // run twice.
    await getJsonSWR(`${API_BASE}/seasons/${seasonId}`, (season) => render(season), {
      headers: authHeader(),
    });
  } catch (e) {
    if (seasonLoad.current(ticket)) errorEl.textContent = "連不上伺服器：" + e.message;
  }

  function render(season) {
    if (!seasonLoad.current(ticket)) return;
    // A local change happened after this read left, so it is stale; that
    // change queued its own refresh. Only for the season on screen.
    if (currentSeason && currentSeason.id === season.id && localRevision() !== askedAt) {
      return;
    }
    // Skip identical data; redrawing costs a visible frame.
    if (currentSeason && JSON.stringify(season) === JSON.stringify(currentSeason)) {
      seasonLoaded = true;
      return;
    }
    const isNewSeason = !currentSeason || currentSeason.id !== season.id;
    currentSeason = season;
    seasonLoaded = true;
    if (isNewSeason || !season.games.some((g) => g.id === selectedGameId)) {
      const upcoming = season.games.find((g) => !describeDate(g.date).isPast) || season.games[season.games.length - 1];
      selectedGameId = upcoming ? upcoming.id : null;
    }
    document.getElementById("season-picker").hidden = false;
    document.getElementById("cal-label").hidden = false;
    document.getElementById("month-cal").hidden = false;
    renderHero(season);
    renderCalendar();
    renderSelectedGameDetail();
    paintBalance();
    // Runs alongside the paint and repaints only if it found something
    // new.
    refreshMyGuestsQuietly();
    fetchClubMembers(API_BASE, currentClubId()).then((m) => {
      if (JSON.stringify(m) === JSON.stringify(clubMembers)) return;
      clubMembers = m;
      renderSelectedGameDetail();
    });
  }
}

/** Re-reads the "people you've brought before" quick picks in the
 * background, repainting only on a real difference.
 */
function refreshMyGuestsQuietly() {
  if (!currentClubId()) return;
  fetchMyGuests(API_BASE, currentClubId())
    .then((g) => {
      if (JSON.stringify(g) === JSON.stringify(myGuests)) return;
      myGuests = g;
      renderSelectedGameDetail();
    })
    .catch(() => {});
}

function playerName() {
  return document.getElementById("player-name").value.trim();
}

function selectGame(gameId) {
  selectedGameId = gameId;
  renderSelectedGameDetail();
  // Tapping a date shows that game in the card above the calendar, with
  // its buttons; the full roster is behind 名單.
  renderHero(currentSeason);
  renderCalendar();
  // Mark the switch: two dates with the same headcount look alike.
  const card = document.querySelector("#hero-wrap .hero");
  if (card) {
    card.classList.remove("card-swap");
    void card.offsetWidth; // restart the animation rather than no-op
    card.classList.add("card-swap");
  }
}

function renderHero(season) {
  const wrap = document.getElementById("hero-wrap");
  const game =
    season.games.find((g) => g.id === selectedGameId) ||
    season.games.find((g) => !describeDate(g.date).isPast) ||
    season.games[season.games.length - 1];
  if (!game) {
    wrap.innerHTML = '<div class="empty">本季尚無場次</div>';
    return;
  }
  selectedGameId = game.id;
  const deadlinePill = season.change_deadline_hours != null
    ? `<span class="meta-pill">更動截止 <strong>開賽前 ${season.change_deadline_hours} 小時</strong></span>`
    : "";
  const mine = myActions(season, game);
  // Everything about this date, including the way into the roster, is in
  // this one card.
  wrap.innerHTML = renderGameHero(season, game, {
    metaPills: [acPill(season, game), deadlinePill],
    statusHtml: mine.status,
    actionsHtml: mine.actions,
    moreHtml: `<button type="button" class="hero-more" onclick="openGameSheet()">
        <span>名單（${expectedAttendance(season, game)} 人）</span><span class="chev">›</span>
      </button>`,
  });
  wrap.dataset.heroGameId = game.id;
  wrap.onclick = null;
}

/** Which colour that day's dot gets: the viewer's own absence first, then
 * whether the game can go ahead.
 */
function calendarState(game) {
  const name = playerName();
  if (game.absences.some((a) => a.player_name === name)) return "away";
  const playing = expectedAttendance(currentSeason, game);
  if (playing >= currentSeason.capacity) return "full";
  if (playing < currentSeason.minimum_roster) return "short";
  return "";
}

function renderCalendar() {
  if (!currentSeason) return;
  renderMonthCalendar(
    document.getElementById("month-cal"),
    currentSeason.games,
    selectGame,
    { stateOf: calendarState, selectedId: selectedGameId, viewKey: currentSeason.id }
  );
}

function renderSelectedGameDetail() {
  const el = document.getElementById("game-detail");
  const game = currentSeason && currentSeason.games.find((g) => g.id === selectedGameId);
  if (!currentSeason || !game) {
    el.innerHTML = '<div class="empty">本季尚無場次</div>';
    return;
  }
  const deadlinePill = currentSeason.change_deadline_hours != null
    ? `<span class="meta-pill">更動截止 <strong>開賽前 ${currentSeason.change_deadline_hours} 小時</strong></span>`
    : "";
  renderGameDetail(el, currentSeason, game, {
    asMember: true,
    heroMetaPills: [acPill(currentSeason, game), deadlinePill],
    clubMembers,
    myGuests,
    viewerName: playerName(),
    statusHtml: myActions(currentSeason, game).status,
    actionsHtml: myActions(currentSeason, game).actions,
    onAssignSubstitute: (absenceId, name, gender, target) =>
      assignSubstitute(absenceId, name, gender, target),
    // The viewer's own row only: this screen is for their own attendance.
    onRecordAbsence: (name, btn) => recordAbsence(selectedGameId, btn),
    canRecordAbsence: (m) => m.name === playerName(),
    onRecordAbsenceWithSubstitute: (name, subName, gender, target) =>
      recordAbsenceWithSubstitute(selectedGameId, subName, gender, target),
    onCancelAbsence: (absenceId, btn) => cancelAbsence(absenceId, btn),
    onLeaveWaitlist: (entryId, btn) => leaveWaitlist(entryId, btn),
    onCancelSubstitute: (dropInId, btn) => cancelDropIn(dropInId, btn),
    onRemoveDropIn: (dropInId, btn) => cancelDropIn(dropInId, btn),
    canAssignSubstitute: (absence) => absence.player_name === playerName(),
    // Only their own signups and guests they brought, whatever their
    // role; the server enforces the same (_require_self_or_organizer).
    canRemoveDropIn: (d) => d.signed_up_by_me === true,
    canLeaveWaitlist: (w) => w.signed_up_by_me === true,
  });
}

/** Opens the roster sheet with this absence's substitute form already
 * open.
 */
function openSubstitutePicker(absenceId) {
  openGameSheet();
  const detail = document.getElementById("game-detail");
  // The form is in the absences tab, so show that first.
  showGameDetailTab(detail, "absent");
  const form = detail.querySelector(`[data-sub-form="${absenceId}"]`);
  if (form) {
    form.hidden = false;
    form.scrollIntoView({ block: "center" });
  }
}

function openGameSheet() { document.getElementById("game-sheet-backdrop").hidden = false; }
function closeGameSheet() { document.getElementById("game-sheet-backdrop").hidden = true; }

/** Ends the roster move started on the tap: repaints the completion first,
 * so the caller's success message lands in the same frame. A stale
 * response cannot complete a newer tap.
 */
function settleRosterChange(token, succeeded) {
  const detail = document.getElementById("game-detail");
  const changed = succeeded
    ? completeGameDetailChange(detail, token)
    : clearGameDetailChange(detail, token);
  if (changed) repaintGame();
}

async function assignSubstitute(absenceId, name, gender, target) {
  // The server decides who is displaced, so the button shows the wait;
  // the response is enough to move the rows without another read.
  return whileBusy(target, async () => {
  let changeToken = null;
  try {
    // Noted before the call: the response has only an id.
    const game = currentSeason.games.find((g) => g.id === selectedGameId) || {};
    const playingBefore = game.confirmed_drop_ins || [];
    const absence = (game.absences || []).find((a) => a.id === absenceId);
    changeToken = markGameDetailChange(
      document.getElementById("game-detail"),
      "absent",
      absence ? [absence.player_name] : []
    );
    repaintGame();
    const result = await postJson(API_BASE, `/absences/${absenceId}/substitute`, { player_name: name, gender }, "PUT");
    const bumped = playingBefore.find((d) => d.player_id === result.displaced_player_id);
    applySubstituteLocally(currentSeason, selectedGameId, absenceId, result, { name, gender });
    settleRosterChange(changeToken, true);
    // Name whoever went back to the queue.
    toast(
      bumped ? `已指定 ${name} 代打，${bumped.player_name} 回到候補` : `已指定 ${name} 代打`,
      "good"
    );
    refreshSeasonQuietly();
  } catch (e) {
    settleRosterChange(changeToken, false);
    toast("指定代打失敗：" + e.message);
  }
  });
}

/** The card's 指定代打 while still playing: takes leave and names the
 * substitute in one step, from the game sheet.
 */
function pickSubstituteAndLeave(gameId) {
  if (selectedGameId !== gameId) selectGame(gameId);
  openGameSheet();
  openAbsenceWithSubstitute(document.getElementById("game-detail"), playerName());
}

/** 請假並指定代打 in one request (POST /absences with `substitute`). Waits for
 * the server, which decides whether the name is accepted.
 */
async function recordAbsenceWithSubstitute(gameId, subName, gender, target) {
  const name = playerName();
  return whileBusy(target, async () => {
  let changeToken = null;
  try {
    changeToken = markGameDetailChange(
      document.getElementById("game-detail"),
      "absent",
      [name]
    );
    repaintGame();
    const result = await postJson(API_BASE, "/absences", {
      player_name: name,
      game_id: gameId,
      substitute: { player_name: subName, gender },
    });
    applyAbsenceWithSubstituteLocally(currentSeason, gameId, name, result, {
      name: subName,
      gender,
    });
    settleRosterChange(changeToken, true);
    toast(`已完成請假，由 ${subName} 代打`, "good");
    refreshSeasonQuietly();
  } catch (e) {
    settleRosterChange(changeToken, false);
    toast("請假並指定代打失敗：" + e.message);
  }
  });
}

async function cancelAbsence(absenceId, btn) {
  const name = playerName();
  const gameId = selectedGameId;
  const before = currentSeason.games.find((g) => g.id === gameId);
  const beforeAbsence = before && before.absences.find((a) => a.id === absenceId);
  const releasedName = beforeAbsence && (beforeAbsence.covered_by || beforeAbsence.filled_by);
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      for (const game of season.games) {
        const released = releasedName
          ? game.confirmed_drop_ins.find((d) => d.player_name === releasedName)
          : null;
        if (released) {
          game.confirmed_drop_ins = game.confirmed_drop_ins.filter((d) => d !== released);
          game.waitlist_entries.unshift({
            id: -1,
            player_id: released.player_id,
            player_name: released.player_name,
            gender: released.gender || null,
            signed_up_by_me: released.signed_up_by_me === true,
          });
        }
        game.absences = game.absences.filter(
          (a) => a.id !== absenceId && !(a.id === -1 && a.player_name === name)
        );
      }
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "attending",
        [name]
      );
    },
    async () => {
      // Resolved here rather than at tap time: if 請假 is still in flight,
      // wait for its id.
      let id = absenceId;
      if (!(id > 0)) id = await pendingAbsenceId[absenceKey(gameId, name)];
      if (!(id > 0)) throw new Error("這筆請假尚未建立成功");
      delete pendingAbsenceId[absenceKey(gameId, name)];
      return postJson(API_BASE, `/absences/${id}/cancel`, {});
    },
    "取消請假失敗："
  );
  settleRosterChange(changeToken, !!result);
  if (result) {
    toast(
      result.released_player_id && releasedName
        ? `已取消請假；${releasedName} 已回到候補`
        : "已取消請假",
      "good"
    );
  }
  // Only when somebody went back to the queue; see needsRefreshAfter.
  if (
    needsRefreshAfter(result) ||
    Boolean(releasedName) !== Boolean(result && result.released_player_id)
  ) refreshSeasonQuietly();
}

/** Re-reads the season in the background to catch what the optimistic
 * update could not know. Queued behind pending writes and collapsed to
 * one; see coalescedRefresh.
 */
const refreshSeasonQuietly = coalescedRefresh(() => loadSeason(currentSeasonId));

/** What this viewer can do about this game: a status line above the rule
 * and a row of equal buttons below.
 */
function myActions(season, game) {
  const name = playerName();
  const blocked = currentViewingOnlyReason();
  if (blocked) {
    return { status: `<div class="hero-status">${escapeHtml(blocked)}</div>`, actions: "" };
  }
  // Before the deadline check: a settled season is closed to everybody,
  // the organizer included, so no button is offered.
  if (season.settled_at) {
    return {
      status: '<div class="hero-status">本季已結算，名單已確定</div>',
      actions: "",
    };
  }
  // past_deadline, not just locked: this is the organizer's own
  // attendance screen too.
  if (game.locked || game.past_deadline) {
    return {
      status: '<div class="hero-status">已過更動期限，本場無法再變更</div>',
      actions: "",
    };
  }
  if (!name) return { status: "", actions: "" };

  const isMember = season.members.some((m) => m.name === name);
  const myAbsence = game.absences.find((a) => a.player_name === name);
  const myDropIn = game.confirmed_drop_ins.find((d) => d.player_name === name);
  const myWaitIndex = game.waitlist_entries.findIndex((d) => d.player_name === name);
  const playing = (isMember && !myAbsence) || !!myDropIn;
  const full = isGameFull(season, game);

  // One label either way: the sheet is where you say who the signup is
  // for.
  const bring = `<button type="button" class="hact${playing ? " quiet" : ""}" onclick="openSignup(${game.id})">${
    full ? "＋ 報名候補" : "＋ 報名"
  }</button>`;

  if (isMember && myAbsence) {
    if (myAbsence.covered_by) {
      // With a 代打 named, the card offers taking them off; cancelling the
      // leave itself is in the game sheet.
      const covering = game.confirmed_drop_ins.find((d) => d.covering === name);
      return {
        status: `<div class="hero-status absent">已請假・由 ${escapeHtml(myAbsence.covered_by)} 代打</div>`,
        actions:
          (covering
            ? `<button type="button" class="hact danger" onclick="cancelDropIn(${covering.id}, this)">取消代打</button>`
            : "") + bring,
      };
    }
    // Naming who covers is offered right on the card. A 代打 takes your
    // slot; a 報名 queues for any free one.
    return {
      status: '<div class="hero-status absent">本場已請假</div>',
      actions:
        `<button type="button" class="hact" onclick="openSubstitutePicker(${myAbsence.id})">指定代打</button>` +
        `<button type="button" class="hact danger" onclick="cancelAbsence(${myAbsence.id}, this)">取消請假</button>` +
        bring,
    };
  }
  if (isMember) {
    return {
      status: "",
      actions:
        `<button type="button" class="hact danger" onclick="recordAbsence(${game.id}, this)">請假</button>` +
        // Naming a substitute is taking leave, so it sits beside 請假.
        `<button type="button" class="hact" onclick="pickSubstituteAndLeave(${game.id})">指定代打</button>` +
        bring,
    };
  }
  if (myDropIn) {
    return {
      status: '<div class="hero-status confirmed">本場已報名</div>',
      actions:
        `<button type="button" class="hact danger" onclick="cancelDropIn(${myDropIn.id}, this)">取消報名</button>` +
        bring,
    };
  }
  if (myWaitIndex >= 0) {
    return {
      status: `<div class="hero-status waiting">候補中・第 ${myWaitIndex + 1} 順位</div>`,
      actions:
        `<button type="button" class="hact danger" onclick="leaveWaitlist(${game.waitlist_entries[myWaitIndex].id}, this)">取消候補</button>` +
        bring,
    };
  }
  return { status: "", actions: bring };
}




/* The signup sheet: its own sheet, because anything below an 18-name
 * roster is hidden. How full the game is, who is coming, and what it
 * costs, on one screen.
 */
let suGame = null;
let suPeople = [];

function openSignup(gameId) {
  suGame = currentSeason.games.find((g) => g.id === gameId);
  if (!suGame) return;
  // The first row is the viewer, pre-filled.
  const alreadyIn =
    suGame.confirmed_drop_ins.some((d) => d.player_name === playerName()) ||
    suGame.waitlist_entries.some((w) => w.player_name === playerName()) ||
    currentSeason.members.some((m) => m.name === playerName());
  // Already playing: start empty and add whoever they bring.
  suPeople = alreadyIn
    ? []
    : [{ name: playerName(), gender: null, playerId: myPlayerId, self: true }];
  document.getElementById("su-when").textContent = describeGameWhen(currentSeason, suGame);
  renderSignup();
  document.getElementById("signup-backdrop").hidden = false;
}

function describeGameWhen(season, game) {
  const d = describeDate(game.date);
  // This night's own time and venue when it has them; see gameStartTime.
  const startsAt = gameStartTime(season, game);
  const where = gameLocation(season, game);
  const time = startsAt ? " " + startsAt.slice(0, 5) : "";
  return `${d.month}/${d.day}（${d.weekday.slice(-1)}）${time}${where ? " · " + where : ""}`;
}

function closeSignup() { document.getElementById("signup-backdrop").hidden = true; }

/** Who is already on this game, including members not on leave; nobody may
 * be added twice.
 */
function alreadyOnThisGame() {
  return new Set(
    currentSeason.members
      .filter((m) => !suGame.absences.some((a) => a.player_name === m.name))
      .map((m) => m.name)
      .concat(suGame.confirmed_drop_ins.map((d) => d.player_name))
      .concat(suGame.waitlist_entries.map((w) => w.player_name))
      .concat(suPeople.map((p) => p.name))
  );
}

/** Opens the shared person picker to add somebody. */
async function addSignupPerson() {
  const taken = alreadyOnThisGame();
  const picked = await choosePerson(document.getElementById("su-picker"));
  if (!picked) return;
  if (taken.has(picked.name)) {
    toast(`${picked.name} 已在本場名單上`);
    return;
  }
  suPeople.push({ name: picked.name, gender: picked.gender, playerId: picked.id });
  renderSignup();
}

function renderSignup() {
  const playing = expectedAttendance(currentSeason, suGame);
  const slots = Math.max(0, currentSeason.capacity - playing);
  const confirmed = Math.min(suPeople.length, slots);
  const waiting = suPeople.length - confirmed;
  const share = Number(suGame.share ?? currentSeason.share_per_game);

  // Rebuilt each time, so it excludes whoever was just added.
  const taken = alreadyOnThisGame();
  renderPersonPicker(document.getElementById("su-picker"), {
    title: "新增報名對象",
    hint: "選過去報名過的人，可沿用同一筆紀錄與帳務。",
    candidates: myGuests
      .filter((g) => !taken.has(g.name))
      .map((g) => ({ id: g.id, name: g.name, gender: g.gender, note: `報名過 ${g.times} 次` })),
  });

  document.getElementById("su-now").textContent = playing + confirmed;
  document.getElementById("su-cap").textContent = currentSeason.capacity;
  document.getElementById("su-note").textContent =
    waiting > 0 ? `${waiting} 位排候補` : `${slots - confirmed} 位空位`;
  const pct = (n) => (n / currentSeason.capacity) * 100 + "%";
  document.getElementById("su-bar-now").style.width = pct(playing);
  document.getElementById("su-bar-add").style.width = pct(confirmed);
  document.getElementById("su-bar-over").style.width = pct(waiting);

  document.getElementById("su-who").innerHTML = suPeople.length
    ? suPeople
        .map((p, i) => {
          const wait = playing + i >= currentSeason.capacity;
          return `
            <div class="su-row${wait ? " wait" : ""}">
              <span class="n">${i + 1}</span>
              <span class="su-name">${escapeHtml(p.name)}${p.self ? "（你）" : ""}</span>
              ${genderTag(p.gender)}
              ${wait ? '<span class="su-wait-tag">候補</span>' : ""}
              ${p.self ? "" : `<button type="button" class="su-drop" data-drop="${i}" aria-label="移除">✕</button>`}
            </div>`;
        })
        .join("")
    : '<div class="empty" style="padding:14px 0">尚未加入任何人</div>';

  const amount = document.getElementById("su-amt");
  const label = document.getElementById("su-total-label");
  const breakdown = document.getElementById("su-breakdown");
  // The same layout in every state: 合計, a figure, and a breakdown. The
  // queue is shown on the capacity line and on each queued row, so this
  // slot is always money.
  label.textContent = "合計";
  if (suPeople.length === 0) {
    // Nobody chosen yet: "$0" would read as free.
    amount.textContent = "—";
    breakdown.textContent = "先新增報名對象";
  } else if (confirmed === 0 && waiting > 0) {
    // Everybody is queued, so nothing is owed now.
    amount.textContent = "—";
    breakdown.textContent = `候補 ${waiting} 位・遞補上才收，每位 $${share}`;
  } else {
    amount.textContent = "$" + confirmed * share;
    breakdown.textContent = waiting
      ? `${confirmed} 位 × $${share}；候補 ${waiting} 位遞補上才收`
      : `${confirmed} 位 × $${share}`;
  }
  const go = document.getElementById("su-go");
  go.textContent = confirmed === 0 ? "加入候補" : "確認報名";
  go.disabled = suPeople.length === 0;
}

document.getElementById("su-add").addEventListener("click", addSignupPerson);

document.getElementById("su-who").addEventListener("click", (e) => {
  const drop = e.target.closest("[data-drop]");
  if (!drop) return;
  suPeople.splice(Number(drop.dataset.drop), 1);
  renderSignup();
});

/** Signs up, drawing the result before the network answers. The sheet
 * already showed who gets a slot and who queues, by the same arithmetic
 * and order the server uses. If the server agrees, the rows just gain
 * their real ids; if not, somebody else moved meanwhile and the season is
 * re-read.
 */
document.getElementById("su-go").addEventListener("click", async () => {
  if (!suPeople.length) return toast("請先新增報名對象");
  for (const p of suPeople) {
    if (!p.playerId && !p.gender) return toast(`請幫「${p.name}」選性別`);
  }
  const gameId = suGame.id;
  const people = suPeople.map((p) => ({
    player_name: p.name.trim(),
    gender: p.gender,
    player_id: p.playerId,
  }));
  // Closed first: the tap is finished.
  closeSignup();

  let guessHeld = true;
  let confirmedNames = [];
  let queuedNames = [];
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      const game = season.games.find((g) => g.id === gameId);
      if (!game) return;
      let free = Math.max(0, season.capacity - expectedAttendance(season, game));
      confirmedNames = [];
      queuedNames = [];
      for (const p of people) {
        if (free > 0) {
          game.confirmed_drop_ins.push({
            id: -1,
            player_id: p.player_id || -1,
            player_name: p.player_name,
            gender: p.gender || null,
            covering: null,
            // Same rule as the server: null when signing yourself up.
            brought_by_name: p.player_name === playerName() ? null : playerName(),
            signed_up_by_me: true,
          });
          confirmedNames.push(p.player_name);
          free -= 1;
        } else {
          game.waitlist_entries.push({
            id: -1,
            player_name: p.player_name,
            gender: p.gender || null,
            brought_by_name: p.player_name === playerName() ? null : playerName(),
            signed_up_by_me: true,
          });
          queuedNames.push(p.player_name);
        }
      }
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        queuedNames.length ? "queued" : "attending",
        queuedNames.length ? queuedNames : confirmedNames
      );
    },
    () => postJson(API_BASE, `/games/${gameId}/drop-ins`, { people }),
    "報名沒有送出：",
    (batch, season) => {
      const game = season.games.find((g) => g.id === gameId);
      if (!game) return;
      const results = (batch && batch.results) || [];
      const placedHere = game.confirmed_drop_ins.filter((d) => d.id === -1);
      const queuedHere = game.waitlist_entries.filter((w) => w.id === -1);
      const confirmed = results.filter((r) => r.status === "confirmed");
      const queued = results.filter((r) => r.status === "waitlisted");
      // A displacement moves somebody this screen cannot name, so
      // re-read.
      guessHeld =
        placedHere.length === confirmed.length &&
        queuedHere.length === queued.length &&
        !results.some((r) => r.displaced_player_id);
      if (!guessHeld) return;
      placedHere.forEach((row, i) => {
        row.id = confirmed[i].id;
        row.player_id = confirmed[i].player_id;
      });
      queuedHere.forEach((row, i) => {
        row.id = queued[i].id;
      });
    }
  );
  settleRosterChange(changeToken, !!result);
  if (result) {
    const confirmedCount = result.results.filter((item) => item.status === "confirmed").length;
    const queuedCount = result.results.filter((item) => item.status === "waitlisted").length;
    const message =
      confirmedCount && queuedCount
        ? `報名完成：${confirmedCount} 位出席，${queuedCount} 位候補`
        : queuedCount
          ? queuedCount === 1
            ? "已加入候補名單"
            : `${queuedCount} 位已加入候補名單`
          : confirmedCount === 1
            ? "報名成功，已加入出席名單"
            : `報名成功，${confirmedCount} 位已加入出席名單`;
    toast(message, "good");
  }
  // A signup can fill somebody's absence by FIFO matching, which only the
  // server decides, so re-read when this game has an unfilled absence.
  // Other signups keep the fast path.
  const signedGame = currentSeason && currentSeason.games.find((g) => g.id === gameId);
  const mayHaveSettledAnAbsence =
    !!signedGame && (signedGame.absences || []).some((a) => !a.filled_by);
  if (!result || !guessHeld || mayHaveSettledAnAbsence) refreshSeasonQuietly();
  else refreshMyGuestsQuietly();
});

/** The viewer's balance, in the row that names the season. Keyed by their
 * own player id, so drop-ins see theirs too. The figure spans the club's
 * seasons, so it is not labelled 本季. Read through getJsonSWR to paint at
 * once.
 */
let myLedger = null;
let ledgerReady = null;
/** The club `myLedger` belongs to. A ledger is per club, so it is
 * reloaded whenever the club on screen is a different one. */
let myLedgerClubId = null;

/** Fetches the ledger without painting, so the chip goes up with
 * everything else.
 */
function loadMyLedger() {
  const clubId = currentClubId();
  if (!myPlayerId || !clubId) return Promise.resolve();
  if (String(clubId) !== String(myLedgerClubId)) {
    myLedger = null;
    myLedgerClubId = clubId;
  }
  ledgerReady = getJsonSWR(
    `${API_BASE}/clubs/${clubId}/players/${myPlayerId}/ledger`,
    (ledger) => {
      // An answer for a club no longer on screen is dropped.
      if (String(clubId) !== String(currentClubId())) return;
      myLedger = ledger;
      // Repaint only after the first render.
      if (seasonLoaded) paintBalance();
    },
    { headers: authHeader() }
  ).catch(() => {});
  return ledgerReady;
}

function paintBalance() {
  const chip = document.getElementById("balance-chip");
  if (!myLedger || String(myLedgerClubId) !== String(currentClubId())) {
    chip.hidden = true;
    return;
  }
  // Up to the season on screen — never a season booked for later.
  const n = ledgerUpTo(myLedger, currentSeason).total;
  chip.innerHTML = `
    <span class="bc-label">我的帳務</span>
    <span class="bc-value">${
      n === 0 ? "已結清" : n > 0 ? `餘額 +$${n}` : `應繳 $${Math.abs(n)}`
    }<span class="bc-chev">›</span></span>`;
  chip.className = "balance-chip " + (n === 0 ? "even" : n > 0 ? "owed" : "owe");
  chip.hidden = false;
}

/** Opens the money sheet: the summary from summaryRows. */
function openLedger() {
  document.getElementById("ledger-backdrop").hidden = false;
  const wrap = document.getElementById("lg-entries");
  const total = document.getElementById("lg-total");
  const club = allClubs.find((c) => String(c.id) === String(currentClubId()));
  document.getElementById("lg-sub").textContent = club ? club.name : "";

  if (!myLedger) {
    total.className = "lg-total";
    total.textContent = "";
    wrap.innerHTML = '<div class="empty">載入中…</div>';
    return;
  }

  // Up to the season on screen — never a season booked for later.
  const n = ledgerUpTo(myLedger, currentSeason).total;
  total.className = "lg-total " + (n === 0 ? "even" : n > 0 ? "owed" : "owe");
  total.innerHTML =
    `<span>${n === 0 ? "目前已結清" : n > 0 ? "應退給你" : "你應繳"}</span>` +
    `<strong>$${Math.abs(n)}</strong>`;

  wrap.innerHTML = summaryRows(myLedger, currentSeason, playerName())
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

  renderRefundEstimate();
}

/** What this season would refund if it settled now: the shares of games
 * where this member is away and somebody fills the slot (the server's
 * `filled_by`). The FIFO pairing is not re-run here; the server decides
 * it.
 *
 * Read fresh on every open, since a substitute named a moment ago changes
 * it. Never added to the total, which is the ledger balance.
 */
function renderRefundEstimate() {
  const box = document.getElementById("lg-estimate");
  const me = playerName();
  if (!currentSeason || !me) {
    box.hidden = true;
    return;
  }
  // A cancelled-and-refunded game refunds nothing.
  const covered = (currentSeason.games || []).filter(
    (g) =>
      g.status !== "cancelled_refunded" &&
      (g.absences || []).some((a) => a.player_name === me && a.filled_by)
  );
  const amount = covered.reduce((sum, g) => sum + Number(g.share || 0), 0);
  box.hidden = false;
  box.innerHTML =
    '<span class="est-what">預估退費' +
    `<span class="est-note">${
      amount > 0
        ? `${covered.length} 場請假有人遞補，結算後才入帳。補的人取消就會取消退費。`
        : "請假且有人遞補才退費，結算後入帳。"
    }</span></span>` +
    `<span class="est-amt${amount > 0 ? "" : " none"}">${
      amount > 0 ? `+$${amount}` : "$0"
    }</span>`;
}


function closeLedger() { document.getElementById("ledger-backdrop").hidden = true; }
/** Repaints everything showing the selected game, the calendar's dot
 * included.
 */
function repaintGame() {
  renderHero(currentSeason);
  renderCalendar();
  renderSelectedGameDetail();
}

// Changes apply on screen at once and are then sent (see
// optimisticRunner); on failure the screen goes back and says why. The
// refresh afterwards is not awaited.
const optimistically = optimisticRunner({
  getState: () => currentSeason,
  setState: (s) => {
    currentSeason = s;
  },
  repaint: () => repaintGame(),
});

/** The real absence id once the server issues it, keyed by game and
 * player, so 取消請假 tapped before 請假 returns waits for it.
 */
const pendingAbsenceId = {};

function absenceKey(gameId, name) {
  return `${gameId}:${name}`;
}

async function recordAbsence(gameId, btn) {
  const name = playerName();
  const key = absenceKey(gameId, name);
  let promotedName = null;
  let changeToken = null;
  let announceId;
  pendingAbsenceId[key] = new Promise((resolve) => {
    announceId = resolve;
  });
  const result = await optimistically(
    (season) => {
      const game = season.games.find((g) => g.id === gameId);
      const promoted = game.waitlist_entries.shift() || null;
      promotedName = promoted ? promoted.player_name : null;
      if (promoted) {
        game.confirmed_drop_ins.push({
          id: -1,
          player_id: promoted.player_id || -1,
          player_name: promoted.player_name,
          gender: promoted.gender || null,
          covering: null,
          signed_up_by_me: promoted.signed_up_by_me === true,
        });
      }
      // A placeholder id until the server answers.
      game.absences.push({
        id: -1,
        player_name: name,
        covered_by: null,
        filled_by: promotedName,
      });
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "absent",
        [name]
      );
    },
    async () => {
      const created = await postJson(API_BASE, "/absences", {
        player_name: name,
        game_id: gameId,
      });
      announceId(created.id);
      return created;
    },
    "請假失敗：",
    (created, season) => {
      // A promotion changes people this page cannot draw; the refresh
      // will.
      if (created.promoted_from_waitlist) return;
      for (const game of season.games) {
        for (const a of game.absences) if (a.id === -1) a.id = created.id;
      }
    }
  );
  if (!result) {
    announceId(null); // it failed; unblock anything waiting on the id
    delete pendingAbsenceId[key];
  }
  settleRosterChange(changeToken, !!result);
  if (result) {
    toast(
      result.promoted_from_waitlist && promotedName
        ? `已完成請假；${promotedName} 已由候補遞補`
        : "已完成請假",
      "good"
    );
  }
  // Only when somebody was promoted; see needsRefreshAfter.
  if (
    needsRefreshAfter(result) ||
    Boolean(promotedName) !== Boolean(result && result.promoted_from_waitlist)
  ) refreshSeasonQuietly();
}


/** Leaves the queue, through /waitlist/{id}/cancel. */
async function leaveWaitlist(entryId, btn) {
  if (entryId < 0) return;
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      for (const game of season.games) {
        game.waitlist_entries = game.waitlist_entries.filter((w) => w.id !== entryId);
      }
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "queued",
        []
      );
    },
    () => postJson(API_BASE, `/waitlist/${entryId}/cancel`, {}),
    "取消候補失敗："
  );
  settleRosterChange(changeToken, !!result);
  if (result) toast("已取消候補", "good");
}

async function cancelDropIn(dropInId, btn) {
  if (dropInId < 0) return;  // still saving; the real id isn't known yet
  const game = currentSeason.games.find((item) =>
    item.confirmed_drop_ins.some((dropIn) => dropIn.id === dropInId)
  );
  const leaving = game && game.confirmed_drop_ins.find((dropIn) => dropIn.id === dropInId);
  let promotedName = null;
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      removeDropInLocally(season, dropInId);
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "attending",
        []
      );
    },
    () => postJson(API_BASE, `/drop-ins/${dropInId}/cancel`, {}),
    "取消失敗：",
    (answer, season) => {
      promotedName = applyAutomaticPromotionLocally(
        season,
        game && game.id,
        answer.promoted_from_waitlist,
        leaving
      );
      if (promotedName) {
        changeToken = markGameDetailChange(
          document.getElementById("game-detail"),
          "attending",
          [promotedName],
          changeToken
        );
      }
    }
  );
  settleRosterChange(changeToken, !!result);
  if (result) {
    toast(
      promotedName
        ? `已取消${leaving && leaving.covering ? "代打" : "報名"}；${promotedName} 已遞補`
        : result.promoted_from_waitlist
          ? `已取消${leaving && leaving.covering ? "代打" : "報名"}；候補名單已遞補`
          : `已取消${leaving && leaving.covering ? "代打" : "報名"}`,
      "good"
    );
  }
  // Cancelling offers the slot to the queue; see needsRefreshAfter.
  if (needsRefreshAfter(result)) refreshSeasonQuietly();
}
