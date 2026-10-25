// A season's games and money: who is expected, prices, the game card, the
// calendar, the organizer's to-dos and balance summaries.

/** How many people are expected at a game right now. */
function expectedAttendance(season, game) {
  return season.members.length - game.absences.length + game.confirmed_drop_ins.length;
}

function isGameFull(season, game) {
  return expectedAttendance(season, game) >= season.capacity;
}

/** This game's time and venue: its own when it has them, the season's
 * otherwise.
 *
 * A booking really does move — 「有時候會換場地」, 2026-09-17 — so a game
 * carries `location`, `start_time` and `end_time` of its own. Null on
 * any of them means "the same as every other night", which is what
 * every game booked before that date holds, so the season stays the
 * answer unless one night overrides it.
 *
 * Shared rather than written per page for the same reason acPill and
 * isGameFull are: the member's screen and the organizer's show the same
 * game and have to say the same thing about it. Display only — billing
 * never reads any of this. See docs/billing-rules.md.
 */
function gameStartTime(season, game) {
  return (game && game.start_time) || season.game_start_time || null;
}

function gameEndTime(season, game) {
  return (game && game.end_time) || season.game_end_time || null;
}

function gameLocation(season, game) {
  return (game && game.location) || season.location || null;
}

/** Whether the first game has already happened — the line the roster
 * change rules are drawn on: free and
 * silent before it, a confirmation after, because a mid-season change
 * moves money on somebody's ledger right away. `games` is assumed sorted
 * by date, which is how every season detail response already orders it. */
/** Everything still waiting for the organizer, most urgent first.
 *
 * A pure function of what the page already has, so the rule is testable
 * without a browser — see tests/frontend/todos.test.js. */
function buildTodos(season, requests, balances, lineReachable) {
  // Up to the season on screen. `balance` is club-wide and includes
  // seasons booked for *later* (their fees are charged when they are
  // created), which put people on the 待辦 list over money not yet due.
  // Callers fetch /balances?season_id=…, whose through_season is this
  // season plus whatever earlier ones left. Same rule as the 帳務 page's
  // splitLedger; season_total is the fallback for an older cached answer.
  const owing = new Set(
    balances
      .filter((b) => Number(b.through_season ?? b.season_total) < 0)
      .map((b) => b.player_id)
  );
  const upcoming = season.games.filter(
    (g) => g.status === "scheduled" && !describeDate(g.date).isPast
  );
  const list = [];
  if (requests.length) {
    list.push({
      kind: "urgent",
      label: `${requests.length} 位新成員等待核准`,
      sub: "用邀請連結申請加入",
      href: "organizer-members.html",
      action: "核准",
    });
  }
  for (const game of upcoming) {
    const uncovered = game.absences.filter((a) => !a.filled_by);
    if (!uncovered.length) continue;
    list.push({
      kind: "urgent",
      label: `${describeDate(game.date).label} ${uncovered.map((a) => a.player_name).join("、")}請假，尚無代打`,
      sub: "本場人數將不足",
      gameId: game.id,
      action: "指定代打",
    });
  }
  for (const game of upcoming) {
    const playing = expectedAttendance(season, game);
    if (playing >= season.minimum_roster) continue;
    list.push({
      kind: "warn",
      label: `${describeDate(game.date).label} 只有 ${playing} 人`,
      sub: `低於門檻 ${season.minimum_roster} 人`,
      gameId: game.id,
      action: "名單",
    });
  }
  const unpaid = season.members.filter((m) => owing.has(m.id)).length;
  if (unpaid) {
    list.push({
      kind: "calm",
      label: `季費未收 ${unpaid} 人`,
      sub: "季費在開季前收齊",
      href: "organizer-ledger.html",
      action: "收款",
    });
  }
  const todayGame = season.games.find((g) => g.date === dateKey(new Date()));
  const unpaidToday = todayGame
    ? todayGame.confirmed_drop_ins.filter((d) => owing.has(d.player_id)).length
    : 0;
  if (unpaidToday) {
    list.push({
      kind: "calm",
      label: `今天臨打未收 ${unpaidToday} 人`,
      sub: "當天現場收",
      href: "organizer-ledger.html",
      action: "收款",
    });
  }
  // Strictly false, never merely falsy. `undefined` means the caller
  // never asked, `null` means LINE could not answer, and both have to
  // stay silent: telling somebody to add a friend they added months ago
  // reads as the app being broken, which is worse than saying nothing.
  // The fourth argument is optional for the same reason — every other
  // caller passes three and must keep getting no todo from this.
  if (lineReachable === false) {
    list.push({
      kind: "warn",
      label: "尚未加入官方帳號好友",
      sub: "人數不足時無法通知你",
      href: OFFICIAL_ACCOUNT_URL,
      action: "加入",
    });
  }
  return list;
}

function seasonHasStarted(season) {
  const first = season.games[0];
  return !!first && describeDate(first.date).isPast;
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
  return `<span class="meta-pill ac">${snowIcon(12)}含冷氣 +$${acExtraEach(season, game)}</span>`;
}

// The server's own prices where a cooled game has one, so this matches the ledger.
function acExtraEach(season, game) {
  const cooled =
    game && game.air_conditioned && game.share != null
      ? game
      : (season.games || []).find((g) => g.air_conditioned && g.share != null);
  if (cooled && season.share_per_game != null) {
    return Number(cooled.share) - Number(season.share_per_game);
  }
  return Math.ceil(Number(season.ac_surcharge) / season.capacity);
}

function renderGameHero(season, game, opts) {
  const o = opts || {};
  const info = describeDate(game.date);
  const expected = expectedAttendance(season, game);
  const { male, female } = computeGenderCounts(season, game);
  const capacity = season.capacity;
  const full = expected >= capacity;

  // This night's own time and venue when it has them — see
  // gameStartTime. Identical to the season's for every game that does
  // not override, which is nearly all of them.
  const startsAt = gameStartTime(season, game);
  const endsAt = gameEndTime(season, game);
  const where = gameLocation(season, game);
  const whenParts = [];
  if (startsAt && endsAt) {
    whenParts.push(escapeHtml(`${startsAt.slice(0, 5)}–${endsAt.slice(0, 5)}`));
  }
  if (where) whenParts.push(escapeHtml(where));

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

/** The game named in one line, for a sheet opened from a card that
 * already shows the rest. The organizer's 名單 drew the whole hero card a
 * second time above the tabs, pushing the first name half a screen down
 * (proposal 7, 2026-10-17). */
function renderGameHeadLine(season, game, opts) {
  const o = opts || {};
  const info = describeDate(game.date);
  const expected = expectedAttendance(season, game);
  const full = expected >= season.capacity;
  const share =
    game.share === null || game.share === undefined
      ? ""
      : `<span class="meta-pill">每人 <strong>$${game.share}</strong></span>`;
  return `
    <div class="gd-head">
      <span class="gd-head-date">${info.month}/${info.day}<span class="hero-weekday">${info.weekday}</span></span>
      <span class="gd-head-count"><b${full ? ' class="full"' : ""}>${expected}</b> / ${season.capacity} 人</span>
      <span class="gd-head-pills">${share}${(o.metaPills || []).join("")}</span>
    </div>
    ${o.statusHtml || ""}
  `;
}

/** What a member's money looks like right now, not a journal of every
 * entry.
 *
 * The first version listed raw ledger rows newest-first — 季費 −$3055,
 * 收付款 +$3055, 臨打費 −$235 — which is the accountant's view and
 * answers a question nobody asked. What a member wants to know is how
 * many games they signed up for, how many they took leave from, and
 * therefore what they owe or are owed.
 *
 * So the counts come from the season (what actually happened to you) and
 * the amounts come from the ledger (what it cost). Anything on the
 * ledger these lines don't account for shows as 其他 rather than being
 * silently dropped — the lines have to add up to the headline, or the
 * screen is lying about money.
 *
 * Lives here rather than on member.html because the all-clubs money page
 * shows the same breakdown per club. Everything it needs is an argument,
 * so the rule can be tested without a browser — see
 * tests/frontend/my_ledger.test.js.
 *
 * `season` may be null, and the result is then honest but nearly
 * useless: every entry falls into 其他季別, because nothing is known
 * about which games this person played. Callers that have a season
 * should pass it.
 */
/** A member's ledger up to `season`: everything except seasons that
 * start after it. Their fees are charged the moment they are booked, so
 * with next season already set up the whole ledger read as owing it
 * now. `season_starts` comes with the ledger, so switching season needs
 * no second request. Without a season, or for a season the ledger knows
 * nothing about, nothing counts as later. */
function ledgerUpTo(ledger, season) {
  const starts = ledger.season_starts || {};
  const own = season ? starts[season.id] || (season.games[0] && season.games[0].date) : null;
  const later = (e) =>
    !!own && e.season_id != null && starts[e.season_id] != null && starts[e.season_id] > own;
  const laterTotal = ledger.entries.filter(later).reduce((t, e) => t + Number(e.amount), 0);
  return { later, total: Number(ledger.balance) - laterTotal };
}

function summaryRows(ledger, season, name) {
  const here = (e) => !!season && e.season_id === season.id;
  const sum = (pred) =>
    ledger.entries.filter(pred).reduce((t, e) => t + Number(e.amount), 0);

  const rows = [];
  let accounted = 0;
  const add = (label, detail, amount) => {
    if (amount === 0) return;
    rows.push({ label, detail, amount });
    accounted += amount;
  };

  if (season) {
    const absences = season.games.filter((g) =>
      g.absences.some((a) => a.player_name === name)
    );
    const covered = absences.filter((g) =>
      g.absences.some((a) => a.player_name === name && a.covered_by)
    );
    const dropIns = season.games.filter((g) =>
      g.confirmed_drop_ins.some((d) => d.player_name === name)
    );

    add("本季季費", `${season.games.length} 場`,
      sum((e) => here(e) && e.entry_type === "season_fee_charged"));
    add("請假退費", `請假 ${absences.length} 次・${covered.length} 次有人代打`,
      sum((e) => here(e) && e.entry_type === "absence_refund"));
    add("臨打費", `${dropIns.length} 場`,
      sum((e) => here(e) && e.entry_type === "drop_in_fee_charged"));
    add("已收付款", null, sum((e) => here(e) && e.entry_type === "payment"));
  }
  // What earlier seasons left. A season starting later is left out
  // altogether rather than shown here — see ledgerUpTo. With no season
  // on screen there is no "earlier", only other seasons.
  const upTo = ledgerUpTo(ledger, season);
  const earlier = sum((e) => !here(e) && !upTo.later(e));
  add(!season ? "其他季別" : earlier > 0 ? "上季餘額" : "上季未繳", null, earlier);

  // The lines and the headline must never disagree: an entry type this
  // screen doesn't know about still has to appear somewhere.
  const rest = upTo.total - accounted;
  if (Math.abs(rest) >= 1) add("其他", null, rest);

  return rows.length ? rows : [{ label: "尚無任何費用", detail: null, amount: 0 }];
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
// The month each calendar is showing, so a repaint doesn't jump back to the next game's.
const calendarViews = new WeakMap();

function renderMonthCalendar(container, games, onPick, opts) {
  const { stateOf = () => "", selectedId = null, awayLabel = "你請假", viewKey = null } = opts || {};
  const gamesByDate = {};
  for (const g of games) gamesByDate[g.date] = g;

  const remembered = calendarViews.get(container);
  let viewDate;
  if (remembered && remembered.key === viewKey) {
    viewDate = new Date(remembered.year, remembered.month, 1);
  } else {
    const anchor =
      games.find((g) => String(g.id) === String(selectedId)) ||
      games.find((g) => !describeDate(g.date).isPast) ||
      games[games.length - 1];
    viewDate = anchor ? new Date(anchor.date + "T00:00:00") : new Date();
    viewDate.setDate(1);
  }
  const remember = () =>
    calendarViews.set(container, {
      key: viewKey,
      year: viewDate.getFullYear(),
      month: viewDate.getMonth(),
    });
  remember();

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
    // Six rows every month, padded at the end as well as the start. A
    // month needs five or six depending on which weekday it opens on,
    // so without this the card was 288px in September and 330px in
    // January: paging a month resized it and shoved everything under it
    // up or down. Reported as 「切換月份可能會變形」.
    for (let i = firstWeekday + daysInMonth; i < 42; i++) {
      cells += `<div class="mcal-cell empty"></div>`;
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
      remember();
      draw();
      return;
    }
    const cell = e.target.closest(".mcal-cell.has-game");
    if (cell) onPick(Number(cell.dataset.gameId));
  };

  draw();
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
