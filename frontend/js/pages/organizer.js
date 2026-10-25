// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
// The club is chosen from the top bar, as on the member page.
mountClubChip(document.getElementById("club-chip"), document.getElementById("club-picker"));
const LIFF_ID = "2011156233-6CouG6VI";
let currentSeason = null;
let selectedGameId = null;
let clubMembers = [];
// People this viewer has brought before, for the quick picks; see
// fetchMyGuests.
let myGuests = [];
let myName = "";

// Resolves the caller once; the server enforces the organizer role on
// every action.
const identityReady = (async function initIdentity() {
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  if (identified) {
    myName = identified.name;
    paintSelfAvatar(document.getElementById("avatar-btn"), identified);
  }
  return identified;
})();
document.getElementById("avatar-btn").addEventListener("click", () => {
  location.href = "profile.html";
});

showPageLoading(document.getElementById("hero-wrap"));

// Every read carries the caller's token, so nothing is fetched before
// identity resolves.
identityReady.then((identified) => {
  if (!identified) {
    stopLoadingEscalation();
    document.getElementById("hero-wrap").innerHTML = signInFailureHtml(identified);
    return;
  }
  initClubAndSeasonPickers(
    API_BASE,
    document.getElementById("club-picker"),
    document.getElementById("season-picker"),
    "vf_org_season",
    (id) => {
      stopLoadingEscalation();
      const hasClub = !!currentClubId();
      document.getElementById("season-picker-field").hidden = !id;
      document.getElementById("season-picker").hidden = !id;
      if (id) loadDashboard(id);
      else renderNoSeason();
    },
    (e) => showPageError(document.getElementById("hero-wrap"), e.message),
    // Only clubs this person organizes; see managesClub.
    managesClub
  );
});

function renderNoSeason() {
  currentSeason = null;
  document.getElementById("season-meta").innerHTML = "";
  document.getElementById("meta-label").hidden = true;
  document.getElementById("month-cal").hidden = true;
  document.getElementById("season-picker-field").hidden = true;
  document.getElementById("season-picker").hidden = true;
  document.getElementById("hero-wrap").innerHTML = currentClubId()
    ? emptyStateHtml(
        "這個球隊尚未開季",
        "建立第一季之後，這裡會顯示下一場的出席人數、男女比，以及需要你處理的事情。",
        { label: "建立第一季", href: "organizer-settings.html" }
      )
    : noClubYetHtml();
  document.getElementById("hero-wrap").onclick = null;
}

const dashboardLoad = staleGuard();

async function loadDashboard(seasonId) {
  const ticket = dashboardLoad.take();
  // What the screen knew when this read left; see localRevision.
  const askedAt = localRevision();
  const errorEl = document.getElementById("error");
  errorEl.textContent = "";
  try {
    // Cached copy first, network second — see getJsonSWR.
    await getJsonSWR(`${API_BASE}/seasons/${seasonId}`, (season) => render(season), {
      headers: authHeader(),
    });
  } catch (e) {
    errorEl.textContent = "連不上伺服器：" + e.message;
  }

  function render(season) {
    if (!dashboardLoad.current(ticket)) return;
    // Older than the screen: painting it would undo a tap. A newer read
    // is already queued.
    if (currentSeason && currentSeason.id === season.id && localRevision() !== askedAt) {
      return;
    }
    // Skip identical data: redrawing it costs a visible frame.
    if (currentSeason && JSON.stringify(season) === JSON.stringify(currentSeason)) return;
    const isNewSeason = !currentSeason || currentSeason.id !== season.id;
    currentSeason = season;
    // Keep the selected game across reloads; reset only for a new season
    // or the first load.
    if (isNewSeason || !season.games.some((g) => g.id === selectedGameId)) {
      const upcoming = season.games.find((g) => !describeDate(g.date).isPast) || season.games[season.games.length - 1];
      selectedGameId = upcoming ? upcoming.id : null;
    }

    document.getElementById("season-picker-field").hidden = false;
    document.getElementById("season-picker").hidden = false;
    document.getElementById("meta-label").hidden = false;
    document.getElementById("month-cal").hidden = false;
    renderMeta(season);
    renderHero(season);
    renderCalendar();
    renderSelectedGameDetail();
    // Loads alongside the paint and repaints only if it found something
    // new.
    fetchMyGuests(API_BASE, currentClubId()).then((g) => {
      if (JSON.stringify(g) === JSON.stringify(myGuests)) return;
      myGuests = g;
      renderSelectedGameDetail();
    });
    fetchClubMembers(API_BASE, currentClubId()).then((m) => {
      if (JSON.stringify(m) === JSON.stringify(clubMembers)) return;
      clubMembers = m;
      renderSelectedGameDetail();
    });
    loadTodos();
  }
}

let todos = [];
/** False until the reads behind 待辦 have answered once, so "still looking"
 * differs from "nothing to do".
 */
let todosLoaded = false;

async function loadTodos() {
  const clubId = currentClubId();
  if (!clubId || !currentSeason) return;
  const ask = (path, fallback = []) =>
    fetch(`${API_BASE}${path}`, { headers: authHeader() })
      .then((r) => (r.ok ? r.json() : fallback))
      .catch(() => fallback);
  // Show "still looking" while the three reads are out.
  renderTodos();
  // A failed friendship check is null (unknown), not an empty answer.
  const [requests, balances, reach] = await Promise.all([
    ask(`/clubs/${clubId}/join-requests`),
    ask(`/clubs/${clubId}/balances?season_id=${currentSeason.id}`),
    ask("/players/me/line-reachable", null),
  ]);
  const next = buildTodos(
    currentSeason,
    requests,
    balances,
    reach ? reach.reachable : null
  );
  // Skip repainting an unchanged list, except on the first answer, which
  // must clear the spinner.
  const wasLoading = !todosLoaded;
  todosLoaded = true;
  if (!wasLoading && JSON.stringify(next) === JSON.stringify(todos)) return;
  todos = next;
  renderTodos();
}

function renderTodos() {
  const line = document.getElementById("todo-line");
  // Loading, nothing to do, or the list; the line keeps its place so
  // nothing below moves.
  if (!todosLoaded) {
    line.hidden = false;
    line.disabled = true;
    document.getElementById("todo-dot").className = "spinner sm";
    document.getElementById("todo-main").textContent = "正在檢查待辦事項";
    document.getElementById("todo-more").textContent = "";
    return;
  }
  line.disabled = false;
  line.hidden = todos.length === 0;
  if (line.hidden) return;
  document.getElementById("todo-dot").className = `todo-dot ${todos[0].kind}`;
  document.getElementById("todo-main").textContent = todos[0].label;
  document.getElementById("todo-more").textContent =
    todos.length > 1 ? `還有 ${todos.length - 1} 件` : "";
}

function openTodos() {
  document.getElementById("todo-list").innerHTML = todos
    .map(
      (t, i) => `
        <div class="todo-row">
          <i class="todo-dot ${t.kind}"></i>
          <span class="ts-label">${escapeHtml(t.label)}<span class="ts-sub">${escapeHtml(t.sub)}</span></span>
          <button type="button" class="mini-action" data-todo="${i}">${escapeHtml(t.action)}</button>
        </div>`
    )
    .join("");
  document.getElementById("todo-backdrop").hidden = false;
}

function closeTodos() {
  document.getElementById("todo-backdrop").hidden = true;
}

document.getElementById("todo-list").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-todo]");
  if (!btn) return;
  const todo = todos[Number(btn.dataset.todo)];
  closeTodos();
  if (todo.href) {
    location.href = todo.href;
    return;
  }
  selectGame(todo.gameId);
  openGameSheet();
});

document.getElementById("todo-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "todo-backdrop") closeTodos();
});

/** This season's terms as one line; they are changed on the settings page.
 */
function renderMeta(season) {
  const el = document.getElementById("season-meta");
  const settled = season.settled_at !== null;
  const parts = [
    `${season.games.length} 場`,
    `${season.members.length} 人`,
    // share_per_game is the price without air conditioning, so say so
    // when the season charges for it.
    `每人每場 $${season.share_per_game}${Number(season.ac_surcharge) > 0 ? "（未含冷氣）" : ""}`,
    season.game_start_time && season.game_end_time
      ? `${escapeHtml(season.game_start_time.slice(0, 5))}–${escapeHtml(season.game_end_time.slice(0, 5))}`
      : null,
    season.location ? escapeHtml(season.location) : null,
  ].filter(Boolean);

  el.innerHTML = `
    <div class="sm-line">
      <span>${parts.join(" · ")}</span>
      ${settled ? '<span class="sm-state done">已結算</span>' : '<span class="sm-state">尚未結算</span>'}
    </div>
  `;
}

function renderHero(season) {
  const wrap = document.getElementById("hero-wrap");
  const game =
    season.games.find((g) => g.id === selectedGameId) ||
    season.games.find((g) => !describeDate(g.date).isPast) ||
    season.games[season.games.length - 1];
  if (!game) { wrap.innerHTML = '<div class="empty">本季尚無場次</div>'; return; }
  selectedGameId = game.id;
  const expected = expectedAttendance(season, game);
  const shortPill = expected < season.minimum_roster
    ? `<span class="meta-pill" style="color:var(--warn)">低於最低門檻 <strong>${season.minimum_roster}</strong></span>`
    : `<span class="meta-pill">最低門檻 <strong>${season.minimum_roster}</strong> 人</span>`;
  const cancelled = game.status !== "scheduled";
  // The same card as the member page; 名單 leads because it is where the
  // organizer works.
  wrap.innerHTML = renderGameHero(season, game, {
    metaPills: [shortPill],
    statusHtml: cancelled
      ? `<div class="hero-status absent">${statusLabel(game.status)}</div>`
      : "",
    actionsHtml: cancelled
      ? ""
      : `<button type="button" class="hact" onclick="openGameSheet()">名單與請假</button>
         <button type="button" class="hact quiet" onclick="location.href='organizer-settings.html'">場次設定</button>`,
    moreHtml: cancelled ? "" : acSwitch(season, game),
  });
  wrap.onclick = null;
}

/** The air conditioning's state as a switch, so a wrong setting catches
 * the eye. Absent when the club does not pay for it.
 */
function acSwitch(season, game) {
  if (!(Number(season.ac_surcharge) > 0)) return "";
  const on = game.air_conditioned;
  const each = acExtraEach(season, game);
  return `
    <div class="ac-switch">
      <span class="lbl">本場冷氣</span>
      <span class="ac-seg">
        <button type="button" class="cool" aria-pressed="${on}"
          onclick="toggleAirConditioning(${game.id}, true)">${snowIcon(16)}開</button>
        <button type="button" class="plain" aria-pressed="${!on}"
          onclick="toggleAirConditioning(${game.id}, false)">${snowOffIcon(16)}關</button>
      </span>
      <span class="ac-cost">每人 ${on ? `<b>+$${each}</b>` : '<b class="zero">+$0</b>'}</span>
    </div>`;
}

/** Records whether the air conditioning ran on the night. It changes what
 * the club owes the venue and every member's share, so it asks first.
 */
async function toggleAirConditioning(gameId, on) {
  if (on === currentSeason.games.find((g) => g.id === gameId).air_conditioned) return;
  const each = acExtraEach(currentSeason, currentSeason.games.find((g) => g.id === gameId));
  // Both figures: the club's bill moves by the whole surcharge, each
  // member's share by their part. Correcting the forecast without moving
  // the total is on 設定 · 冷氣場次.
  const surcharge = Number(currentSeason.ac_surcharge) || 0;
  const wording = on
    ? `確定將本場改為開冷氣？\n場地費總額將增加 $${surcharge}，每位成員本場增加 $${each}。`
    : `確定將本場改為未開冷氣？\n場地費總額將減少 $${surcharge}，每位成員本場減少 $${each}。`;
  if (!confirm(wording)) return;
  try {
    await postJson(API_BASE, `/games/${gameId}/air-conditioning`, { air_conditioned: on }, "PUT");
    await loadDashboard(currentSeason.id);
  } catch (e) {
    toast("改冷氣設定失敗：" + e.message);
  }
}

function calendarState(game) {
  if (game.status !== "scheduled") return "away";
  const expected = expectedAttendance(currentSeason, game);
  if (expected >= currentSeason.capacity) return "full";
  if (expected < currentSeason.minimum_roster) return "short";
  return "";
}

function renderCalendar() {
  if (!currentSeason) return;
  renderMonthCalendar(
    document.getElementById("month-cal"),
    currentSeason.games,
    selectGame,
    { stateOf: calendarState, selectedId: selectedGameId, awayLabel: "已取消", viewKey: currentSeason.id }
  );
}

function selectGame(gameId) {
  selectedGameId = gameId;
  // Switches the card above the calendar rather than opening a sheet.
  renderHero(currentSeason);
  renderCalendar();
  renderSelectedGameDetail();
  const card = document.querySelector("#hero-wrap .hero");
  if (card) {
    card.classList.remove("card-swap");
    void card.offsetWidth;
    card.classList.add("card-swap");
  }
}

function renderSelectedGameDetail() {
  const el = document.getElementById("game-detail");
  const game = currentSeason && currentSeason.games.find((g) => g.id === selectedGameId);
  if (!currentSeason || !game) {
    el.innerHTML = '<div class="empty">本季尚無場次</div>';
    return;
  }
  renderGameDetail(el, currentSeason, game, {
    // The card behind this sheet already shows the game.
    compactHead: true,
    // The same pills the member sees on this game.
    heroMetaPills: [acPill(currentSeason, game)],
    statusHtml: game.status === "scheduled"
      ? ""
      : `<div class="hero-status absent">${statusLabel(game.status)}</div>`,
    clubMembers,
    pickFromWholeClub: true,
    myGuests,
    viewerName: myName,
    onAssignSubstitute: (absenceId, name, gender, target) =>
      assignSubstitute(absenceId, name, gender, target),
    onLeaveWaitlist: (entryId, btn) => leaveWaitlistFor(entryId, btn),
    // All three arguments: the third is who steps out to make room.
    onPromoteFromWaitlist: (entryId, target, replacingDropInId) =>
      promoteFromWaitlist(entryId, target, replacingDropInId),
    onCancelSubstitute: (dropInId, btn) => cancelSubstitute(dropInId, btn),
    // The organizer does all of this for others, including members
    // without a LINE account.
    onRecordAbsence: (playerName, btn) => recordAbsenceFor(playerName, btn),
    onRecordAbsenceWithSubstitute: (playerName, name, gender, target) =>
      recordAbsenceWithSubstituteFor(playerName, name, gender, target),
    onCancelAbsence: (absenceId, btn) => cancelAbsenceFor(absenceId, btn),
    onRemoveDropIn: (dropInId, btn) => cancelSubstitute(dropInId, btn),
    // The management screen manages everybody's signups; the server
    // checks the role.
    canRemoveDropIn: () => true,
    onAddDropIn: (name, gender, btn) => addDropIn(name, gender, btn),
    // No canAssignSubstitute: the organizer may arrange anyone's
    // substitute.
  });
}

function openGameSheet() {
  document.getElementById("game-sheet-backdrop").hidden = false;
}

function closeGameSheet() {
  document.getElementById("game-sheet-backdrop").hidden = true;
}

const gameSheetBackdrop = document.getElementById("game-sheet-backdrop");
gameSheetBackdrop.addEventListener("click", (e) => {
  if (e.target === gameSheetBackdrop) closeGameSheet();
});
// Scoped to the game sheet: this page has more than one sheet.
gameSheetBackdrop.querySelector(".gsheet-close").addEventListener("click", closeGameSheet);

function repaintRosterChange() {
  renderHero(currentSeason);
  renderCalendar();
  renderSelectedGameDetail();
}

function settleRosterChange(token, succeeded) {
  const detail = document.getElementById("game-detail");
  const changed = succeeded
    ? completeGameDetailChange(detail, token)
    : clearGameDetailChange(detail, token);
  if (changed) repaintRosterChange();
}

async function assignSubstitute(absenceId, name, gender, target) {
  return whileBusy(target, async () => {
  let changeToken = null;
  try {
    // Turn the id into a name before the reload removes them.
    const game = currentSeason.games.find((g) => g.id === selectedGameId) || {};
    const playingBefore = game.confirmed_drop_ins || [];
    const absence = (game.absences || []).find((a) => a.id === absenceId);
    changeToken = markGameDetailChange(
      document.getElementById("game-detail"),
      "absent",
      absence ? [absence.player_name] : []
    );
    repaintRosterChange();
    const result = await postJson(
      API_BASE,
      `/absences/${absenceId}/substitute`,
      { player_name: name, gender },
      "PUT"
    );
    const bumped = playingBefore.find((d) => d.player_id === result.displaced_player_id);
    applySubstituteLocally(currentSeason, selectedGameId, absenceId, result, { name, gender });
    settleRosterChange(changeToken, true);
    toast(
      bumped ? `已指定 ${name} 代打，${bumped.player_name} 回到候補` : `已指定 ${name} 代打`,
      "good"
    );
    refreshQuietly();
  } catch (e) {
    settleRosterChange(changeToken, false);
    toast("設定代打失敗：" + e.message);
  }
  });
}

/** 請假並指定代打 for somebody else, in one request. Waits for the server, which
 * decides whether the name is accepted.
 */
async function recordAbsenceWithSubstituteFor(playerName, name, gender, target) {
  const gameId = selectedGameId;
  return whileBusy(target, async () => {
  let changeToken = null;
  try {
    changeToken = markGameDetailChange(
      document.getElementById("game-detail"),
      "absent",
      [playerName]
    );
    repaintRosterChange();
    const result = await postJson(API_BASE, "/absences", {
      player_name: playerName,
      game_id: gameId,
      substitute: { player_name: name, gender },
    });
    applyAbsenceWithSubstituteLocally(currentSeason, gameId, playerName, result, {
      name,
      gender,
    });
    settleRosterChange(changeToken, true);
    toast(`已替 ${playerName} 請假，由 ${name} 代打`, "good");
    refreshQuietly();
  } catch (e) {
    settleRosterChange(changeToken, false);
    toast("請假並指定代打失敗：" + e.message);
  }
  });
}

// Changes show on the tap; requests queue and the refresh is not awaited.
// See optimisticRunner.
const optimistically = optimisticRunner({
  getState: () => currentSeason,
  setState: (s) => {
    currentSeason = s;
  },
  repaint: () => repaintRosterChange(),
});

/** Queued behind pending writes and collapsed to one; see
 * coalescedRefresh.
 */
const refreshQuietly = coalescedRefresh(() => {
  if (currentSeason) return loadDashboard(currentSeason.id);
});

/** Real absence ids by game and player, so 取消請假 before 請假 returns waits
 * for it.
 */
const pendingAbsenceId = {};
const absenceKey = (gameId, name) => `${gameId}:${name}`;

async function recordAbsenceFor(playerName, btn) {
  const gameId = selectedGameId;
  const key = absenceKey(gameId, playerName);
  let announceId;
  let promotedName = null;
  let changeToken = null;
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
      game.absences.push({
        id: -1,
        player_name: playerName,
        covered_by: null,
        filled_by: promotedName,
      });
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "absent",
        [playerName]
      );
    },
    async () => {
      const created = await postJson(API_BASE, "/absences", {
        player_name: playerName,
        game_id: gameId,
      });
      announceId(created.id);
      return created;
    },
    "代為請假失敗：",
    (created, season) => {
      if (created.promoted_from_waitlist) return;
      for (const game of season.games) {
        for (const a of game.absences) if (a.id === -1) a.id = created.id;
      }
    }
  );
  if (!result) {
    announceId(null);
    delete pendingAbsenceId[key];
  }
  settleRosterChange(changeToken, !!result);
  if (result) {
    toast(
      result.promoted_from_waitlist && promotedName
        ? `已替 ${playerName} 請假；${promotedName} 已由候補遞補`
        : `已替 ${playerName} 請假`,
      "good"
    );
  }
  // Only when somebody was promoted; see needsRefreshAfter.
  if (
    needsRefreshAfter(result) ||
    Boolean(promotedName) !== Boolean(result && result.promoted_from_waitlist)
  ) refreshQuietly();
}

async function cancelAbsenceFor(absenceId, btn) {
  const gameId = selectedGameId;
  const game = currentSeason.games.find((g) => g.id === gameId);
  const row = (game.absences || []).find((a) => a.id === absenceId);
  const playerName = row ? row.player_name : null;
  const releasedName = row && (row.covered_by || row.filled_by);
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      for (const g of season.games) {
        const released = releasedName
          ? g.confirmed_drop_ins.find((d) => d.player_name === releasedName)
          : null;
        if (released) {
          g.confirmed_drop_ins = g.confirmed_drop_ins.filter((d) => d !== released);
          g.waitlist_entries.unshift({
            id: -1,
            player_id: released.player_id,
            player_name: released.player_name,
            gender: released.gender || null,
            signed_up_by_me: released.signed_up_by_me === true,
          });
        }
        g.absences = g.absences.filter((a) => a.id !== absenceId);
      }
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "attending",
        playerName ? [playerName] : []
      );
    },
    async () => {
      let id = absenceId;
      if (!(id > 0) && playerName) {
        id = await pendingAbsenceId[absenceKey(gameId, playerName)];
        delete pendingAbsenceId[absenceKey(gameId, playerName)];
      }
      if (!(id > 0)) throw new Error("這筆請假尚未建立成功");
      return postJson(API_BASE, `/absences/${id}/cancel`, {});
    },
    "取消請假失敗："
  );
  settleRosterChange(changeToken, !!result);
  if (result) {
    toast(
      result.released_player_id && releasedName
        ? `已取消 ${playerName} 的請假；${releasedName} 已回到候補`
        : `已取消 ${playerName} 的請假`,
      "good"
    );
  }
  // Only when somebody went back to the queue; see needsRefreshAfter.
  if (
    needsRefreshAfter(result) ||
    Boolean(releasedName) !== Boolean(result && result.released_player_id)
  ) refreshQuietly();
}

async function addDropIn(name, gender, btn) {
  const gameId = selectedGameId;
  let predictedStatus = null;
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      const game = season.games.find((g) => g.id === gameId);
      predictedStatus = isGameFull(season, game) ? "waitlisted" : "confirmed";
      if (predictedStatus === "confirmed") {
        game.confirmed_drop_ins.push({
          id: -1,
          player_id: -1,
          player_name: name,
          gender: gender || null,
          covering: null,
          linked: false,
          // Same rule as the server: null when signing yourself up.
          brought_by_name: name === myName ? null : myName,
        });
      } else {
        game.waitlist_entries.push({
          id: -1,
          player_id: -1,
          player_name: name,
          gender: gender || null,
          brought_by_name: name === myName ? null : myName,
          signed_up_by_me: true,
        });
      }
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        predictedStatus === "confirmed" ? "attending" : "queued",
        [name]
      );
    },
    async () => {
      const created = await postJson(API_BASE, "/drop-ins", {
        player_name: name,
        game_id: gameId,
      });
      // Set a new drop-in's gender for the roster's male/female count.
      if (gender && created.player_id) {
        await postJson(API_BASE, `/players/${created.player_id}/gender`, { gender }, "PUT")
          .catch(() => {});
      }
      return created;
    },
    "新增臨打失敗：",
    (created, season) => {
      if (created.status !== predictedStatus) return;
      const game = season.games.find((g) => g.id === gameId);
      const rows = created.status === "confirmed"
        ? game.confirmed_drop_ins
        : game.waitlist_entries;
      const row = rows.find((item) => item.id === -1 && item.player_name === name);
      if (row) {
        row.id = created.id;
        row.player_id = created.player_id;
      }
    }
  );
  settleRosterChange(changeToken, !!result);
  const input = document.querySelector("[data-new-dropin]");
  if (input) input.value = "";
  if (result) {
    toast(
      result.status === "confirmed"
        ? `${name} 已加入出席名單`
        : `${name} 已加入候補名單`,
      "good"
    );
  }
  if (!result || result.status !== predictedStatus) refreshQuietly();
}

/** Removes someone from the queue (POST /waitlist/{id}/cancel). */
async function leaveWaitlistFor(entryId, btn) {
  if (entryId < 0) return;
  const game = currentSeason.games.find((item) =>
    item.waitlist_entries.some((entry) => entry.id === entryId)
  );
  const leaving = game && game.waitlist_entries.find((entry) => entry.id === entryId);
  let changeToken = null;
  const result = await optimistically(
    (season) => {
      for (const g of season.games) {
        g.waitlist_entries = g.waitlist_entries.filter((w) => w.id !== entryId);
      }
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "queued",
        []
      );
    },
    () => postJson(API_BASE, `/waitlist/${entryId}/cancel`, {}),
    "移除候補失敗："
  );
  settleRosterChange(changeToken, !!result);
  if (result) toast(`已將 ${leaving ? leaving.player_name : "這位成員"} 移出候補`, "good");
}

/** Puts a queued person on court, in the order the organizer decides. On a
 * full game, who comes off is named and both happen in one request, so
 * the automatic promotion cannot take the slot in between.
 */
async function promoteFromWaitlist(entryId, target, replacingDropInId) {
  const game = currentSeason.games.find((g) => g.id === selectedGameId);
  const entry = game.waitlist_entries.find((w) => w.id === entryId);
  const replacing = replacingDropInId
    ? game.confirmed_drop_ins.find((d) => d.id === replacingDropInId)
    : null;

  if (isGameFull(currentSeason, game) && !replacing) {
    toast(`本場已滿 ${currentSeason.capacity} 人，且沒有可換下的臨打。請先登記一位請假，或於設定調高人數上限。`);
    return;
  }

  let changeToken = null;
  const result = await optimistically(
    (season) => {
      // Move with placeholders now; the real ids arrive below.
      promoteWaitlistLocally(
        season,
        game.id,
        entryId,
        replacing ? replacing.id : null,
        { player_id: entry.player_id || -1, drop_in_id: -1 }
      );
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "attending",
        [entry.player_name]
      );
    },
    () => postJson(API_BASE, `/waitlist/${entryId}/promote`, {
      replacing_drop_in_id: replacing ? replacing.id : null,
    }),
    "遞補失敗：",
    (answer, season) => {
      const current = season.games
        .find((item) => item.id === game.id)
        ?.confirmed_drop_ins.find((item) => item.id === -1 && item.player_name === entry.player_name);
      if (current) {
        current.id = answer.drop_in_id;
        current.player_id = answer.player_id;
      }
    }
  );
  settleRosterChange(changeToken, !!result);
  if (result) {
    toast(
      replacing
        ? `${entry.player_name} 已遞補上場，${replacing.player_name} 回到候補`
        : `${entry.player_name} 已遞補上場`,
      "good"
    );
    if (replacing) refreshQuietly();
  }
}

async function cancelSubstitute(dropInId, btn) {
  if (dropInId < 0) return; // still saving; the real id isn't known yet
  const game = currentSeason.games.find((item) =>
    item.confirmed_drop_ins.some((dropIn) => dropIn.id === dropInId)
  );
  const leaving = game && game.confirmed_drop_ins.find((dropIn) => dropIn.id === dropInId);
  let promotedName = null;
  let changeToken = null;
  const result = await optimistically(
    // Clears the absence that named them too; see removeDropInLocally.
    (season) => {
      removeDropInLocally(season, dropInId);
      changeToken = markGameDetailChange(
        document.getElementById("game-detail"),
        "attending",
        []
      );
    },
    () => postJson(API_BASE, `/drop-ins/${dropInId}/cancel`, {}),
    "移除失敗：",
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
        ? `已移除 ${leaving ? leaving.player_name : "臨打"}；${promotedName} 已遞補`
        : result.promoted_from_waitlist
          ? `已移除 ${leaving ? leaving.player_name : "臨打"}；候補名單已遞補`
          : `已移除 ${leaving ? leaving.player_name : "臨打"}`,
      "good"
    );
  }
  // The freed slot is offered to the queue; see needsRefreshAfter.
  if (needsRefreshAfter(result)) refreshQuietly();
}

function statusLabel(status) {
  if (status === "cancelled_unrefunded") return "取消・場地費不退";
  if (status === "cancelled_refunded") return "取消・場地費已退";
  return status;
}
