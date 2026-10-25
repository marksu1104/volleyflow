// Text, dates and the small pieces of markup every page uses.

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

/** Escapes a value for an HTML string. Names come from LINE display names
 * and anyone can join, so every free-text value built into innerHTML goes
 * through this; elsewhere use textContent.
 */
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** The first character a reader would see: a whole emoji, not half of one
 * (`"🐱".slice(0, 1)` is a lone surrogate). Grapheme-aware where the
 * browser supports it, otherwise by code point.
 */
function firstGrapheme(text) {
  const value = String(text || "").trim();
  if (!value) return "?";
  try {
    const segments = new Intl.Segmenter("zh-Hant", { granularity: "grapheme" }).segment(value);
    const first = segments[Symbol.iterator]().next().value;
    if (first) return first.segment;
  } catch (e) {
    // Intl.Segmenter missing — fall through.
  }
  return Array.from(value)[0];
}

/** A person's LINE picture, or their initial without one. The picture sits
 * over the initial in the same circle, so the row never shifts, and a
 * picture that fails to load removes itself. Lazy-loaded.
 */
function avatarHtml(name, url, cls = "avatar sm") {
  const letter = escapeHtml(firstGrapheme(name));
  if (!url) return `<span class="${cls}">${letter}</span>`;
  return (
    `<span class="${cls} has-photo">${letter}` +
    `<img src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async" ` +
    `referrerpolicy="no-referrer" onerror="this.remove()"></span>`
  );
}

function dateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** A stored timestamp as read in Taipei: 9/16 20:15. Times are stored as
 * naive UTC, so they are marked as UTC before converting.
 */
function stampTime(iso) {
  const stamped = /(Z|[+-]\d\d:\d\d)$/.test(iso) ? iso : iso + "Z";
  return new Date(stamped).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Weekday label + a relative 今天/明天/已結束 hint for one game date. */
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
    year: date.getFullYear(),
    month: date.getMonth() + 1,
    day: String(date.getDate()),
    weekday: `週${WEEKDAYS[date.getDay()]}`,
    relative,
    isPast: diffDays < 0,
    isToday: diffDays === 0,
  };
}

// Same limit as the server's ClubName.
const CLUB_NAME_MAX = 20;

/** Air conditioning is drawn the same everywhere: blue, with this
 * snowflake.
 */
function snowIcon(size) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v20M4.9 6l14.2 12M4.9 18L19.1 6"/><path d="M9 3.5l3 2 3-2M9 20.5l3-2 3 2"/></svg>`;
}

/** The same snowflake struck through, for "no air conditioning". The slash
 * has a gap cut either side (class "cut", stroked in the background
 * colour) so it does not merge into the arms at small sizes.
 */
function snowOffIcon(size) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g opacity=".7"><path d="M12 2v20M4.9 6l14.2 12M4.9 18L19.1 6"/><path d="M9 3.5l3 2 3-2M9 20.5l3-2 3 2"/></g><path class="cut" d="M2 9.5l20 5" stroke-width="6"/><path d="M2 9.5l20 5" stroke-width="2.4"/></svg>`;
}

/** How many of the people expected at this game are each gender, for the
 * game card's 男/女 pills and capacity bar. Absent members are left out (a
 * substitute is counted as a drop-in); people without a gender count
 * toward the total only.
 */
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

/** The 男/女 tag beside a name. */
function genderTag(g) {
  if (g === "male") return '<span class="gender-tag male">男</span>';
  if (g === "female") return '<span class="gender-tag female">女</span>';
  return "";
}

/** "2026年7月" / "2026年7~9月" / "2025年12月~2026年2月": a season's game dates
 * read as a season.
 */
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

/** The money badge on a club row: 應繳 $N, 餘額 $N, or nothing.
 *
 * A missing balance is not zero: a cached club list from before balances
 * existed has no field, and Number(undefined) would print $NaN. `settled`
 * decides what a cleared balance shows (nothing on 個人資料, 已結清 on 我的帳務).
 * Prints the number, so "470.00" from SQL shows as $470.
 */
function balanceBadgeHtml(balance, opts) {
  const { settled = "" } = opts || {};
  const n = Number(balance);
  if (balance === undefined || balance === null || Number.isNaN(n)) return "";
  if (n === 0) return settled ? `<span class="bal">${escapeHtml(settled)}</span>` : "";
  return `<span class="bal ${n > 0 ? "plus" : "minus"}">${
    n > 0 ? `餘額 $${n}` : `應繳 $${Math.abs(n)}`
  }</span>`;
}
