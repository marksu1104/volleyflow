// Shared helpers for member.html and organizer.html — date formatting,
// the season picker, and the month calendar. One copy so the two pages
// can't quietly drift apart on how they show the same data.

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

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

/**
 * Fills a <select> with every season, labeled by its real dates instead
 * of a bare id. Restores the last choice from localStorage and calls
 * onChange (also once immediately) whenever the selection changes.
 */
async function initSeasonPicker(apiBase, selectEl, storageKey, onChange) {
  const res = await fetch(`${apiBase}/seasons`);
  const seasons = await res.json();

  if (seasons.length === 0) {
    selectEl.innerHTML = '<option value="">尚無任何季別</option>';
    return;
  }

  selectEl.innerHTML = seasons
    .map((s) => {
      const settledTag = s.settled ? " · 已結算" : "";
      return `<option value="${s.id}">${formatSeasonLabel(s)}（${s.total_games} 場・${s.member_count} 人）${settledTag}</option>`;
    })
    .join("");

  const remembered = localStorage.getItem(storageKey);
  if (remembered && seasons.some((s) => String(s.id) === remembered)) {
    selectEl.value = remembered;
  }

  selectEl.addEventListener("change", () => {
    localStorage.setItem(storageKey, selectEl.value);
    onChange(selectEl.value);
  });

  onChange(selectEl.value);
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
 *     absences get a "設定代打" control that calls this.
 *   canAssignSubstitute(absence) — gates which absences get that
 *     control (default: all of them — an organizer can arrange anyone's
 *     substitute; member.html restricts this to the viewer's own row).
 */
function renderGameDetail(container, season, game, options) {
  const opts = options || {};
  const extraHtml = opts.extraHtml || "";
  const onAssignSubstitute = opts.onAssignSubstitute;
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
      const note = absence.covered_by ? `已由 ${absence.covered_by} 遞補` : "尚無人遞補";
      const offerSub = !absence.covered_by && !game.locked && onAssignSubstitute && canAssignSubstitute(absence);
      const subControl = offerSub
        ? `<button class="mini-action" data-toggle-sub="${absence.id}">設定代打</button>`
        : "";
      const subForm = offerSub
        ? `<div class="sub-form" data-sub-form="${absence.id}" hidden>
             <input type="text" placeholder="代打姓名" data-sub-name="${absence.id}">
             <select data-sub-gender="${absence.id}">
               <option value="">性別</option>
               <option value="male">男</option>
               <option value="female">女</option>
             </select>
             <button data-confirm-sub="${absence.id}">確認</button>
           </div>`
        : "";
      return `
        <div class="roster-row absent">
          <span>${m.name}${genderTag(m.gender)}</span>
          <span class="roster-note">${note}${subControl}</span>
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
async function postJson(apiBase, path, body, method) {
  const res = await fetch(`${apiBase}${path}`, {
    method: method || "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
