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
  return season.members.length - game.absent_player_names.length + game.confirmed_drop_ins.length;
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
