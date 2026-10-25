// A season's games and money: who is expected, prices, the game card, the
// calendar, the organizer's to-dos and balance summaries.

/** How many people are expected at a game right now. */
function expectedAttendance(season, game) {
  return season.members.length - game.absences.length + game.confirmed_drop_ins.length;
}

function isGameFull(season, game) {
  return expectedAttendance(season, game) >= season.capacity;
}

/** This game's time and venue: its own when it has them (a night at
 * another court), otherwise the season's. Display only; billing never
 * reads them.
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

/** Everything still waiting for the organizer, most urgent first. Pure, so
 * it is tested without a browser (tests/frontend/todos.test.js).
 */
function buildTodos(season, requests, balances, lineReachable) {
  // Money due up to the season on screen (through_season), not the
  // club-wide balance, which includes seasons booked ahead. season_total
  // is the fallback for an older cached answer.
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
  // Strictly false: undefined (not asked) and null (LINE could not
  // answer) both stay silent.
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

/** Whether the first game has happened. Roster changes before it are free
 * and silent; after it they move money at once, so they ask for
 * confirmation. `games` is sorted by date.
 */
function seasonHasStarted(season) {
  const first = season.games[0];
  return !!first && describeDate(first.date).isPast;
}

/** What the air conditioning adds to one person's share of this game, or
 * nothing when it is off or not charged. Shared so both pages describe a
 * game the same way.
 */
function acPill(season, game) {
  if (!game.air_conditioned || !(Number(season.ac_surcharge) > 0)) return "";
  return `<span class="meta-pill ac">${snowIcon(12)}含冷氣 +$${acExtraEach(season, game)}</span>`;
}

// The server's own prices where it has them, so this matches the ledger.
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

/** The game card at the top of the home screen and inside a game's sheet:
 * date, expected headcount with the male/female split, and a row of small
 * facts. Returns markup so callers can place it above their own content.
 *
 * `opts.metaPills`: extra `<span class="meta-pill">` HTML after the
 * per-game share, such as a change deadline.
 */
function renderGameHero(season, game, opts) {
  const o = opts || {};
  const info = describeDate(game.date);
  const expected = expectedAttendance(season, game);
  const { male, female } = computeGenderCounts(season, game);
  const capacity = season.capacity;
  const full = expected >= capacity;

  // This night's own time and venue when it has them; see gameStartTime.
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
          // A game without a share (a season being set up, an old cached
          // answer) shows no price rather than $undefined.
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

/** The game named in one line, for a sheet opened from a card that already
 * shows the rest.
 */
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

/** A member's ledger up to `season`: everything except seasons that start
 * after it, whose fees are charged when booked but are not due.
 * `season_starts` comes with the ledger, so switching season needs no
 * request.
 */
function ledgerUpTo(ledger, season) {
  const starts = ledger.season_starts || {};
  const own = season ? starts[season.id] || (season.games[0] && season.games[0].date) : null;
  const later = (e) =>
    !!own && e.season_id != null && starts[e.season_id] != null && starts[e.season_id] > own;
  const laterTotal = ledger.entries.filter(later).reduce((t, e) => t + Number(e.amount), 0);
  return { later, total: Number(ledger.balance) - laterTotal };
}

/** A member's money as a summary rather than a list of ledger rows: games
 * played, absences taken, and what that costs or refunds. Counts come
 * from the season, amounts from the ledger, and anything unaccounted for
 * shows as 其他, so the lines always add up to the total.
 *
 * Shared with the all-clubs money page and tested without a browser
 * (tests/frontend/my_ledger.test.js). With no season, every entry falls
 * under 其他季別.
 */
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
  // What earlier seasons left; later seasons are left out (see
  // ledgerUpTo).
  const upTo = ledgerUpTo(ledger, season);
  const earlier = sum((e) => !here(e) && !upTo.later(e));
  add(!season ? "其他季別" : earlier > 0 ? "上季餘額" : "上季未繳", null, earlier);

  // Lines and total must agree, so unknown entry types still appear.
  const rest = upTo.total - accounted;
  if (Math.abs(rest) >= 1) add("其他", null, rest);

  return rows.length ? rows : [{ label: "尚無任何費用", detail: null, amount: 0 }];
}

// The month each calendar is showing, so a repaint keeps it.
const calendarViews = new WeakMap();

/** A month-grid calendar. Opens on the month of the nearest upcoming game,
 * marks each day that has one, and pages between months; tapping a marked
 * day calls onPick(gameId).
 *
 * `opts.stateOf(game)` returns "", "short", "full" or "away", which
 * colours that day's dot (a dot, because a phone's cell is too small for
 * words); `opts.selectedId` draws the ring.
 */
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
    // Always six rows, so paging between months never resizes the card.
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

  /** Only the states this season actually contains. */
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

/** A preview of one game's share, mirroring pricing.share_per_game, so the
 * 開新一季 wizard can show prices as they are typed. The server stays
 * authoritative; the page re-reads its figure after creating the season.
 * Integer inputs divide exactly below 2^53, so ceil agrees with
 * ROUND_CEILING.
 */
function previewSharePerGame(totalVenueCost, totalGames, capacity, acSurcharge, cooledGames) {
  // Divided by capacity, not the members picked: the price of one slot.
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

/** A member's season fee: the sum of the games they are charged for (games
 * can cost different amounts). Mirrors settlement.settle_member.
 */
function seasonFeeFor(season) {
  return season.games
    .filter((g) => g.status !== "cancelled_refunded")
    .reduce((total, g) => total + Number(g.share), 0);
}
