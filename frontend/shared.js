// Shared helpers for member.html and organizer.html — date formatting,
// the season picker, and the month calendar. One copy so the two pages
// can't quietly drift apart on how they show the same data.

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

/** Escape a value for safe interpolation into innerHTML. Names shown on
 * these pages can come straight from a LINE display name — attacker
 * controlled once anyone can join via the LIFF link — so anywhere a
 * name (or other free-text field) gets built into an HTML string
 * instead of set via textContent, it must go through this first. */
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function dateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Weekday label + a relative "今天/明天/已結束" hint for one game date. */
function describeDate(dateStr) {
  const date = new Date(dateStr + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((date - today) / 86400000);

  let relative = "";
  if (diffDays === 0) relative = "今天";
  else if (diffDays === 1) relative = "明天";
  else if (diffDays < 0) relative = "已結束";
  else if (diffDays <= 7) relative = `${diffDays} 天後`;

  return {
    label: `${date.getMonth() + 1}/${date.getDate()}（週${WEEKDAYS[date.getDay()]}）`,
    month: date.getMonth() + 1,
    day: String(date.getDate()),
    weekday: `週${WEEKDAYS[date.getDay()]}`,
    relative,
    isPast: diffDays < 0,
    isToday: diffDays === 0,
  };
}

/** How many people are expected at a game right now. */
function expectedAttendance(season, game) {
  return season.members.length - game.absences.length + game.confirmed_drop_ins.length;
}

/** How many of the people expected at this game are each gender — for
 * the hero card's 男/女 pills and capacity bar. A member who recorded an
 * absence isn't attending directly (their substitute, if any, is
 * already counted separately as a confirmed drop-in); a gender-less
 * member or drop-in counts toward the total but not toward either pill,
 * same as the roster elsewhere in this app never requires gender. */
function computeGenderCounts(season, game) {
  const absentNames = new Set(game.absences.map((a) => a.player_name));
  let male = 0;
  let female = 0;
  for (const m of season.members) {
    if (absentNames.has(m.name)) continue;
    if (m.gender === "male") male++;
    else if (m.gender === "female") female++;
  }
  for (const d of game.confirmed_drop_ins) {
    if (d.gender === "male") male++;
    else if (d.gender === "female") female++;
  }
  return { male, female };
}

/**
 * The hero card shown at the top of the member/organizer home screen and
 * inside a game's detail sheet: date, expected headcount with the
 * male/female split, and a row of small meta facts. Returns markup, not
 * a rendered container, so callers can place it above their own
 * page-specific content (an action button, a status line).
 *
 * `opts.metaPills` — extra `<span class="meta-pill">` HTML beyond the
 * per-game share, e.g. a change deadline or the minimum-roster count.
 */
function renderGameHero(season, game, opts) {
  const o = opts || {};
  const info = describeDate(game.date);
  const expected = expectedAttendance(season, game);
  const { male, female } = computeGenderCounts(season, game);
  const capacity = season.capacity;
  const full = expected >= capacity;

  const whenParts = [info.weekday];
  if (season.game_start_time && season.game_end_time) {
    whenParts.push(escapeHtml(`${season.game_start_time.slice(0, 5)}–${season.game_end_time.slice(0, 5)}`));
  }
  if (season.location) whenParts.push(escapeHtml(season.location));

  return `
    <div class="hero">
      <div class="hero-head">
        <div>
          <div class="hero-date">${info.month} / ${info.day}</div>
          <div class="hero-when">${whenParts.join(" · ")}</div>
        </div>
        ${info.relative ? `<span class="hero-rel">${info.relative}</span>` : ""}
      </div>
      <div class="count-row">
        <span class="count-big${full ? " full" : ""}">${expected}</span><span class="count-cap">/ ${capacity} 人</span>
        <span class="gender-pill m">男 ${male}</span><span class="gender-pill f">女 ${female}</span>
      </div>
      <div class="capacity-bar">
        <i class="fill-m" style="width:${Math.min(100, (male / capacity) * 100)}%"></i>
        <i class="fill-f" style="width:${Math.min(100, (female / capacity) * 100)}%"></i>
      </div>
      <div class="hero-meta">
        <span class="meta-pill">每場 <strong>$${season.share_per_game}</strong></span>
        ${(o.metaPills || []).join("")}
      </div>
    </div>
  `;
}

/** "2026年7月" / "2026年7~9月" / "2025年12月~2026年2月" — a season's
 * game-date span read as a season, not an ISO date range. */
function formatSeasonLabel(season) {
  const first = new Date(season.first_game_date + "T00:00:00");
  const last = new Date(season.last_game_date + "T00:00:00");
  const fy = first.getFullYear();
  const fm = first.getMonth() + 1;
  const ly = last.getFullYear();
  const lm = last.getMonth() + 1;

  if (fy === ly && fm === lm) return `${fy}年${fm}月`;
  if (fy === ly) return `${fy}年${fm}~${lm}月`;
  return `${fy}年${fm}月~${ly}年${lm}月`;
}

const CLUB_STORAGE_KEY = "vf_club";

/** The club the pickers currently have selected, or null. Anything that
 * calls a club-scoped endpoint (ledgers, payments) reads it from here
 * rather than threading it through every function. */
function currentClubId() {
  return localStorage.getItem(CLUB_STORAGE_KEY);
}

/**
 * Wires the club <select> and season <select> together: picking a club
 * reloads that club's seasons, picking a season calls onSeasonChange
 * (also once immediately). Both remember their last choice in
 * localStorage. onSeasonChange(null) means "nothing to show" — no clubs
 * exist yet, or this club has no seasons.
 *
 * Seasons live under a club now (GET /clubs/{id}/seasons), so the two
 * pickers can't be initialised independently: the season list is
 * meaningless until a club is chosen.
 */
async function initClubAndSeasonPickers(
  apiBase,
  clubEl,
  seasonEl,
  seasonStorageKey,
  onSeasonChange,
  onError
) {
  // Without this, a failed fetch (offline, CORS, a backend that never
  // woke up) rejected an un-awaited promise and the page just sat there
  // blank forever with nothing said. Now the caller gets to show the
  // failure — see showPageError.
  try {
    await loadClubs();
  } catch (e) {
    console.error("Could not load clubs:", e);
    if (onError) onError(e);
    else onSeasonChange(null);
  }

  async function loadClubs() {
  const res = await fetch(`${apiBase}/clubs`);
  const clubs = res.ok ? await res.json() : [];

  if (clubs.length === 0) {
    clubEl.innerHTML = '<option value="">尚無球隊</option>';
    seasonEl.innerHTML = '<option value="">尚無任何季別</option>';
    onSeasonChange(null);
    return;
  }

  clubEl.innerHTML = clubs
    .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    .join("");

  const rememberedClub = localStorage.getItem(CLUB_STORAGE_KEY);
  if (rememberedClub && clubs.some((c) => String(c.id) === rememberedClub)) {
    clubEl.value = rememberedClub;
  }
  localStorage.setItem(CLUB_STORAGE_KEY, clubEl.value);

  async function loadSeasons() {
    const seasonRes = await fetch(`${apiBase}/clubs/${clubEl.value}/seasons`);
    const seasons = seasonRes.ok ? await seasonRes.json() : [];

    if (seasons.length === 0) {
      seasonEl.innerHTML = '<option value="">尚無任何季別</option>';
      onSeasonChange(null);
      return;
    }

    seasonEl.innerHTML = seasons
      .map((s) => {
        const settledTag = s.settled ? " · 已結算" : "";
        return `<option value="${s.id}">${formatSeasonLabel(s)}（${s.total_games} 場・${s.member_count} 人）${settledTag}</option>`;
      })
      .join("");

    const remembered = localStorage.getItem(seasonStorageKey);
    if (remembered && seasons.some((s) => String(s.id) === remembered)) {
      seasonEl.value = remembered;
    }

    // Assignment rather than addEventListener: loadSeasons runs again on
    // every club change, and addEventListener would stack one more
    // handler each time.
    seasonEl.onchange = () => {
      localStorage.setItem(seasonStorageKey, seasonEl.value);
      onSeasonChange(seasonEl.value);
    };

    onSeasonChange(seasonEl.value);
  }

  clubEl.onchange = () => {
    localStorage.setItem(CLUB_STORAGE_KEY, clubEl.value);
    loadSeasons();
  };

  await loadSeasons();
  }
}

/**
 * A real month-grid calendar. Opens on the month of the nearest
 * upcoming game, marks every day that has one, and lets you page
 * between months. Tapping a marked day calls onPick(gameId).
 */
function renderMonthCalendar(container, games, onPick) {
  const gamesByDate = {};
  for (const g of games) gamesByDate[g.date] = g;

  const upcoming = games.find((g) => !describeDate(g.date).isPast) || games[games.length - 1];
  const viewDate = upcoming ? new Date(upcoming.date + "T00:00:00") : new Date();
  viewDate.setDate(1);

  function draw() {
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    const firstWeekday = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const todayKey = dateKey(new Date());

    let cells = "";
    for (let i = 0; i < firstWeekday; i++) cells += `<div class="mcal-cell empty"></div>`;
    for (let d = 1; d <= daysInMonth; d++) {
      const key = dateKey(new Date(year, month, d));
      const game = gamesByDate[key];
      const cls = ["mcal-cell"];
      if (key === todayKey) cls.push("today");
      if (game) cls.push("has-game");
      cells += `<div class="${cls.join(" ")}" ${game ? `data-game-id="${game.id}"` : ""}>
        <span>${d}</span>${game ? '<div class="mcal-dot"></div>' : ""}
      </div>`;
    }

    container.innerHTML = `
      <div class="mcal-head">
        <button class="mcal-nav" data-dir="-1" aria-label="上個月">‹</button>
        <span class="mcal-title">${year} 年 ${month + 1} 月</span>
        <button class="mcal-nav" data-dir="1" aria-label="下個月">›</button>
      </div>
      <div class="mcal-grid mcal-weekdays"><div>日</div><div>一</div><div>二</div><div>三</div><div>四</div><div>五</div><div>六</div></div>
      <div class="mcal-grid">${cells}</div>
    `;
  }

  container.onclick = (e) => {
    const navBtn = e.target.closest(".mcal-nav");
    if (navBtn) {
      viewDate.setMonth(viewDate.getMonth() + Number(navBtn.dataset.dir));
      draw();
      return;
    }
    const cell = e.target.closest(".mcal-cell.has-game");
    if (cell) onPick(Number(cell.dataset.gameId));
  };

  draw();
}

/**
 * One game's full detail: a hero card up top (same as the home screen's),
 * then "異動" — only the rows that differ from a plain fixed member
 * showing up (an absence, a substitute, a drop-in) — with the full
 * roster folded away behind a button, since most members most weeks are
 * just... there, and don't need to be read one by one.
 *
 * `options`:
 *   extraHtml — a page's own content (e.g. a request-leave button, a
 *     cancelled-game status line), shown at the very end.
 *   heroMetaPills — extra `<span class="meta-pill">` HTML for the hero
 *     card (a change deadline, the minimum-roster count).
 *   clubMembers — [{id, name, gender}], the pool a substitute is picked
 *     from by tapping instead of typing; manual entry still works
 *     underneath as a fallback for someone not in the list yet.
 *   viewerName — the current viewer's resolved name, used only to mark
 *     "(你)" on their own waitlist row.
 *   onAssignSubstitute(absenceId, name, gender) — if given, an
 *     uncovered absent row gets a 設定代打/編輯代打 control that calls
 *     this — available any time, even once the game is locked, since
 *     swapping who covers a slot doesn't create the last-minute-
 *     understaffed risk the change deadline protects against.
 *   onCancelSubstitute(dropInId) — if given, a covered absence also
 *     gets a 取消代打 control, but only before the change deadline —
 *     removing coverage outright is exactly what that deadline guards.
 *   canAssignSubstitute(absence) — gates both of the above controls
 *     per absence (default: all of them — an organizer can arrange
 *     anyone's substitute; member.html restricts this to the viewer's
 *     own row).
 */
function renderGameDetail(container, season, game, options) {
  const opts = options || {};
  const extraHtml = opts.extraHtml || "";
  const clubMembers = opts.clubMembers || [];
  const viewerName = opts.viewerName || "";
  const onAssignSubstitute = opts.onAssignSubstitute;
  const onCancelSubstitute = opts.onCancelSubstitute;
  const canAssignSubstitute = opts.canAssignSubstitute || (() => true);

  function genderTag(g) {
    if (g === "male") return '<span class="gender-tag male">男</span>';
    if (g === "female") return '<span class="gender-tag female">女</span>';
    return "";
  }

  function initial(name) {
    return escapeHtml((name || "?").trim().slice(0, 1));
  }

  function substituteForm(absence) {
    const candidates = clubMembers.filter((m) => m.name !== absence.player_name);
    const covering = game.confirmed_drop_ins.find((d) => d.covering === absence.player_name);
    const pickRows = candidates
      .map(
        (m) => `
          <div class="pick-row" data-pick="${absence.id}" data-pick-name="${escapeHtml(m.name)}" data-pick-gender="${m.gender || ""}">
            <i class="radio"></i><span class="avatar sm">${initial(m.name)}</span>
            <span class="pk-name">${escapeHtml(m.name)}</span>${genderTag(m.gender)}
          </div>`
      )
      .join("");
    const maleSelected = covering && covering.gender === "male" ? " selected" : "";
    const femaleSelected = covering && covering.gender === "female" ? " selected" : "";
    return `
      <div class="sub-form" data-sub-form="${absence.id}" hidden>
        ${candidates.length ? `<div class="sub-pick">${pickRows}</div><div class="or-line"><span>不在名單上</span></div>` : ""}
        <input type="text" placeholder="直接輸入名字" data-sub-name="${absence.id}" value="${escapeHtml(absence.covered_by || "")}">
        <select data-sub-gender="${absence.id}">
          <option value="">性別</option>
          <option value="male"${maleSelected}>男</option>
          <option value="female"${femaleSelected}>女</option>
        </select>
        <button type="button" data-confirm-sub="${absence.id}">確認</button>
      </div>
    `;
  }

  const diffRows = [];
  for (const absence of game.absences) {
    const allowed = canAssignSubstitute(absence);
    const covering = game.confirmed_drop_ins.find((d) => d.covering === absence.player_name);
    const note = absence.covered_by ? `${escapeHtml(absence.covered_by)} 代打` : "無代打";

    const offerAssign = !!onAssignSubstitute && allowed;
    const assignLabel = absence.covered_by ? "編輯代打" : "指定代打";
    const assignControl = offerAssign
      ? `<button type="button" class="mini-action" data-toggle-sub="${absence.id}">${assignLabel}</button>`
      : "";

    const offerCancel = !!absence.covered_by && !game.locked && !!onCancelSubstitute && allowed && covering;
    const cancelControl = offerCancel
      ? `<button type="button" class="mini-action danger" data-cancel-sub="${covering.id}">取消代打</button>`
      : "";

    diffRows.push(`
      <div class="roster-row absent">
        <span><span class="avatar sm">${initial(absence.player_name)}</span> ${escapeHtml(absence.player_name)}</span>
        <span class="roster-note">請假・${note}${assignControl}${cancelControl}</span>
      </div>
      ${offerAssign ? substituteForm(absence) : ""}
    `);
  }
  for (const d of game.confirmed_drop_ins) {
    const note = d.covering ? `代打・${escapeHtml(d.covering)}` : "臨打";
    diffRows.push(`
      <div class="roster-row dropin">
        <span><span class="avatar sm">${initial(d.player_name)}</span> ${escapeHtml(d.player_name)}${genderTag(d.gender)}</span>
        <span class="roster-note">${note}</span>
      </div>
    `);
  }

  const absentNames = new Set(game.absences.map((a) => a.player_name));
  const fullRosterRows = season.members
    .map((m) => {
      if (absentNames.has(m.name)) {
        return `<div class="roster-row absent"><span>${escapeHtml(m.name)}${genderTag(m.gender)}</span><span class="roster-note">請假</span></div>`;
      }
      return `<div class="roster-row present"><span>${escapeHtml(m.name)}${genderTag(m.gender)}</span></div>`;
    })
    .join("");

  const waitlistRows = game.waitlist_entries.length
    ? game.waitlist_entries
        .map((w, i) => {
          const isMe = viewerName && w.player_name === viewerName;
          return `<div class="wl-row${isMe ? " me" : ""}"><span class="wl-num">${i + 1}</span><span class="wl-name">${escapeHtml(w.player_name)}${genderTag(w.gender)}${isMe ? "（你）" : ""}</span></div>`;
        })
        .join("")
    : "";

  container.innerHTML = `
    ${renderGameHero(season, game, { metaPills: opts.heroMetaPills })}
    ${game.locked ? '<div class="gdetail-locked">已過更動期限，這一場無法再變更</div>' : ""}
    ${
      diffRows.length
        ? `<div class="gdetail-section-label">異動</div><div class="gdetail-roster">${diffRows.join("")}</div>`
        : ""
    }
    ${
      waitlistRows
        ? `<div class="gdetail-section-label">候補（${game.waitlist_entries.length} 人）</div><div class="gdetail-waitlist">${waitlistRows}</div>`
        : ""
    }
    <button type="button" class="gdetail-toggle" data-toggle-roster>完整名單（${season.members.length} 人）▾</button>
    <div class="gdetail-roster-wrap" data-roster-wrap hidden>
      <div class="gdetail-roster">${fullRosterRows}</div>
    </div>
    ${extraHtml}
  `;

  // Assigned directly (not addEventListener) so re-rendering this same
  // container never accumulates duplicate handlers — matches
  // renderMonthCalendar's pattern.
  container.onclick = (e) => {
    const toggleRoster = e.target.closest("[data-toggle-roster]");
    if (toggleRoster) {
      const wrap = container.querySelector("[data-roster-wrap]");
      wrap.hidden = !wrap.hidden;
      toggleRoster.textContent = `完整名單（${season.members.length} 人）${wrap.hidden ? "▾" : "▴"}`;
      return;
    }
    const toggleSub = e.target.closest("[data-toggle-sub]");
    if (toggleSub) {
      const form = container.querySelector(`[data-sub-form="${toggleSub.dataset.toggleSub}"]`);
      if (form) form.hidden = !form.hidden;
      return;
    }
    const pick = e.target.closest("[data-pick]");
    if (pick) {
      const id = pick.dataset.pick;
      const nameInput = container.querySelector(`[data-sub-name="${id}"]`);
      const genderSelect = container.querySelector(`[data-sub-gender="${id}"]`);
      if (nameInput) nameInput.value = pick.dataset.pickName;
      if (genderSelect) genderSelect.value = pick.dataset.pickGender || "";
      container
        .querySelectorAll(`[data-pick="${id}"]`)
        .forEach((row) => row.classList.toggle("on", row === pick));
      return;
    }
    const confirmSub = e.target.closest("[data-confirm-sub]");
    if (confirmSub && onAssignSubstitute) {
      const id = confirmSub.dataset.confirmSub;
      const nameInput = container.querySelector(`[data-sub-name="${id}"]`);
      const genderSelect = container.querySelector(`[data-sub-gender="${id}"]`);
      const name = nameInput ? nameInput.value.trim() : "";
      if (!name) return;
      onAssignSubstitute(Number(id), name, (genderSelect && genderSelect.value) || null);
      return;
    }
    const cancelSub = e.target.closest("[data-cancel-sub]");
    if (cancelSub && onCancelSubstitute) {
      onCancelSubstitute(Number(cancelSub.dataset.cancelSub), cancelSub);
    }
  };
}

/** Disables a button and swaps its label while an async action runs,
 * restoring it on failure (a successful action usually re-renders the
 * whole page anyway). Makes a tap feel acknowledged immediately instead
 * of sitting dead until the network call resolves. */
async function withButtonFeedback(btn, busyLabel, action) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyLabel;
  try {
    await action();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = original;
    throw e;
  }
}

/** POST or PUT a JSON body, returning the parsed response or throwing
 * with the API's own error detail. */
/** Authorization header for the current LIFF session, or {} if there
 * isn't one — every request that might need identity spreads this in,
 * and it's a no-op when LIFF was never initialized (identify_player and
 * every write it gates would then just be rejected server-side, exactly
 * as if the header were simply absent). A fresh token is fetched every
 * call rather than cached: liff.getIDToken() already handles refreshing
 * it, so caching here would just risk holding an expired one. */
function authHeader() {
  try {
    if (typeof liff !== "undefined" && liff.isLoggedIn()) {
      return { Authorization: `Bearer ${liff.getIDToken()}` };
    }
  } catch (e) {
    console.warn("No LIFF identity available:", e);
  }
  return {};
}

/** Initializes LIFF — logging in via redirect if this isn't already a
 * logged-in LIFF session — then resolves this device's real identity
 * through /players/identify. Works the same whether opened inside the
 * LINE app or in a plain desktop browser: liff.login() falls back to a
 * LINE Login web redirect outside the app, so this one flow covers both
 * member.html and the organizer pages.
 *
 * Returns the identified player ({id, name, avatar_url, gender}), or
 * null if LIFF genuinely isn't available (login declined, or a real
 * error) — callers should degrade to a read-only view in that case,
 * since nothing requiring identity will succeed anyway.
 */
async function initLiffIdentity(apiBase, liffId) {
  try {
    await liff.init({ liffId });
    if (!liff.isLoggedIn()) {
      liff.login();
      return null; // page reloads after LINE login redirects back
    }
    const profile = await liff.getProfile();
    return await postJson(apiBase, "/players/identify", {
      id_token: liff.getIDToken(),
      display_name: profile.displayName,
      picture_url: profile.pictureUrl,
    });
  } catch (e) {
    console.warn("LIFF/identify unavailable:", e);
    return null;
  }
}

async function postJson(apiBase, path, body, method) {
  const res = await fetch(`${apiBase}${path}`, {
    method: method || "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

/**
 * A *preview* of each member's cost for one game, mirroring
 * pricing.share_per_game — see docs/billing-rules.md "Core formula".
 *
 * The server stays authoritative: this only exists so the 開新一季 wizard
 * can show the effect of adding a date or a member as you type, which is
 * the whole point of that screen. Anything actually written to a ledger
 * is computed in Python, and the page re-reads the server's number right
 * after creating the season. Integer inputs divide exactly in IEEE754
 * below 2^53, so ceil here agrees with ROUND_CEILING on Decimal.
 */
function previewSharePerGame(totalVenueCost, totalGames, memberCount) {
  if (!(totalGames > 0) || !(memberCount > 0)) return null;
  return Math.ceil(Number(totalVenueCost) / (totalGames * memberCount));
}

let _loadingEscalation = null;

/** Says "still loading" in the same box that will later hold either the
 * content or the empty state, so those three states are never confusable.
 * Escalates its wording after a few seconds: the backend is on a free
 * tier that sleeps, and a silent 30-second wait is indistinguishable
 * from a broken app unless the page says what's happening. */
function showPageLoading(container) {
  if (!container) return;
  container.innerHTML = `
    <div class="page-state">
      <div class="spinner" role="status" aria-label="載入中"></div>
      <p data-loading-text>載入中…</p>
    </div>
  `;
  clearTimeout(_loadingEscalation);
  _loadingEscalation = setTimeout(() => {
    const text = container.querySelector("[data-loading-text]");
    if (text) text.textContent = "伺服器休眠中，正在喚醒——第一次開啟大約需要 30 秒。";
  }, 4000);
}

/** The third state: the request actually failed. Always offers a way
 * out (retry) rather than leaving a dead page. */
function showPageError(container, detail) {
  clearTimeout(_loadingEscalation);
  if (!container) return;
  container.innerHTML = `
    <div class="empty-state">
      <h3>連不上伺服器</h3>
      <p>可能是網路不穩，或伺服器還在喚醒中。稍等一下再試一次。${detail ? `<br><span style="font-size:.75rem;opacity:.7">${escapeHtml(detail)}</span>` : ""}</p>
      <button type="button" class="btn btn-primary" onclick="location.reload()">重新載入</button>
    </div>
  `;
}

function stopLoadingEscalation() {
  clearTimeout(_loadingEscalation);
}

/** The "there's nothing here yet, and here's what to do about it" block.
 * Every page that can legitimately have no data (a club with no season,
 * a player in no club) renders one of these instead of leaving its
 * styled-but-empty cards on screen, which reads as a broken page rather
 * than an empty one. `action` is optional: {label, href}. */
function emptyStateHtml(title, body, action) {
  return `
    <div class="empty-state">
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(body)}</p>
      ${action ? `<a class="btn btn-primary" href="${action.href}">${escapeHtml(action.label)}</a>` : ""}
    </div>
  `;
}

/** Everyone in a club (fixed member or not) — the pool the 指定代打
 * picker offers before falling back to typing a name. Public endpoint,
 * no auth needed. Returns [] rather than throwing on failure, since a
 * missing picker list should just fall back to the manual text input,
 * not break the page. */
async function fetchClubMembers(apiBase, clubId) {
  try {
    const res = await fetch(`${apiBase}/clubs/${clubId}/members`);
    return res.ok ? await res.json() : [];
  } catch (e) {
    console.warn("Could not load club members:", e);
    return [];
  }
}
