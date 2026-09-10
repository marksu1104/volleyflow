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
    year: date.getFullYear(),
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

function isGameFull(season, game) {
  return expectedAttendance(season, game) >= season.capacity;
}

/** What the air conditioning adds to one person's share of this game,
 * or nothing when it isn't running or the season doesn't charge for it.
 *
 * Shared because both pages show the same game: this lived in
 * member.html only, so opening one game as a member and as the
 * organizer reported different facts about it — and the organizer is
 * the person who gets asked "why is tonight more expensive". */
function acPill(season, game) {
  if (!game.air_conditioned || !(Number(season.ac_surcharge) > 0)) return "";
  const each = Math.round(Number(season.ac_surcharge) / season.members.length);
  return `<span class="meta-pill ac">含冷氣 +$${each}</span>`;
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

function renderGameHero(season, game, opts) {
  const o = opts || {};
  const info = describeDate(game.date);
  const expected = expectedAttendance(season, game);
  const { male, female } = computeGenderCounts(season, game);
  const capacity = season.capacity;
  const full = expected >= capacity;

  const whenParts = [];
  if (season.game_start_time && season.game_end_time) {
    whenParts.push(escapeHtml(`${season.game_start_time.slice(0, 5)}–${season.game_end_time.slice(0, 5)}`));
  }
  if (season.location) whenParts.push(escapeHtml(season.location));

  return `
    <div class="hero">
      <div class="hero-head">
        <div>
          <div class="hero-date">${info.month}/${info.day}<span class="hero-weekday">${info.weekday}</span></div>
          <div class="hero-when">${whenParts.length ? whenParts.join(" · ") : `${info.year} 年`}</div>
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
        ${
          // Guarded, because "每人 $undefined" is what this printed when
          // a game arrived without its share — a season still being set
          // up, or an older cached response. Saying nothing about the
          // price is far better than saying something that isn't a price.
          game.share === null || game.share === undefined
            ? ""
            : `<span class="meta-pill">每人 <strong>$${game.share}</strong></span>`
        }
        ${(o.metaPills || []).join("")}
      </div>
      ${o.statusHtml || ""}
      ${o.actionsHtml ? `<div class="hero-actions">${o.actionsHtml}</div>` : ""}
      ${o.moreHtml || ""}
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
  onError,
  clubFilter
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
    // applyClubs kicks off the season fetch, and getJsonSWR may call it
    // twice (cache then network) — its rejection has to be caught here
    // rather than escaping as an unhandled rejection, which is exactly
    // the silent-blank-page failure this whole path is meant to prevent.
    // These reads are club-scoped now, so they carry the caller's token.
    // Callers must resolve identity (initLiffIdentity) before starting
    // the pickers, or authHeader() is empty and the server says 401.
    await getJsonSWR(`${apiBase}/clubs`, (clubs, meta) => {
      // GET /clubs returns every club the caller belongs to, in any
      // role. The management pages pass a filter so a club you are
      // merely a member of never appears in their picker — it used to,
      // which put a whole management surface in front of an ordinary
      // member. The server refuses the writes either way; this stops
      // the app from offering them.
      const visible = clubFilter ? clubs.filter(clubFilter) : clubs;
      applyClubs(visible, meta).catch((e) => {
        console.error("Could not load seasons:", e);
        if (onError) onError(e);
      });
    }, { headers: authHeader() });
  }

  async function applyClubs(clubs, meta) {

  if (clubs.length === 0) {
    clubEl.innerHTML = '<option value="">尚無球隊</option>';
    seasonEl.innerHTML = '<option value="">尚無任何季別</option>';
    // Forget the remembered club, or currentClubId() keeps returning an
    // id that no longer exists — which reads to the rest of the app as
    // "a club is selected, it just has no season", the wrong empty state
    // entirely. Hits anyone whose club was deleted, and anyone testing a
    // wiped database. Only ever on the network's word: a cached copy
    // that happens to be empty is a guess, and forgetting is not undoable.
    if (!meta || meta.fresh) {
      localStorage.removeItem(CLUB_STORAGE_KEY);
      localStorage.removeItem(seasonStorageKey);
    }
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
    await getJsonSWR(
      `${apiBase}/clubs/${clubEl.value}/seasons`,
      (seasons) => applySeasons(seasons),
      { headers: authHeader() }
    );
  }

  function applySeasons(seasons) {
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
/** `opts.stateOf(game)` returns "", "short", "full" or "away", which
 * colours that day's dot; `opts.selectedId` draws the ring. A dot rather
 * than a label on purpose — a calendar cell on a phone is about 40px
 * across, and anything with words in it is unreadable at that size. */
function renderMonthCalendar(container, games, onPick, opts) {
  const { stateOf = () => "", selectedId = null, awayLabel = "你請假" } = opts || {};
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
      if (game && String(game.id) === String(selectedId)) cls.push("on");
      cells += `<div class="${cls.join(" ")}" ${game ? `data-game-id="${game.id}"` : ""}>
        <span>${d}</span>${game ? `<div class="mcal-dot ${stateOf(game)}"></div>` : ""}
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
      ${legendHtml()}
    `;
  }

  /** Only the states this season actually contains. A legend entry for
   * "人數不足" on a season where every game is full is noise, and on a
   * phone every row of it is a row of dates pushed off the screen. */
  function legendHtml() {
    const labels = { "": "有場次", short: "人數不足", full: "已滿", away: awayLabel };
    const present = [];
    for (const g of games) {
      const st = stateOf(g);
      if (!present.includes(st)) present.push(st);
    }
    if (present.length < 2) return ""; // one state everywhere explains itself
    return `<div class="mcal-legend">${present
      .sort((a, b) => Object.keys(labels).indexOf(a) - Object.keys(labels).indexOf(b))
      .map((st) => `<span><i class="mcal-dot ${st}"></i>${labels[st]}</span>`)
      .join("")}</div>`;
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
 *   onAssignSubstitute(absenceId, name, gender, button) — if given, an
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
/** The three groups a game's roster splits into, in tab order. Playing
 * first because that is what most people open the sheet to see. */
const TAB_ORDER = [
  { key: "attending", label: "出席" },
  { key: "absent", label: "請假" },
  { key: "queued", label: "候補" },
];

/** An empty group says so in its own words. A blank panel reads as a
 * page that failed to load — that has been reported twice. */
function emptyPanel(text) {
  return `<div class="empty gd-empty">${text}</div>`;
}

let _requestQueue = Promise.resolve();

/** Sends one change at a time, in the order the taps happened — without
 * making the screen wait for any of it.
 *
 * The first attempt at this locked the buttons until each change and its
 * reload came back. That fixed the interleaving and made the app feel
 * broken: the screen had already updated optimistically, so tapping 請假
 * and then 取消請假 looked like it should work and simply did nothing.
 *
 * Blocking the person was the wrong half to block. What must not
 * interleave is the requests, so they queue here and the screen stays
 * immediate. A change that needs an id the previous request is still
 * fetching resolves it inside its own turn of the queue, by which time
 * the answer has arrived — see recordAbsence/cancelAbsence.
 *
 * A failure doesn't stall the queue: the next change still goes, and the
 * one that failed rolls its own optimistic update back.
 */
function enqueueRequest(work) {
  const run = () => work();
  const next = _requestQueue.then(run, run);
  _requestQueue = next.then(
    () => {},
    () => {}
  );
  return next;
}

/** Runs a change against the screen first and the server second.
 *
 * `mutateLocally` edits the page's own copy so the tap is visible in the
 * same frame; `request` is queued behind any change already in flight;
 * `reconcile` folds the server's ids into what is already drawn, so the
 * refresh afterwards finds nothing to redraw. On failure the snapshot
 * goes back and the reason is shown.
 *
 * Shared, because the organizer's screen had none of this: every action
 * there sat through a write and a reload before anything moved, which is
 * the lag reported as 卡頓.
 */
function optimisticRunner({ getState, setState, repaint }) {
  return async function optimistically(mutateLocally, request, failureMessage, reconcile) {
    const snapshot = JSON.parse(JSON.stringify(getState()));
    mutateLocally(getState());
    repaint();
    try {
      const result = await enqueueRequest(request);
      if (reconcile) {
        reconcile(result, getState());
        // The screen was painted from the placeholder id, so its
        // handlers still hold it even though the data no longer does.
        // Without this repaint the refresh below finds nothing changed
        // and skips redrawing, leaving controls wired to an id that
        // means "still saving".
        repaint();
      }
      return result;
    } catch (e) {
      setState(snapshot);
      repaint();
      toast(failureMessage + e.message);
      return null;
    }
  };
}

/** Takes a drop-in off the local copy — including the absence row that
 * named them.
 *
 * Removing the drop-in alone left "小明 代打" sitting on the absence
 * until the refresh landed, so 取消代打 looked like it had done nothing.
 * The absence goes back to being a gap, which is what it is the instant
 * the substitute is gone. */
function removeDropInLocally(season, dropInId) {
  for (const game of season.games) {
    const going = game.confirmed_drop_ins.find((d) => d.id === dropInId);
    if (!going) continue;
    game.confirmed_drop_ins = game.confirmed_drop_ins.filter((d) => d.id !== dropInId);
    for (const absence of game.absences) {
      if (absence.covered_by === going.player_name) absence.covered_by = null;
      if (absence.filled_by === going.player_name) absence.filled_by = null;
    }
  }
}

/** One panel for choosing a person, wherever a person has to be chosen.
 *
 * Both places that pick somebody — naming a 代打 and adding people to a
 * signup — need the same thing: the handful of people you already know,
 * as taps, with a text field for anyone else. Building it twice let them
 * drift, and the signup version was worse: it repeated the whole list
 * under every row, so signing up three people meant three long lists on
 * one phone screen.
 *
 * `candidates` are `{ id, name, gender, note }`. `id` is what keeps a
 * regular one person instead of a new row each week — a typed name
 * always means somebody new, deliberately, because two people really can
 * be called 小明.
 */
function renderPersonPicker(container, { title, candidates, hint }) {
  const rows = (candidates || [])
    .map(
      (c) => `
      <div class="pick-row" data-person-pick
        data-person-id="${c.id === null || c.id === undefined ? "" : c.id}"
        data-person-name="${escapeHtml(c.name)}"
        data-person-gender="${c.gender || ""}">
        <i class="radio"></i><span class="avatar sm">${escapeHtml((c.name || "?").trim().slice(0, 1))}</span>
        <span class="pk-name">${escapeHtml(c.name)}</span>${genderTag(c.gender)}
        <span class="pk-note">${escapeHtml(c.note || "")}</span>
      </div>`
    )
    .join("");

  container.innerHTML = `
    <div class="picker-backdrop" data-person-picker hidden>
      <div class="picker">
        <div class="picker-head">
          <span>${escapeHtml(title)}</span>
          <button type="button" class="picker-close" data-person-cancel aria-label="關閉">✕</button>
        </div>
        ${hint ? `<p class="hint" style="margin-top:0">${escapeHtml(hint)}</p>` : ""}
        ${
          rows
            ? `<div class="or-line"><span>曾報名過的對象</span></div>
               <div class="sub-pick">${rows}</div>
               <div class="or-line"><span>以上皆非，請直接輸入姓名</span></div>`
            : `<div class="or-line"><span>請輸入姓名</span></div>`
        }
        <div class="picker-manual">
          <input type="text" placeholder="輸入姓名" data-person-name-input>
          <select data-person-gender-input>
            <option value="">性別</option>
            <option value="male">男</option>
            <option value="female">女</option>
          </select>
        </div>
        <button type="button" class="btn btn-primary" data-person-confirm>加入</button>
      </div>
    </div>`;
}

/** Opens the panel built above and resolves with the person chosen, or
 * null if it was closed. A promise rather than a callback because every
 * caller does the same thing with the answer and nothing else until it
 * arrives. */
function choosePerson(container) {
  const backdrop = container.querySelector("[data-person-picker]");
  if (!backdrop) return Promise.resolve(null);
  backdrop.hidden = false;
  return new Promise((resolve) => {
    const done = (value) => {
      backdrop.hidden = true;
      backdrop.onclick = null;
      resolve(value);
    };
    backdrop.onclick = (e) => {
      if (e.target === backdrop || e.target.closest("[data-person-cancel]")) {
        done(null);
        return;
      }
      const row = e.target.closest("[data-person-pick]");
      if (row) {
        done({
          id: row.dataset.personId ? Number(row.dataset.personId) : null,
          name: row.dataset.personName,
          gender: row.dataset.personGender || null,
        });
        return;
      }
      if (e.target.closest("[data-person-confirm]")) {
        const nameEl = backdrop.querySelector("[data-person-name-input]");
        const genderEl = backdrop.querySelector("[data-person-gender-input]");
        const name = nameEl ? nameEl.value.trim() : "";
        if (!name) {
          toast("請輸入姓名");
          if (nameEl && nameEl.focus) nameEl.focus();
          return;
        }
        done({ id: null, name, gender: (genderEl && genderEl.value) || null });
      }
    };
  });
}

/** Shows one of a game sheet's three groups. Used by the tab strip and
 * by anything that needs to send you to a particular group — opening
 * the substitute picker, for instance, whose form lives in 請假. */
function showGameDetailTab(container, key) {
  if (!TAB_ORDER.some((t) => t.key === key)) return;
  if (container.dataset) container.dataset.gdActiveTab = key;
  for (const panel of container.querySelectorAll("[data-gd-panel]")) {
    panel.hidden = panel.dataset.gdPanel !== key;
  }
  for (const tab of container.querySelectorAll("button[data-gd-tab]")) {
    const on = tab.dataset.gdTab === key;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", String(on));
  }
}

function renderGameDetail(container, season, game, options) {
  const opts = options || {};
  const extraHtml = opts.extraHtml || "";
  const clubMembers = opts.clubMembers || [];
  const viewerName = opts.viewerName || "";
  const onAssignSubstitute = opts.onAssignSubstitute;
  const onCancelSubstitute = opts.onCancelSubstitute;
  const canAssignSubstitute = opts.canAssignSubstitute || (() => true);

  /** Marks anyone who has never opened the app: they can't record their
   * own absence or sign themselves up, so somebody has to do it for
   * them, and that's worth seeing on any list of names. */
  function guestTag(person) {
    return person && person.linked === false
      ? '<span class="guest-tag">訪客</span>'
      : "";
  }

  function initial(name) {
    return escapeHtml((name || "?").trim().slice(0, 1));
  }

  /** Who this member may name as their 代打.
   *
   * Not "everybody in the club", which is what it used to offer. Half
   * that list is already on court for this game, and picking one of them
   * signs the same person up twice — reported on 2026-09-10. What is
   * left is the two groups that make sense: whoever is waiting in the
   * queue for this game, and the people this member has brought before,
   * so a regular friend is one tap rather than a retyped name (which is
   * how the same person ends up in the database three times). Typing a
   * new name still works underneath.
   */
  function substituteCandidates(absence) {
    const playing = new Set(
      season.members
        .filter((m) => !game.absences.some((a) => a.player_name === m.name))
        .map((m) => m.name)
        .concat(game.confirmed_drop_ins.map((d) => d.player_name))
    );
    const seen = new Set();
    const out = [];
    for (const person of [...game.waitlist_entries, ...(opts.myGuests || [])]) {
      if (playing.has(person.player_name || person.name)) continue;
      const name = person.player_name || person.name;
      if (name === absence.player_name || seen.has(name)) continue;
      seen.add(name);
      out.push({
        name,
        gender: person.gender,
        note: person.times ? `報名過 ${person.times} 次` : "候補中",
      });
    }
    return out;
  }

  function substituteForm(absence) {
    const candidates = substituteCandidates(absence);
    const covering = game.confirmed_drop_ins.find((d) => d.covering === absence.player_name);
    const pickRows = candidates
      .map(
        (m) => `
          <div class="pick-row" data-pick="${absence.id}" data-pick-name="${escapeHtml(m.name)}" data-pick-gender="${m.gender || ""}">
            <i class="radio"></i><span class="avatar sm">${initial(m.name)}</span>
            <span class="pk-name">${escapeHtml(m.name)}</span>${genderTag(m.gender)}
            <span class="pk-note">${escapeHtml(m.note)}</span>
          </div>`
      )
      .join("");
    const maleSelected = covering && covering.gender === "male" ? " selected" : "";
    const femaleSelected = covering && covering.gender === "female" ? " selected" : "";
    // Its own layer over the sheet, not a panel wedged between two roster
    // rows. Picking a person is a decision of its own: it wants the whole
    // screen, a title saying whose slot is being filled, and one way out.
    return `
      <div class="picker-backdrop" data-sub-form="${absence.id}" hidden>
        <div class="picker">
          <div class="picker-head">
            <span>指定 ${escapeHtml(absence.player_name)} 的代打</span>
            <button type="button" class="picker-close" data-close-sub="${absence.id}" aria-label="關閉">✕</button>
          </div>
          ${
            candidates.length
              ? `<div class="or-line"><span>候補名單，或你曾報名過的對象</span></div>
                 <div class="sub-pick">${pickRows}</div>
                 <div class="or-line"><span>以上皆非，請直接輸入姓名</span></div>`
              : `<div class="or-line"><span>目前無可選對象，請直接輸入姓名</span></div>`
          }
          <div class="picker-manual">
            <input type="text" placeholder="輸入姓名" data-sub-name="${absence.id}" value="${escapeHtml(absence.covered_by || "")}">
            <select data-sub-gender="${absence.id}">
              <option value="">性別</option>
              <option value="male"${maleSelected}>男</option>
              <option value="female"${femaleSelected}>女</option>
            </select>
          </div>
          <button type="button" class="btn btn-primary" data-confirm-sub="${absence.id}">確認指定</button>
        </div>
      </div>
    `;
  }

  // Who is actually playing. Reading that off a list of absences and a
  // list of drop-ins means doing "everyone, minus these, plus those" in
  // your head, and it gets harder the longer the lists are — which is
  // exactly when you most need the answer. So the app does the sum: one
  // numbered list of the people who will be on court, and a separate
  // short list of who won't be and whether anyone covered for them.

  /** Every person on this screen, in every list, is this row.
   *
   * They used not to be. A member's row had four children and everyone
   * else's had five — the extra one being the note — so the flex
   * distribution differed, the name got a different share of the width,
   * and the rows came out visibly different heights next to each other.
   * A min-height papered over the symptom without making the rows the
   * same object.
   *
   * Now the slots are fixed and always present: position, avatar, name,
   * and a right-hand side holding whatever note and controls this row
   * happens to have. An empty side is an empty flex item, which takes no
   * width and adds no height, so a row with nothing to say is the same
   * shape as a row with plenty.
   */
  function rosterRow({ num, person, name, tone, note, controls, guest = true }) {
    // The note (代打 / 臨打 / 缺額 / 候補) goes *inside* the name, as an
    // inline element, and that placement is the whole fix.
    //
    // As a flex child of the row it was blockified, so its vertical
    // padding counted toward the row's height: a row whose note ran long
    // measured 57px against its neighbours' 46px, and the note sat 9px
    // off the controls beside it. Inline, its padding doesn't touch the
    // line box — which is exactly why the 訪客 tag, inline in the same
    // place, never caused any of this. Measured in a real browser, after
    // four theories about the flex layout turned out to be wrong.
    //
    // Only the person's own name is allowed to be shortened, hence the
    // inner att-who: with the truncation on the whole thing, a long name
    // ate the 代打 note beside it and left a blank pill.
    return `
      <div class="att-row${tone ? " " + tone : ""}">
        <span class="att-num">${num}</span>
        <span class="avatar sm">${initial(name)}</span>
        <span class="att-name"><span class="att-who">${escapeHtml(name)}</span>${genderTag(
          person ? person.gender : null
        )}${guest ? guestTag(person) : ""}${note || ""}</span>
        <span class="roster-note">${controls || ""}</span>
      </div>`;
  }

  const absentByName = {};
  for (const a of game.absences) absentByName[a.player_name] = a;
  const canEdit = !!opts.onRecordAbsence;

  const attendingRows = [];
  for (const m of season.members) {
    if (absentByName[m.name]) continue;
    attendingRows.push({
      person: m,
      name: m.name,
      tone: "",
      note: "",
      controls: canEdit
        ? `<button type="button" class="mini-action" data-mark-absent="${escapeHtml(m.name)}">請假</button>`
        : "",
    });
  }
  for (const d of game.confirmed_drop_ins) {
    // 代打 and 臨打 are different things and no longer share a label.
    // 代打 is somebody a member personally arranged to stand in for
    // them; 臨打 signed themselves up and stands in for nobody, even
    // when their fee is what refunds an absence. Showing the second as
    // the first told members that a stranger was "their" substitute.
    attendingRows.push({
      person: d,
      name: d.player_name,
      tone: "dropin",
      note: d.covering
        ? `<span class="att-note sub">代 ${escapeHtml(d.covering)}</span>`
        : '<span class="att-note">臨打</span>',
      // Only offered for signups this caller is entitled to undo — their
      // own, or a guest they brought — unless this is the organizer's
      // screen, which manages everybody's. `canRemoveDropIn` is how the
      // two pages differ; the server checks the same thing again.
      controls:
        opts.onRemoveDropIn && (opts.canRemoveDropIn || (() => true))(d)
          ? `<button type="button" class="mini-action danger" data-remove-drop-in="${d.id}">移除</button>`
          : "",
    });
  }
  const attendingHtml = attendingRows
    .map((row, i) => rosterRow({ ...row, num: i + 1 }))
    .join("");

  const absentRows = [];
  for (const absence of game.absences) {
    const allowed = canAssignSubstitute(absence);
    const covering = game.confirmed_drop_ins.find((d) => d.covering === absence.player_name);
    const offerAssign = !!onAssignSubstitute && allowed;
    const offerCancel =
      !!absence.covered_by && !game.locked && !!onCancelSubstitute && allowed && covering;

    const controls =
      (offerAssign
        ? `<button type="button" class="mini-action" data-toggle-sub="${absence.id}">${
            absence.covered_by ? "編輯代打" : "指定代打"
          }</button>`
        : "") +
      (offerCancel
        ? `<button type="button" class="mini-action danger" data-cancel-sub="${covering.id}">取消代打</button>`
        : "") +
      (opts.onCancelAbsence && !absence.covered_by
        ? `<button type="button" class="mini-action" data-undo-absence="${absence.id}">取消請假</button>`
        : "");

    absentRows.push(
      rosterRow({
        num: "—",
        person: season.members.find((m) => m.name === absence.player_name),
        name: absence.player_name,
        tone: "absent",
        // The 訪客 tag says "can't record their own absence" — which is
        // moot on the list of people who are already absent, and it was
        // the content that pushed this row past the width of a phone.
        guest: false,
        // Three different states, and they used to be two. Somebody you
        // arranged is your 代打; somebody who simply signed up is named
        // too — "who filled my slot" is the first thing asked — but as
        // 已補上, because they were never asked to stand in for you.
        // Either way the share comes back; an empty slot is a gap.
        note: absence.covered_by
          ? `<span class="att-note sub">${escapeHtml(absence.covered_by)} 代打</span>`
          : absence.filled_by
            ? `<span class="att-note">${escapeHtml(absence.filled_by)} 已補上</span>`
            : '<span class="att-note gap">缺額</span>',
        controls,
      }) + (offerAssign ? substituteForm(absence) : "")
    );
  }

  /** Who comes off, so a queued person can come on — shown only when
   * the game is already full.
   *
   * The first version asked for a number in a `prompt()` against a
   * numbered list, which meant reading a dialog, matching a name to an
   * index and typing a digit to move one person. Same rows as every
   * other picker on this screen: tap the name.
   */
  function swapOutPicker(entry) {
    if (!isGameFull(season, game)) return "";
    const swappable = game.confirmed_drop_ins;
    if (!swappable.length) return "";
    return `
      <div class="sub-form" data-swap-form="${entry.id}" hidden>
        <div class="or-line"><span>這一場已滿，選一位換下來</span></div>
        <div class="sub-pick">
          ${swappable
            .map(
              (d) => `
            <div class="pick-row" data-swap-in="${entry.id}" data-swap-out="${d.id}">
              <i class="radio"></i><span class="avatar sm">${initial(d.player_name)}</span>
              <span class="pk-name">${escapeHtml(d.player_name)}</span>${genderTag(d.gender)}
              ${d.covering ? `<span class="att-note sub">代 ${escapeHtml(d.covering)}</span>` : ""}
            </div>`
            )
            .join("")}
        </div>
      </div>`;
  }

  // Same row as everything else — see rosterRow. These were borderless
  // text next to eighteen bordered cards, which made a queue of three
  // read as nothing at all and got reported as "the waitlist
  // disappeared" more than once.
  const waitlistRows = game.waitlist_entries
    .map((w, i) =>
      rosterRow({
        num: i + 1,
        person: w,
        name: w.player_name + (viewerName && w.player_name === viewerName ? "（你）" : ""),
        tone: "queued" + (viewerName && w.player_name === viewerName ? " me" : ""),
        note: '<span class="att-note">候補</span>',
        // 遞補 first: the organizer opens this sheet to put somebody on
        // the court far more often than to strike them off, and the
        // queue's own order is exactly what they are overriding — the
        // person at the front is often the one who can't make it.
        controls:
          (opts.onPromoteFromWaitlist
            ? `<button type="button" class="mini-action" data-promote-waitlist="${w.id}">遞補</button>`
            : "") +
          (opts.onLeaveWaitlist
            ? `<button type="button" class="mini-action danger" data-remove-waitlist="${w.id}">移除</button>`
            : ""),
      }) + (opts.onPromoteFromWaitlist ? swapOutPicker(w) : "")
    )
    .join("");

  // Order matters, and it used to be wrong: every control sat *after*
  // the attendance, absence and waitlist lists. With a full roster
  // that's most of a screen of names before you reach the button you
  // opened the sheet to press, and the organizer's 新增臨打 field was
  // further down still. Nobody found them.
  //
  // So: the hero says which game this is, the controls come next, and
  // the lists are last. Scrolling down only ever reveals more names —
  // nothing is ever buried behind them.
  //
  // The three groups are tabs rather than one stacked column, which is
  // the answer to "I can't tell in one screen who's playing, who's
  // away, who's covering for them and who's queued". Eighteen players
  // is eighteen rows however it's arranged — what can be fixed is that
  // reaching the other three groups meant scrolling past all of them,
  // and that a queue of one was invisible below a full roster. The tab
  // strip carries the counts, so the summary stays on screen and each
  // group is one tap away rather than one scroll.
  //
  // Counts show even at zero: 候補 0 is how anyone finds out the queue
  // exists, and "I never saw that button" is exactly what happened
  // while the section only appeared once somebody was already in it.
  const counts = {
    attending: attendingRows.length,
    absent: absentRows.length,
    queued: game.waitlist_entries.length,
  };
  // Kept on the container, which outlives the innerHTML below. Without
  // it every action that repaints the sheet would throw you back to the
  // first tab — one of the "it jumps around" complaints.
  const asked = container.dataset ? container.dataset.gdActiveTab : null;
  const activeTab = TAB_ORDER.some((t) => t.key === asked) ? asked : "attending";
  if (container.dataset) container.dataset.gdActiveTab = activeTab;

  container.innerHTML = `
    ${renderGameHero(season, game, {
      metaPills: opts.heroMetaPills,
      // Straight into the card, not stacked underneath it: .hero-actions
      // draws its own top rule to separate itself from the facts above,
      // which outside the card is a hairline floating on the page.
      statusHtml: opts.statusHtml,
      actionsHtml: opts.actionsHtml,
    })}
    ${game.locked ? '<div class="gdetail-locked">已過更動期限，這一場無法再變更</div>' : ""}
    ${extraHtml}
    ${
      opts.onAddDropIn
        ? `<div class="add-dropin">
             <input type="text" placeholder="臨打姓名" data-new-dropin>
             <select data-new-dropin-gender>
               <option value="">性別</option><option value="male">男</option><option value="female">女</option>
             </select>
             <button type="button" data-add-dropin>新增臨打</button>
           </div>`
        : ""
    }
    <div class="gd-tabs" role="tablist">
      ${TAB_ORDER.map(
        (t) => `<button type="button" role="tab" class="gd-tab${
          t.key === activeTab ? " active" : ""
        }" data-gd-tab="${t.key}" aria-selected="${t.key === activeTab}">${
          t.label
        } <b>${counts[t.key]}</b></button>`
      ).join("")}
    </div>
    <div class="gd-panel" data-gd-panel="attending"${activeTab === "attending" ? "" : " hidden"}>
      <div class="att-list">${attendingHtml || emptyPanel("這一場本場尚無出席名單")}</div>
    </div>
    <div class="gd-panel" data-gd-panel="absent"${activeTab === "absent" ? "" : " hidden"}>
      ${
        absentRows.length
          ? `<div class="att-list">${absentRows.join("")}</div>`
          : emptyPanel("本場無人請假")
      }
    </div>
    <div class="gd-panel" data-gd-panel="queued"${activeTab === "queued" ? "" : " hidden"}>
      ${
        waitlistRows
          ? `<div class="att-list">${waitlistRows}</div>`
          : emptyPanel("本場無人候補")
      }
    </div>
  `;

  // Assigned directly (not addEventListener) so re-rendering this same
  // container never accumulates duplicate handlers — matches
  // renderMonthCalendar's pattern.
  container.onclick = (e) => {
    // Switching tabs shows a panel that is already built and already in
    // the document — no refetch, no re-render, nothing to flash. The
    // choice is stored on the container so the next repaint keeps it.
    //
    // `button[...]`, not a bare attribute selector: the container itself
    // records the active tab, and it does so in a data attribute of its
    // own. A bare [data-gd-tab] therefore walked all the way up from any
    // button in the sheet and matched the container — so every 請假,
    // 移除, 遞補 and 指定代打 tap was swallowed here as a tab switch and
    // silently did nothing. The stored key is named apart from the tab
    // buttons' as well, so the two can't collide again.
    const tab = e.target.closest("button[data-gd-tab]");
    if (tab) {
      showGameDetailTab(container, tab.dataset.gdTab);
      return;
    }
    const toggleSub = e.target.closest("[data-toggle-sub]");
    if (toggleSub) {
      const form = container.querySelector(`[data-sub-form="${toggleSub.dataset.toggleSub}"]`);
      if (form) form.hidden = !form.hidden;
      return;
    }
    const closeSub = e.target.closest("[data-close-sub]");
    if (closeSub) {
      const form = container.querySelector(`[data-sub-form="${closeSub.dataset.closeSub}"]`);
      if (form) form.hidden = true;
      return;
    }
    // Tapping the dimmed area outside the panel closes it, the way every
    // other sheet in this app does.
    if (e.target.classList && e.target.classList.contains("picker-backdrop")) {
      e.target.hidden = true;
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
      if (!name) {
        toast("請先選一個人，或直接輸入名字");
        return;
      }
      onAssignSubstitute(
        Number(id),
        name,
        (genderSelect && genderSelect.value) || null,
        confirmSub
      );
      return;
    }
    const cancelSub = e.target.closest("[data-cancel-sub]");
    if (cancelSub && onCancelSubstitute) {
      onCancelSubstitute(Number(cancelSub.dataset.cancelSub), cancelSub);
      return;
    }
    const markAbsent = e.target.closest("[data-mark-absent]");
    if (markAbsent && opts.onRecordAbsence) {
      opts.onRecordAbsence(markAbsent.dataset.markAbsent, markAbsent);
      return;
    }
    const undoAbsence = e.target.closest("[data-undo-absence]");
    if (undoAbsence && opts.onCancelAbsence) {
      opts.onCancelAbsence(Number(undoAbsence.dataset.undoAbsence), undoAbsence);
      return;
    }
    const removeDropIn = e.target.closest("[data-remove-drop-in]");
    if (removeDropIn && opts.onRemoveDropIn) {
      opts.onRemoveDropIn(Number(removeDropIn.dataset.removeDropIn), removeDropIn);
      return;
    }
    // Separate hook, not a shared one: a queue place lives in its own
    // table with its own id sequence, and routing it through the
    // drop-in handler cancels whoever happens to hold that number.
    // Choosing who steps out so this queued person can play. One tap on
    // a name, rather than reading a numbered list out of a dialog box.
    const swapOut = e.target.closest("[data-swap-out]");
    if (swapOut && opts.onPromoteFromWaitlist) {
      opts.onPromoteFromWaitlist(
        Number(swapOut.dataset.swapIn),
        swapOut,
        Number(swapOut.dataset.swapOut)
      );
      return;
    }
    const promoteWaitlist = e.target.closest("[data-promote-waitlist]");
    if (promoteWaitlist && opts.onPromoteFromWaitlist) {
      const id = Number(promoteWaitlist.dataset.promoteWaitlist);
      // With room to spare there is nothing to choose, so this goes
      // straight through; on a full game it opens the picker instead.
      const picker = container.querySelector(`[data-swap-form="${id}"]`);
      if (picker) {
        picker.hidden = !picker.hidden;
        return;
      }
      opts.onPromoteFromWaitlist(id, promoteWaitlist, null);
      return;
    }
    const removeWaitlist = e.target.closest("[data-remove-waitlist]");
    if (removeWaitlist && opts.onLeaveWaitlist) {
      opts.onLeaveWaitlist(Number(removeWaitlist.dataset.removeWaitlist), removeWaitlist);
      return;
    }
    const addDropIn = e.target.closest("[data-add-dropin]");
    if (addDropIn && opts.onAddDropIn) {
      const nameInput = container.querySelector("[data-new-dropin]");
      const genderSelect = container.querySelector("[data-new-dropin-gender]");
      const name = nameInput ? nameInput.value.trim() : "";
      // Say why, rather than ignoring the tap. A control that does
      // nothing visible is indistinguishable from a broken one, and
      // "沒反應" is how it gets reported.
      if (!name) {
        toast("請先輸入臨打的名字");
        if (nameInput && nameInput.focus) nameInput.focus();
        return;
      }
      opts.onAddDropIn(name, (genderSelect && genderSelect.value) || null, addDropIn);
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

/** Runs something that has to wait on the network, with the control
 * saying so and refusing further taps until it is done.
 *
 * A spinner rather than swapped-out words: the label is what tells you
 * which button you pressed, and replacing it with "處理中…" takes that
 * away at the exact moment you are waiting to find out. The label
 * dims, a spinner appears beside it, and the button is disabled — so a
 * second tap cannot land, which is what produced duplicate requests and
 * stale-id errors before.
 *
 * Restores the button whatever happens, including on failure, because a
 * control stuck spinning forever is worse than the error it is hiding.
 * Anything the screen can show immediately should be optimistic instead
 * — this is for the rest.
 */
async function whileBusy(el, action) {
  if (!el || !el.classList) return action();
  if (el.classList.contains("is-busy")) return undefined;
  el.classList.add("is-busy");
  if ("disabled" in el) el.disabled = true;
  try {
    return await action();
  } finally {
    el.classList.remove("is-busy");
    if ("disabled" in el) el.disabled = false;
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
/** Whether this page is being served from a development machine rather
 * than the real site. Not a security boundary by itself — this runs in
 * the visitor's browser — but it means the shipped site never even
 * offers to send a dev token. The real locks are on the server (see
 * auth.verify_id_token).
 *
 * Private LAN addresses count, not just localhost. A desktop browser is
 * not a phone: it can't show real Safari rendering, touch targets or the
 * keyboard, and three bugs have reached a phone that every static check
 * passed. Reaching the laptop's server from the phone over wifi is what
 * makes "change it and look at it on the actual device" possible, and
 * that address is 192.168.x.x, never localhost.
 */
function isLocalDev() {
  const h = location.hostname;
  if (["localhost", "127.0.0.1", "[::1]"].includes(h)) return true;
  // The three private IPv4 ranges (RFC 1918) — home and office wifi.
  return (
    /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h) ||
    /^192\.168\.\d{1,3}\.\d{1,3}$/.test(h) ||
    /^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(h)
  );
}

/** Who you're pretending to be, from `?as=<name>` — local only.
 *
 * Nothing in this app is reachable without a verified identity, and on a
 * laptop there is no LIFF to get one from, so every page stopped at
 * "open this in LINE". Testing a member's view meant picking up a phone
 * and using the live club's real data. This is the way in:
 *
 *     http://localhost:5500/member.html?as=蘇懂
 *
 * Remembered for the session, so links inside the app keep the identity
 * without carrying the parameter around. `?as=` with no name clears it.
 */
function devIdentityName() {
  if (!isLocalDev()) return null;
  const asked = new URLSearchParams(location.search).get("as");
  try {
    if (asked !== null) {
      if (asked.trim()) sessionStorage.setItem("vf_dev_as", asked.trim());
      else sessionStorage.removeItem("vf_dev_as");
    }
    return sessionStorage.getItem("vf_dev_as");
  } catch (e) {
    return asked && asked.trim() ? asked.trim() : null;
  }
}

function authHeader() {
  const dev = devIdentityName();
  // encodeURIComponent because an Authorization header has to be ASCII:
  // a browser throws outright on `Bearer dev:蘇懂`. auth.verify_id_token
  // decodes it back.
  if (dev) return { Authorization: `Bearer dev:${encodeURIComponent(dev)}` };
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
 * Returns the identified player ({id, name, avatar_url, gender}), or a
 * falsy value that says which half failed, because the two need
 * different words on screen and only one is worth retrying:
 *   null  — LINE's side. No LIFF, login declined, token rejected.
 *           Nothing to retry; tell them to open it in LINE.
 *   false — our backend didn't answer after three tries. Almost always
 *           a sleeping free-tier instance. Tell them that, and offer
 *           to reload.
 * Callers that don't care can keep testing falsiness and degrade to a
 * read-only view either way.
 */
async function initLiffIdentity(apiBase, liffId) {
  // Skips LINE entirely — see devIdentityName. Goes through the same
  // /players/identify call as a real login, so everything downstream
  // (clubs, roles, the ledger) behaves exactly as it does for a real
  // person; only where the identity came from is different.
  const devName = devIdentityName();
  if (devName) {
    try {
      return await postJson(apiBase, "/players/identify", {
        id_token: `dev:${encodeURIComponent(devName)}`,
        display_name: devName,
      });
    } catch (e) {
      console.warn("dev login failed:", e);
      return false;
    }
  }

  let profile;
  try {
    await liff.init({ liffId });
    if (!liff.isLoggedIn()) {
      liff.login();
      return null; // page reloads after LINE login redirects back
    }
    // Resolved once per LIFF session rather than on every page load:
    // navigating between the four organizer pages is a full document
    // load each time, and this is a whole network round trip before
    // anything else can start. The ID token itself still comes fresh
    // from liff.getIDToken() on every request (see authHeader) — only
    // the "who is this" lookup is reused. Cleared by profile.html when
    // the player renames themselves.
    const cached = sessionStorage.getItem("vf_identity");
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch (e) {
        sessionStorage.removeItem("vf_identity");
      }
    }
    profile = await liff.getProfile();
  } catch (e) {
    // Everything above is LINE's side: no LIFF SDK, a bad liff id, login
    // declined. Nothing we retry will change that, and the caller should
    // say "open this in LINE".
    console.warn("LIFF unavailable:", e);
    return null;
  }

  // Identifying is our own backend, and on a free Render instance the
  // first request of the day wakes it from sleep — 30 seconds, often a
  // 502 on the way. Treating that like a LINE login failure is what put
  // "請用 LINE 開啟" on screen for someone who was perfectly logged in,
  // and it happened constantly. So: retry a cold start, and if it still
  // won't answer, report a *server* failure (false), not a login one.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const identified = await postJson(apiBase, "/players/identify", {
        id_token: liff.getIDToken(),
        display_name: profile.displayName,
        picture_url: profile.pictureUrl,
      });
      try {
        sessionStorage.setItem("vf_identity", JSON.stringify(identified));
      } catch (e) {
        // Private mode — just means we identify again next page.
      }
      return identified;
    } catch (e) {
      // A rejected token is a real answer, not a cold start; retrying it
      // just delays the same 401 three times over.
      if (e.status === 401 || e.status === 403) {
        console.warn("Identity rejected:", e);
        return null;
      }
      console.warn(`identify attempt ${attempt + 1} failed:`, e);
      if (attempt < 2) await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
    }
  }
  return false;
}

async function postJson(apiBase, path, body, method) {
  const res = await fetch(`${apiBase}${path}`, {
    method: method || "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data.detail || res.statusText);
    // Callers that retry need to tell "the server didn't answer" from
    // "the server answered no" — see initLiffIdentity, which must not
    // retry a rejected token three times over.
    error.status = res.status;
    throw error;
  }
  // Anything that writes invalidates every cached read: taking leave
  // changes the season detail, adding a member changes the season list,
  // creating a club changes the club list. Dropping the lot is cheap
  // (a handful of small keys) and can't get the invalidation wrong.
  clearResponseCache();
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
function previewSharePerGame(totalVenueCost, totalGames, capacity, acSurcharge, cooledGames) {
  // capacity, not the number of members picked: the price is what one
  // slot costs, and a season with room for 18 charges 18ths whether or
  // not the eighteenth person has been found yet. Dividing by the
  // roster instead made the quoted price fall every time a name was
  // added in the wizard, and then jump the first time somebody left.
  if (!(totalGames > 0) || !(capacity > 0)) return null;
  const ac = Number(acSurcharge) || 0;
  const cooled = Number(cooledGames) || 0;
  const baseTotal = Number(totalVenueCost) - ac * cooled;
  if (baseTotal < 0) return null;
  const baseEach = baseTotal / totalGames;
  return {
    plain: Math.ceil(baseEach / capacity),
    cooled: Math.ceil((baseEach + ac) / capacity),
  };
}

/** A member's whole-season fee: the sum of the games they're charged
 * for, not a share times a count.
 *
 * Those stopped being the same number once air conditioning made games
 * cost different amounts — a season where half the nights are cooled is
 * not twice the cheap half. Mirrors settlement.settle_member.
 */
function seasonFeeFor(season) {
  return season.games
    .filter((g) => g.status !== "cancelled_refunded")
    .reduce((total, g) => total + Number(g.share), 0);
}

// --- stale-while-revalidate -------------------------------------------
//
// Every page transition re-runs the whole chain (identify -> clubs ->
// seasons -> season detail), and the last three are strictly sequential
// because each needs the previous one's id. That's a visible pause on
// every tap. So: paint from whatever this device saw last time, then
// correct it when the network answers. Only structural data is cached —
// money is always fetched fresh, because a stale balance is worse than a
// slow one.

const CACHE_PREFIX = "vf_cache:";

function readCache(url) {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + url);
    return raw === null ? null : JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function writeCache(url, data) {
  try {
    localStorage.setItem(CACHE_PREFIX + url, JSON.stringify(data));
  } catch (e) {
    // Quota or private mode — caching is an optimisation, never required.
  }
}

/**
 * GETs JSON, calling `onData(data, { fresh })` up to twice: once
 * immediately with the cached copy if there is one (`fresh: false`),
 * then again with the network's copy if it differs (`fresh: true`).
 * Renderers must therefore be idempotent — they all are, since each one
 * rebuilds its container from scratch.
 *
 * The `fresh` flag matters for anything irreversible. A cached copy is a
 * guess about the world; only the network's answer is authoritative, so
 * "there are no clubs, forget the remembered one" must key off `fresh`,
 * not off a stale copy that happened to be empty.
 *
 * If the network fails but a cached copy was served, the failure is
 * swallowed: the page is already showing something usable. With no cache
 * to fall back on, it throws, and the caller shows its error state.
 */
async function getJsonSWR(url, onData, options) {
  const cached = readCache(url);
  const hadCache = cached !== null;
  if (hadCache) onData(cached, { fresh: false });

  try {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(res.statusText);
    const fresh = await res.json();
    const changed = JSON.stringify(fresh) !== JSON.stringify(cached);
    writeCache(url, fresh);
    if (!hadCache || changed) onData(fresh, { fresh: true });
    return fresh;
  } catch (e) {
    if (hadCache) {
      console.warn("Revalidation failed, keeping cached copy:", url, e);
      return cached;
    }
    throw e;
  }
}

/** This club's people, kept for the life of the page. Declared here
 * rather than beside fetchClubMembers so it is initialised before
 * clearResponseCache can reach for it. */
const _clubMemberCache = {};
const _myGuestCache = {};

/** People this viewer has brought to this club before, for the pickers.
 *
 * Retyping a friend's name every week is how one person becomes three
 * rows in the players table, each carrying its own money — so the app
 * offers the ones already brought as a tap. Returns [] on any failure:
 * a picker that can't load is a picker that isn't there, not a broken
 * page, and the text field beside it still works. */
async function fetchMyGuests(apiBase, clubId) {
  if (Object.prototype.hasOwnProperty.call(_myGuestCache, clubId)) {
    return _myGuestCache[clubId];
  }
  try {
    const res = await fetch(`${apiBase}/clubs/${clubId}/my-guests`, {
      headers: authHeader(),
    });
    const guests = res.ok ? await res.json() : [];
    if (res.ok) _myGuestCache[clubId] = guests;
    return guests;
  } catch (e) {
    console.warn("Could not load previous guests:", e);
    return [];
  }
}

/** Drops every cached response. Called after anything that writes, so
 * the next page doesn't paint from a copy we just invalidated. */
function clearResponseCache() {
  // The in-memory club roster goes with them, or adding a guest would
  // leave the substitute picker offering the list from before. Same for
  // the "people I've brought" list, which grows the moment somebody
  // brings a new one.
  for (const key of Object.keys(_clubMemberCache)) delete _clubMemberCache[key];
  for (const key of Object.keys(_myGuestCache)) delete _myGuestCache[key];
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(CACHE_PREFIX)) localStorage.removeItem(key);
    }
  } catch (e) {
    // Nothing to do — a stale cache self-corrects on the next revalidate.
  }
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
      ${
        !action
          ? ""
          : action.href
            ? `<a class="btn btn-primary" href="${action.href}">${escapeHtml(action.label)}</a>`
            : `<button type="button" class="btn btn-primary" onclick="${escapeHtml(action.onclick)}">${escapeHtml(action.label)}</button>`
      }
    </div>
  `;
}

/** What to show when initLiffIdentity came back falsy. Its two failures
 * need different words: one is the reader's to fix by opening the page
 * in LINE, the other is the server being asleep and fixes itself. Every
 * page shows the same thing, so it's written once here. */
function signInFailureHtml(identified) {
  if (identified === false) {
    return emptyStateHtml(
      "連不上伺服器",
      "伺服器可能正在喚醒中，這通常要等 30 秒左右。稍等一下再重新載入就好。",
      { label: "重新載入", onclick: "location.reload()" }
    );
  }
  // On a laptop that advice is impossible to follow — LINE cannot open
  // localhost — and it's what a page with no ?as= showed. Say the thing
  // that actually works here instead.
  if (isLocalDev()) {
    return emptyStateHtml(
      "尚未選擇身分",
      "本機沒有 LINE 可以登入，要在網址後面加上 ?as=名字 才知道身分，例如 ?as=蘇懂。"
    );
  }
  return emptyStateHtml(
    "請用 LINE 開啟",
    "名單和帳務只有球隊成員看得到，所以需要先用 LINE 登入。請從 LINE 裡的連結開啟這一頁。"
  );
}

/** DELETE with the caller's token, throwing the API's own error detail —
 * the mirror of postJson, including clearing the response cache so the
 * next read doesn't paint from a copy of something just deleted. */
async function deleteJson(apiBase, path) {
  const res = await fetch(`${apiBase}${path}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || res.statusText);
  }
  clearResponseCache();
}

/** Everyone in a club (fixed member or not) — the pool the 指定代打
 * picker offers before falling back to typing a name. Public endpoint,
 * no auth needed. Returns [] rather than throwing on failure, since a
 * missing picker list should just fall back to the manual text input,
 * not break the page. */
async function fetchClubMembers(apiBase, clubId) {
  // Held for the life of the page, because every action ends in a
  // reload and each reload was fetching this list again — a whole
  // network round trip, on every tap, for a list that only changes when
  // somebody joins the club. clearResponseCache() drops it, and that
  // runs after every write, so a roster change is still picked up.
  if (Object.prototype.hasOwnProperty.call(_clubMemberCache, clubId)) {
    return _clubMemberCache[clubId];
  }
  try {
    const res = await fetch(`${apiBase}/clubs/${clubId}/members`, {
      headers: authHeader(),
    });
    const members = res.ok ? await res.json() : [];
    if (res.ok) _clubMemberCache[clubId] = members;
    return members;
  } catch (e) {
    console.warn("Could not load club members:", e);
    return [];
  }
}

/** Lets a loader discard its own result when a newer run has started.
 *
 * getJsonSWR calls its callback twice — once from cache, once from the
 * network — and the season picker turns each of those into a fresh
 * `onSeasonChange`, so two loads are routinely in flight at once. They
 * are not guaranteed to finish in order: the older one finishing last
 * paints its stale copy over the newer one's. That is what made a
 * recorded payment flick back to 收款 a second after it had settled,
 * and it can put any screen a whole request behind reality.
 *
 * Usage: take a ticket at the top of the loader, and check it after
 * every await, before touching the DOM or shared state.
 */
function staleGuard() {
  let latest = 0;
  return {
    take: () => ++latest,
    current: (ticket) => ticket === latest,
  };
}

/** A short message that doesn't take the screen away from you.
 *
 * Every failure used to be an alert(), 48 of them, and inside a LIFF
 * webview that is a system modal: it covers the page, it has to be
 * dismissed before anything else can happen, and it looks like the app
 * crashed rather than like a form needing another go. This says the same
 * thing over the page and gets out of the way.
 *
 * Kept for messages, not decisions — confirm() still blocks, because
 * "are you sure you want to remove this member" genuinely must be
 * answered before anything proceeds.
 */
function toast(message, kind) {
  let host = document.getElementById("vf-toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "vf-toasts";
    host.className = "toasts";
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = message;
  host.appendChild(el);
  // Long enough to read a failure reason, short enough not to stack up.
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }, kind === "good" ? 1800 : 3600);
}

/** Reloads the page once when its markup is older than its scripts.
 *
 * GitHub Pages serves HTML with `Cache-Control: max-age=600`, and LINE's
 * in-app browser holds it a good deal longer than that. Only shared.js
 * and shared.css carry a `?v=` cache-buster, so "cached HTML, fresh
 * script" is a normal state — and every change that lives in the markup
 * rather than the script (a new form field, a new section) is simply
 * invisible until the page happens to expire. That has now been reported
 * as a bug more than once.
 *
 * The deploy stamps the same build id into a <meta> and onto this
 * script's own URL. If they disagree, the HTML is stale: reload it with
 * a cache-busting parameter, exactly once — the `vf_reloaded` guard
 * stops a mismatch we can't fix (a missing meta, a failed deploy) from
 * becoming a refresh loop.
 */
function assertFreshBuild() {
  const meta = document.querySelector('meta[name="vf-build"]');
  const script = document.querySelector('script[src*="shared.js"]');
  if (!meta || !script) return; // local file, or before the deploy stamps it
  const wanted = (script.getAttribute("src").split("?v=")[1] || "").trim();
  const have = (meta.getAttribute("content") || "").trim();
  if (!wanted || !have || wanted === have) return;

  try {
    if (sessionStorage.getItem("vf_reloaded") === wanted) return;
    sessionStorage.setItem("vf_reloaded", wanted);
  } catch (e) {
    return; // private mode: better a stale page than a reload loop
  }
  const url = new URL(location.href);
  url.searchParams.set("v", wanted);
  location.replace(url.toString());
}

assertFreshBuild();

/** Which API this page talks to.
 *
 * Every page had the production URL hardcoded, so a page served from a
 * laptop still called the live server — which is worse than it sounds in
 * both directions: local sign-in is off there so nothing worked, and
 * anything that *did* work was writing to the real club's data.
 *
 * The API is taken from whatever host served the page, not written down
 * as "localhost": on a phone opening 192.168.1.101:5500, localhost is
 * the *phone*, so a fixed name would send every request into a device
 * that isn't running anything. Same host, port 8000.
 */
function apiBase() {
  if (!isLocalDev()) return "https://volleyflow.onrender.com";
  return `http://${location.hostname}:8000`;
}
