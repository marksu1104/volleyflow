// Shared helpers for member.html and organizer.html — date formatting,
// the season picker, and the calendar strip. One copy so the two pages
// can't quietly drift apart on how they show the same data.

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

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
      return `<option value="${s.id}">${s.first_game_date} ~ ${s.last_game_date}（${s.total_games} 場・${s.member_count} 人）${settledTag}</option>`;
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

/** A horizontal strip of date chips — one per game, colour-coded by
 * what's happening that day, click to jump to its card. */
function renderCalStrip(container, games, onPick) {
  container.innerHTML = "";
  for (const game of games) {
    const info = describeDate(game.date);
    const chip = document.createElement("div");
    chip.className = "cal-chip" + (info.isToday ? " today" : "") + (info.isPast ? " past" : "");

    let dotClass = "";
    if (game.absent_player_names.length > 0) dotClass = "has-absence";
    else if (game.confirmed_drop_ins.length > 0) dotClass = "has-dropins";
    else if (game.waitlist_entries.length > 0) dotClass = "has-waitlist";

    chip.innerHTML = `
      <div class="wd">${info.weekday}</div>
      <div class="day">${info.day}</div>
      <div class="dot ${dotClass}"></div>
    `;
    chip.onclick = () => onPick(game.id);
    container.appendChild(chip);
  }
}
