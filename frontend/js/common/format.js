// Text, dates and the small pieces of markup every page uses.

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

/** The first character a reader would see — one whole emoji, not half
 * of one. `"🐱".slice(0, 1)` is a lone surrogate, which every circle
 * showing a LINE name that starts with an emoji rendered as a broken
 * box (2026-10-07). Grapheme-aware where the browser can be; otherwise
 * by code point, which still keeps a single emoji in one piece. */
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

/** Somebody's picture where they have a LINE one, their initial where
 * they don't. The picture sits over the initial in the same circle, so
 * the row never shifts when it loads, and a picture that fails removes
 * itself and leaves the initial showing. Lazy, because a long roster is
 * mostly off screen. */
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

/** Weekday label + a relative "今天/明天/已結束" hint for one game date. */
/** A stored timestamp as a person here reads it: 9/16 20:15.
 *
 * Times are stored naive UTC (routes/_people._now), so they have to be
 * stamped as UTC before being shown in Taipei — without that the
 * evening's payments read as the small hours of the same day.
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

/** Air conditioning is drawn the same everywhere it appears — blue, with
 * this snowflake (2026-10-21; it was orange on some screens and blue on
 * others). */
function snowIcon(size) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v20M4.9 6l14.2 12M4.9 18L19.1 6"/><path d="M9 3.5l3 2 3-2M9 20.5l3-2 3 2"/></svg>`;
}

/** The same snowflake struck through, for "no air conditioning". The
 * slash runs between two of the flake's arms and has a gap cut either
 * side of it (class "cut", stroked in the colour behind), or at this
 * size it merges into the arms and reads as a second snowflake. */
function snowOffIcon(size) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g opacity=".7"><path d="M12 2v20M4.9 6l14.2 12M4.9 18L19.1 6"/><path d="M9 3.5l3 2 3-2M9 20.5l3-2 3 2"/></g><path class="cut" d="M2 9.5l20 5" stroke-width="6"/><path d="M2 9.5l20 5" stroke-width="2.4"/></svg>`;
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
/** The 男/女 tag beside a name. One implementation because there were
 * three — this one, plus genderTagFor on the money screen and
 * genderLabel on the roster screen, all identical and all needing the
 * same edit whenever the tag changes. */
function genderTag(g) {
  if (g === "male") return '<span class="gender-tag male">男</span>';
  if (g === "female") return '<span class="gender-tag female">女</span>';
  return "";
}

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

/** The third state: the request actually failed. Always offers a way
 * out (retry) rather than leaving a dead page. */
/** What one section shows while its own request is still out.
 *
 * Every block on these screens is filled by a request of its own, and
 * showPageLoading only covers whichever one answers first — so a block
 * further back used to be a blank space that filled itself seconds
 * later, pushing everything below it down. A section that cannot answer
 * yet says so instead. Asked for on 2026-09-18: 「如果有東西不能馬上顯
 * 現，至少你要把轉圈做出來」.
 *
 * Deliberately not showPageLoading: that one centres itself in 44px of
 * padding and escalates its wording after four seconds, which is right
 * for a whole screen and far too much inside a block that already has a
 * heading above it.
 */
/** The money badge on a club row: 應繳 $N, 餘額 $N, or nothing at all.
 *
 * Shared because the guard is subtle and was hand-copied onto two pages.
 * Missing is not zero, and the distinction is load-bearing: getJsonSWR
 * paints a *cached* club list before the network answers, and a copy
 * stored before balances shipped carries no such field. Number(undefined)
 * is NaN, so the obvious version renders 應繳 $NaN on a money row for
 * everyone's first load after a deploy.
 *
 * `settled` is the one thing the two callers genuinely disagree about,
 * so it is a parameter rather than a second copy of everything else:
 * 個人資料 shows nothing for a cleared balance, because a club row there
 * is a fact about the reader; 我的帳務 says 已結清, because on the money
 * page "you are square" is an answer worth giving.
 *
 * Prints the *number*, never the string it arrived as. A balance summed
 * in SQL can come back as "470.00" where the Python path gives "470" —
 * the same money, but $470.00 on a row is wrong. The two pages used to
 * disagree about exactly this.
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
