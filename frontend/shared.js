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
  onSeasonChange
) {
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
 * One game's full detail: time, location, per-game price, and how many
 * are expected are shown first; the whole roster (explicit absence ->
 * drop-in coverage, not two separate flat lists) is behind a toggle so
 * picking a day doesn't dump a wall of names on you immediately.
 *
 * `options`:
 *   extraHtml — a page's own content (e.g. an action button), shown
 *     above the roster toggle.
 *   onAssignSubstitute(absenceId, name, gender) — if given, uncovered
 *     absent row gets a 設定代打/編輯代打 control that calls this —
 *     available any time, even once the game is locked, since swapping
 *     who covers a slot doesn't create the last-minute-understaffed
 *     risk the change deadline protects against; a body still fills
 *     it either way.
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
  const onAssignSubstitute = opts.onAssignSubstitute;
  const onCancelSubstitute = opts.onCancelSubstitute;
  const canAssignSubstitute = opts.canAssignSubstitute || (() => true);

  const info = describeDate(game.date);
  const expected = expectedAttendance(season, game);

  const metaParts = [];
  if (season.game_start_time && season.game_end_time) {
    metaParts.push(`${season.game_start_time.slice(0, 5)}-${season.game_end_time.slice(0, 5)}`);
  }
  if (season.location) metaParts.push(season.location);
  metaParts.push(`每人每場 $${season.share_per_game}`);

  function genderTag(g) {
    if (g === "male") return '<span class="gender-tag male">男</span>';
    if (g === "female") return '<span class="gender-tag female">女</span>';
    return "";
  }

  const absenceByName = {};
  for (const a of game.absences) absenceByName[a.player_name] = a;

  const rosterRows = season.members
    .map((m) => {
      const absence = absenceByName[m.name];
      if (!absence) {
        return `<div class="roster-row present"><span>${m.name}${genderTag(m.gender)}</span></div>`;
      }

      const allowed = canAssignSubstitute(absence);
      const covering = game.confirmed_drop_ins.find((d) => d.covering === m.name);
      const note = absence.covered_by ? `已由 ${absence.covered_by} 遞補` : "尚無人遞補";

      const offerAssign = !!onAssignSubstitute && allowed;
      const assignLabel = absence.covered_by ? "編輯代打" : "設定代打";
      const assignControl = offerAssign
        ? `<button class="mini-action" data-toggle-sub="${absence.id}">${assignLabel}</button>`
        : "";

      const offerCancel = !!absence.covered_by && !game.locked && !!onCancelSubstitute && allowed && covering;
      const cancelControl = offerCancel
        ? `<button class="mini-action danger" data-cancel-sub="${covering.id}">取消代打</button>`
        : "";

      const nameValue = absence.covered_by || "";
      const maleSelected = covering && covering.gender === "male" ? " selected" : "";
      const femaleSelected = covering && covering.gender === "female" ? " selected" : "";
      const subForm = offerAssign
        ? `<div class="sub-form" data-sub-form="${absence.id}" hidden>
             <input type="text" placeholder="代打姓名" data-sub-name="${absence.id}" value="${nameValue}">
             <select data-sub-gender="${absence.id}">
               <option value="">性別</option>
               <option value="male"${maleSelected}>男</option>
               <option value="female"${femaleSelected}>女</option>
             </select>
             <button data-confirm-sub="${absence.id}">確認</button>
           </div>`
        : "";

      return `
        <div class="roster-row absent">
          <span>${m.name}${genderTag(m.gender)}</span>
          <span class="roster-note">${note}${assignControl}${cancelControl}</span>
        </div>
        ${subForm}
      `;
    })
    .join("");

  const dropInRows = game.confirmed_drop_ins
    .map((d) => {
      const note = d.covering ? `遞補 ${d.covering}` : "遞補開放名額";
      return `<div class="roster-row dropin"><span>${d.player_name}${genderTag(d.gender)}</span><span class="roster-note">${note}</span></div>`;
    })
    .join("");

  const waitlistText = game.waitlist_entries.length
    ? game.waitlist_entries.map((w) => `${w.player_name}${genderTag(w.gender)}`).join("、")
    : "無";

  container.innerHTML = `
    <div class="gdetail-head">
      <span class="gdetail-date">${info.label}</span>
      <span class="gdetail-when">${info.relative}</span>
    </div>
    <div class="gdetail-meta">${metaParts.join("　・　")}</div>
    <div class="gdetail-count">預計出席 <strong>${expected}</strong> 人</div>
    ${game.locked ? '<div class="gdetail-locked">已過更動期限，這一場無法再變更</div>' : ""}
    ${extraHtml}
    <button type="button" class="gdetail-toggle" data-toggle-roster>出席名單 ▾</button>
    <div class="gdetail-roster-wrap" data-roster-wrap hidden>
      <div class="gdetail-section-label">固定成員（${season.members.length} 人）</div>
      <div class="gdetail-roster">${rosterRows}</div>
      ${
        game.confirmed_drop_ins.length
          ? `<div class="gdetail-section-label">臨打確認</div><div class="gdetail-roster">${dropInRows}</div>`
          : ""
      }
      <div class="gdetail-section-label">候補</div>
      <div class="gdetail-waitlist">${waitlistText}</div>
    </div>
  `;

  // Assigned directly (not addEventListener) so re-rendering this same
  // container never accumulates duplicate handlers — matches
  // renderMonthCalendar's pattern.
  container.onclick = (e) => {
    const toggleRoster = e.target.closest("[data-toggle-roster]");
    if (toggleRoster) {
      const wrap = container.querySelector("[data-roster-wrap]");
      wrap.hidden = !wrap.hidden;
      toggleRoster.textContent = wrap.hidden ? "出席名單 ▾" : "出席名單 ▴";
      return;
    }
    const toggleSub = e.target.closest("[data-toggle-sub]");
    if (toggleSub) {
      const form = container.querySelector(`[data-sub-form="${toggleSub.dataset.toggleSub}"]`);
      if (form) form.hidden = !form.hidden;
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
