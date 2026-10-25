// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
// The club is chosen from the top bar, as on the member page.
mountClubChip(document.getElementById("club-chip"), document.getElementById("club-picker"));
const LIFF_ID = "2011156233-6CouG6VI";
let currentSeason = null;

const identityReady = (async function initIdentity() {
  const identified = await initLiffIdentity(API_BASE, LIFF_ID);
  if (identified) {
    paintSelfAvatar(document.getElementById("avatar-btn"), identified);
  }
  return identified;
})();
document.getElementById("avatar-btn").addEventListener("click", () => {
  location.href = "profile.html";
});

document.getElementById("ledger-tabs").addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (!tab) return;
  showTab(tab.dataset.tab);
});

function showTab(name) {
  document.querySelectorAll("#ledger-tabs [data-tab]").forEach((t) => {
    t.classList.toggle("on", t.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((el) => {
    el.hidden = el.id !== `tab-${name}`;
  });
}
if (location.hash === "#settle") showTab("closing");

showPageLoading(document.getElementById("no-season"));
document.getElementById("ledger-tabs").hidden = true;

// Every read carries the caller's token, so nothing is fetched before
// identity resolves.
identityReady.then((identified) => {
  if (!identified) {
    stopLoadingEscalation();
    document.getElementById("no-season").innerHTML = signInFailureHtml(identified);
    return;
  }
  initClubAndSeasonPickers(
    API_BASE,
    document.getElementById("club-picker"),
    document.getElementById("season-picker"),
    "vf_org_season",
    (id) => {
      stopLoadingEscalation();
      const hasClub = !!currentClubId();
      if (id) {
        // The tabs stay hidden until loadLedger has the numbers.
        document.getElementById("season-picker-field").hidden = false;
        document.getElementById("season-picker").hidden = false;
        loadLedger(id);
      } else {
        setHasSeason(false);
      }
    },
    (e) => showPageError(document.getElementById("no-season"), e.message),
    // Only clubs this person organizes; see managesClub.
    managesClub
  );
});

/** Without a season there is nothing to collect or settle, so the tabs are
 * hidden.
 */
function setHasSeason(hasSeason) {
  // #tab-fee starts hidden in the markup too, so it does not show under
  // the spinner.
  document.getElementById("ledger-tabs").hidden = !hasSeason;
  document.getElementById("season-picker-field").hidden = !hasSeason;
  document.getElementById("season-picker").hidden = !hasSeason;
  document.querySelectorAll(".tab").forEach((el) => {
    if (!hasSeason) el.hidden = true;
  });
  document.getElementById("no-season").innerHTML = hasSeason
    ? ""
    : currentClubId()
      ? emptyStateHtml(
          "這個球隊尚未開季",
          "季費、當天臨打和季末結算都是跟著一季走的。建立第一季之後，每個人的應收金額就會出現在這裡。",
          { label: "建立第一季", href: "organizer-settings.html" }
        )
      : noClubYetHtml();
  // Opens on 當天臨打; a #settle link wins.
  if (hasSeason) {
    const today = dateKey(new Date());
    // Until the last game has been played, 季末結算 is shown quieter than the
    // two weekly tabs, but still tappable.
    const games = currentSeason.games;
    const last = games[games.length - 1];
    const stillRunning =
      currentSeason.settled_at === null && !!last && last.date >= today;
    const closingTab = document.querySelector('#ledger-tabs [data-tab="closing"]');
    if (closingTab) closingTab.classList.toggle("quiet", stillRunning);
    // 當天臨打 first: what this page is opened for most days.
    showTab(location.hash === "#settle" ? "closing" : "dropin");
  }
}

const ledgerLoad = staleGuard();

/** Changing club or season: the old figures come down at once and 載入中
 * shows until the new season paints. Taking a ticket drops any paint
 * still due for the old season.
 */
function showSwitching() {
  ledgerLoad.take();
  document.getElementById("ledger-tabs").hidden = true;
  document.querySelectorAll(".tab").forEach((el) => {
    el.hidden = true;
  });
  showPageLoading(document.getElementById("no-season"));
}
// Alongside the pickers' own handler, which fetches; this only clears the
// screen.
document.getElementById("club-picker").addEventListener("change", showSwitching);
document.getElementById("season-picker").addEventListener("change", showSwitching);

async function loadLedger(seasonId) {
  // Two loads usually overlap; see staleGuard.
  const ticket = ledgerLoad.take();
  // What the screen knew when this read left; see localRevision.
  const askedAt = localRevision();
  const errorEl = document.getElementById("error");
  errorEl.textContent = "";
  try {
    // Cached copy first, network second; a 404 reports through the catch.
    await getJsonSWR(`${API_BASE}/seasons/${seasonId}`, (season) => {
      paint(season).catch((e) => {
        if (!ledgerLoad.current(ticket)) return;
        errorEl.textContent = "連不上伺服器：" + e.message;
      });
    }, { headers: authHeader() });
  } catch (e) {
    if (!ledgerLoad.current(ticket)) return;
    document.getElementById("no-season").innerHTML = "";
    errorEl.textContent = "連不上伺服器：" + e.message;
  }

  async function paint(season) {
    if (!ledgerLoad.current(ticket)) return;
    // Older than the screen: painting it would undo a tap.
    if (currentSeason && currentSeason.id === season.id && localRevision() !== askedAt) return;
    currentSeason = season;
    // Started alongside the balances; the two are independent.
    const settlementReady = fetchSettlement(season);
    await loadBalances(currentSeason);
    if (!ledgerLoad.current(ticket)) return;
    // The page goes up without waiting for the settlement preview, which
    // only 季末結算 uses.
    await Promise.all([renderFeeTab(), renderDropInTab()]);
    if (!ledgerLoad.current(ticket)) return;
    setHasSeason(true);
    settlementPreview = await settlementReady;
    if (!ledgerLoad.current(ticket)) return;
    await renderClosingTab();
  }
}

// Shared helpers

/** Everyone's balance in one request, kept so switching tabs does not
 * re-ask.
 */
let balances = {};

let settlementPreview = { members: [] };

/** What settling now would refund. */
async function fetchSettlement(season) {
  if (season.settled_at !== null) return { members: [] };
  try {
    const res = await fetch(`${API_BASE}/seasons/${season.id}/settlement`, {
      headers: authHeader(),
    });
    return res.ok ? await res.json() : { members: [] };
  } catch (e) {
    return { members: [] };
  }
}

/** `apply` says whether this answer is still wanted, decided before the
 * map is replaced rather than after.
 */
async function loadBalances(season, apply) {
  const use = apply || applyBalances;
  try {
    await getJsonSWR(
      `${API_BASE}/clubs/${currentClubId()}/balances?season_id=${season.id}`,
      (rows) => use(rows),
      { headers: authHeader() }
    );
  } catch (e) {
    use([]);
  }
}

/** Sets the rows every figure comes from; separate from the fetch so tests
 * can set them directly.
 */
function applyBalances(rows) {
  balances = {};
  for (const row of rows) balances[row.player_id] = row;
}

/** Re-reads balances after a payment without undoing it: queued with the
 * writes (coalescedRefresh), dropped if older than the latest local
 * change, and repainted only on a real difference.
 */
const refreshBalancesQuietly = coalescedRefresh(async () => {
  if (!currentSeason) return;
  const askedAt = localRevision();
  const optimistic = JSON.stringify(balances);
  const ticket = ledgerLoad.take();
  let changed = false;
  await loadBalances(currentSeason, (rows) => {
    if (!ledgerLoad.current(ticket) || localRevision() !== askedAt) return;
    applyBalances(rows);
    changed = JSON.stringify(balances) !== optimistic;
  });
  if (changed) renderTabsFromBalances();
});

/** The zero row for someone with no ledger entries yet. */
function balanceOf(playerId) {
  return (
    balances[playerId] || {
      player_id: playerId,
      balance: "0",
      season_total: "0",
      through_season: "0",
      season_fee_charged: "0",
      brought_by: null,
    }
  );
}

/** Which part of a player's club ledger a row is about. Fees are charged
 * when a season is created, so the ledger can hold a season not yet
 * started.
 *
 * "through" (季費 and 季末結算): this season and earlier ones (the server's
 * `through_season`), so last season's kept balance counts and a later
 * season never does. "season" (當天臨打): this season only.
 */
function splitLedger(row, scope = "through") {
  const balance = Number(row.balance);
  const seasonTotal = Number(row.season_total);
  // An old cached answer without through_season has only the whole
  // ledger.
  const through = row.through_season == null ? balance : Number(row.through_season);
  return {
    scope,
    seasonTotal,
    // Earlier seasons only: what 上季餘額 / 上季未繳 mean.
    earlierTotal: through - seasonTotal,
    // Every other season, earlier or later: 當天臨打's context line.
    otherTotal: balance - seasonTotal,
    balance,
    due: scope === "season" ? seasonTotal : through,
  };
}

/** One drop-in night, for 當天臨打: that night's fee less what was taken for
 * it. A player who owes nothing for the season has every night settled.
 */
function nightSplit(row, gameId) {
  const seasonTotal = Number(row.season_total);
  const nights = row.nights;
  const night = nights ? nights.find((n) => n.game_id === gameId) : null;
  const base = { scope: "night", gameId, seasonTotal, seasonOwed: Math.max(0, -seasonTotal) };
  // No figure rather than a wrong one, when the night is not in the
  // answer yet.
  if (!night) return { ...base, due: 0, loading: true };
  const owed = seasonTotal >= 0 ? 0 : Math.max(0, Number(night.charged) - Number(night.paid));
  return { ...base, due: owed === 0 ? 0 : -owed };
}

/** A player's LINE picture, from whichever list on this season has them.
 */
function avatarUrlOf(playerId) {
  const season = currentSeason;
  if (!season) return null;
  const member = season.members.find((m) => m.id === playerId);
  if (member) return member.avatar_url || null;
  for (const game of season.games || []) {
    const d = (game.confirmed_drop_ins || []).find((x) => x.player_id === playerId);
    if (d) return d.avatar_url || null;
  }
  return null;
}

function moneyRowHtml(playerId, name, gender, split, extraBreakLabel, extraBreakValue) {
  // The figure this row is about (see splitLedger); every number and
  // button reads from it.
  const due = split.due;
  const netCls = due === 0 ? "clear" : due > 0 ? "owed" : "owe";
  const netText = due === 0 ? "已結清" : due > 0 ? `應退 $${due}` : `應收 $${Math.abs(due)}`;
  const breakParts = [];
  if (extraBreakLabel) breakParts.push(`<span>${extraBreakLabel} <b>$${extraBreakValue}</b></span>`);
  if (split.scope === "night") {
    // Mention the rest of the season too, but take only this night here.
    if (split.seasonOwed > Math.abs(Math.min(due, 0))) {
      breakParts.push(`<span>本季共應收 <b>$${split.seasonOwed}</b></span>`);
    }
  } else if (split.scope === "season") {
    // Other seasons, possibly later ones, so not 上季.
    if (split.otherTotal !== 0) {
      breakParts.push(`<span>其他季別 <b>${split.otherTotal > 0 ? "+" : "−"}$${Math.abs(split.otherTotal)}</b></span>`);
    }
  } else if (split.earlierTotal !== 0) {
    // Beside 本季季費 the credit comes off it; alone it is what earlier
    // seasons left.
    const credit = extraBreakLabel ? "上季餘額扣除" : "上季餘額";
    breakParts.push(
      `<span>${split.earlierTotal > 0 ? credit : "上季未繳"} <b>$${Math.abs(split.earlierTotal)}</b></span>`
    );
  }

  let actionHtml = "";
  // Only a night's row carries a game; see nightSplit.
  const night = split.gameId == null ? "null" : split.gameId;
  if (split.loading) {
    actionHtml = '<div class="m-done">載入中…</div>';
  } else if (due < 0) {
    const owe = Math.abs(due);
    actionHtml = `
      <div class="m-act">
        <button type="button" class="paid" onclick="recordFullPayment(this, ${playerId}, ${owe}, ${night})">已收</button>
        <button type="button" class="later" onclick="showPartialInput(this)">部分收款</button>
        <input type="number" placeholder="金額">
        <button type="button" class="confirm-partial" onclick="recordPartialPayment(this, ${playerId}, ${night})">確認</button>
      </div>
    `;
  } else if (due > 0) {
    actionHtml = `
      <div class="m-keep">
        <span>保留於餘額</span>
        <button type="button" class="mini-action" onclick="recordCashRefund(${playerId}, ${due})">退款</button>
      </div>
    `;
  } else {
    actionHtml = '<div class="m-done">✓ 已結清</div>';
  }

  // A guest's fee is on their own ledger, but the member who brought them
  // hands over the cash, so name them.
  const brought = balanceOf(playerId).brought_by;
  const broughtTag = brought
    ? `<span class="m-brought">${escapeHtml(brought)} 報名</span>`
    : "";

  return `
    <div class="m-row">
      <div class="m-top">
        ${avatarHtml(name, avatarUrlOf(playerId))}
        <span class="m-name">${escapeHtml(name)}${genderTag(gender)}${broughtTag}</span>
        <span class="m-net ${netCls}">${netText}</span>
        <button type="button" class="mini-action" onclick="openHistory(${playerId})">明細</button>
      </div>
      ${breakParts.length ? `<div class="m-break">${breakParts.join("")}</div>` : ""}
      ${actionHtml}
    </div>
  `;
}


// Scoped to the clicked row: one player can have rows in several tabs.
function showPartialInput(btn) {
  btn.closest(".m-act").classList.add("partial");
}

async function recordFullPayment(btn, playerId, amount, gameId = null) {
  await submitPayment(playerId, amount, gameId);
}

async function recordPartialPayment(btn, playerId, gameId = null) {
  const input = btn.closest(".m-act").querySelector("input");
  const amount = input.value.trim();
  if (!amount) {
    toast("請先填金額");
    input.focus();
    return;
  }
  if (!(Number(amount) > 0)) {
    toast("金額要大於 0");
    input.focus();
    return;
  }
  await submitPayment(playerId, amount, gameId);
}

// Undoing adds an opposite entry; the original stays, marked 已復原.
async function undoEntry(entryId) {
  try {
    await enqueueRequest(() => postJson(API_BASE, `/ledger-entries/${entryId}/reverse`, {}));
    bumpLocalRevision();
    refreshBalancesQuietly();
    toast("已復原", "good");
    if (!document.getElementById("history-backdrop").hidden) openHistory(historyPlayerId);
  } catch (e) {
    toast("復原失敗：" + e.message);
  }
}

let historyPlayerId = null;
const ENTRY_LABELS = {
  season_fee_charged: "季費",
  drop_in_fee_charged: "臨打費",
  absence_refund: "請假退款",
  carried_over: "結轉",
};

async function openHistory(playerId) {
  historyPlayerId = playerId;
  const list = document.getElementById("history-list");
  list.innerHTML = '<div class="empty">載入中…</div>';
  document.getElementById("history-backdrop").hidden = false;
  try {
    const res = await fetch(`${API_BASE}/clubs/${currentClubId()}/players/${playerId}/ledger`, {
      headers: authHeader(),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const ledger = await res.json();
    if (historyPlayerId !== playerId) return;
    document.getElementById("history-title").textContent = `${ledger.player_name} 的明細`;
    list.innerHTML = historyHtml(ledger.entries);
  } catch (e) {
    list.innerHTML = `<div class="empty">讀取失敗：${escapeHtml(e.message)}</div>`;
  }
}

function closeHistory() {
  document.getElementById("history-backdrop").hidden = true;
}

function historyHtml(entries) {
  if (!entries.length) return '<div class="empty">還沒有任何紀錄</div>';
  const undone = new Set(entries.filter((e) => e.reverses_entry_id).map((e) => e.reverses_entry_id));
  return entries
    .slice()
    .reverse()
    .map((e) => {
      const amount = Number(e.amount);
      const label = e.reverses_entry_id
        ? "復原"
        : e.entry_type === "payment"
          ? amount > 0 ? "收款" : "退款"
          : ENTRY_LABELS[e.entry_type] || e.entry_type;
      const canUndo = e.entry_type === "payment" && !e.reverses_entry_id && !undone.has(e.id);
      const when = stampTime(e.recorded_at);
      return `
        <div class="set-row" data-entry="${e.id}">
          <span class="set-label">${label}${undone.has(e.id) ? " · 已復原" : ""}<span class="set-sub">${when}</span></span>
          <span>${amount > 0 ? "+" : "−"}$${Math.abs(amount)}</span>
          ${canUndo ? `<button type="button" class="mini-action" onclick="undoEntry(${e.id})">復原</button>` : ""}
        </div>`;
    })
    .join("");
}

document.getElementById("history-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "history-backdrop") closeHistory();
});

async function recordCashRefund(playerId, amount) {
  if (!confirm(`確定要記錄退款 $${amount} 嗎？`)) return;
  await submitPayment(playerId, -amount);
}

/** Records money and shows it at once. Each intended payment carries a
 * token, so a retried or double tap is recorded once.
 */
async function submitPayment(playerId, amount, gameId = null) {
  const token = `${currentSeason.id}-${playerId}-${gameId}-${amount}-${Date.now()}`;
  const row = balanceOf(playerId);
  const before = { ...row };
  balances[playerId] = {
    ...row,
    balance: String(Number(row.balance) + Number(amount)),
    season_total: String(Number(row.season_total) + Number(amount)),
    through_season: String(Number(row.through_season ?? row.balance) + Number(amount)),
    nights: (row.nights || []).map((n) =>
      n.game_id === gameId ? { ...n, paid: String(Number(n.paid) + Number(amount)) } : n
    ),
  };
  // Balances already in flight are now stale; see refreshBalancesQuietly.
  bumpLocalRevision();
  renderTabsFromBalances();

  try {
    // Queued like every write, so a re-read never answers before earlier
    // taps are sent.
    const entry = await enqueueRequest(() =>
      postJson(API_BASE, `/clubs/${currentClubId()}/players/${playerId}/payments`, {
        amount: String(amount),
        season_id: currentSeason.id,
        // Booked against this night alone when taken on 當天臨打.
        ...(gameId != null ? { game_id: gameId } : {}),
        note: "帳務頁記錄",
        client_token: token,
      })
    );
    refreshBalancesQuietly();
    const what = Number(amount) > 0 ? `已記錄收款 $${amount}` : `已記錄退款 $${Math.abs(amount)}`;
    toast(what, "good", { label: "復原", onClick: () => undoEntry(entry.id) });
  } catch (e) {
    balances[playerId] = before;
    bumpLocalRevision();
    renderTabsFromBalances();
    toast("記錄失敗：" + e.message);
  }
}

/** Redraws the three tabs from the balances in hand, without the network.
 */
function renderTabsFromBalances() {
  renderFeeTab();
  renderDropInList();
  renderClosingTab();
}

// 季費 tab

function renderFeeTab() {
  const season = currentSeason;

  let collected = 0;
  let total = 0;
  const rowsHtml = season.members
    .map((member) => {
      // Up to this season; see splitLedger.
      const split = splitLedger(balanceOf(member.id));
      const feeCharged = -Number(balanceOf(member.id).season_fee_charged);
      total += feeCharged;
      // The bar measures this season's fee, so what is owed counts only
      // up to the fee.
      const outstanding = Math.min(feeCharged, Math.max(0, -split.due));
      collected += feeCharged - outstanding;
      return moneyRowHtml(member.id, member.name, member.gender, split, "本季季費", feeCharged);
    })
    .join("");

  document.getElementById("fee-list").innerHTML =
    rowsHtml || '<div class="empty">本季尚無固定成員</div>';
  document.getElementById("fee-collected").textContent = `$${Math.round(collected)} / $${Math.round(total)}`;
  document.getElementById("fee-progress").style.width = total > 0 ? `${Math.min(100, (collected / total) * 100)}%` : "0%";
  // Counted the same way as the bar above.
  const unpaidCount = season.members.filter(
    (m) => splitLedger(balanceOf(m.id)).due < 0
  ).length;
  document.getElementById("fee-hint").textContent =
    unpaidCount === 0
      ? "全部已收"
      : `${season.members.length - unpaidCount} 人已收・${unpaidCount} 人未收・季費於開季前收款`;
  // Summed per game: games can cost different amounts.
  const games = seasonBillableGames(season);
  document.getElementById("fee-share-label").textContent =
    `每人 $${seasonFeeFor(season)} · ${games} 場`;
  renderFeeNotice(feeNoticeState(season, todayString()));
}

// 繳費通知

/** Who this season's 繳費通知 would go to now, and what the card says. Pure,
 * so tests/frontend/fee_notice.test.js states the rules; the server
 * enforces them again.
 *
 * - A season already over gets no card.
 * - Until the previous season is settled the figure is not final: the
 *   button is shown disabled, with the reason.
 * - Each member at most once a season.
 * - Nobody who owes nothing; nobody without LINE (counted, so the
 *   organizer can tell them in person).
 */
function feeNoticeState(season, today) {
  const last = season.games.reduce((max, g) => (g.date > max ? g.date : max), "");
  if (!last || last < today) return { kind: "none" };
  if (season.previous_season_settled === false) {
    return { kind: "blocked", text: "上一季結算後可發送繳費通知。" };
  }
  const owing = season.members.filter(
    (m) => !m.fee_notice_sent_at && splitLedger(balanceOf(m.id)).due < 0
  );
  const recipients = owing.filter((m) => m.linked);
  const sent = season.members.filter((m) => m.fee_notice_sent_at);
  const lastSent = sent.map((m) => m.fee_notice_sent_at).sort().pop();
  if (recipients.length === 0) {
    return sent.length
      ? { kind: "done", text: `已通知 <b>${sent.length}</b> 人・${sentTime(lastSent)}` }
      : { kind: "none" };
  }
  return {
    kind: "ready",
    text: `${sent.length ? `已通知 ${sent.length} 人・` : ""}未通知 <b>${recipients.length}</b> 人`,
    recipients,
    withoutLine: owing.length - recipients.length,
  };
}

function sentTime(iso) {
  const [day, time] = iso.split("T");
  return `${monthDay(day)} ${time.slice(0, 5)}`;
}

function renderFeeNotice(state) {
  const card = document.getElementById("fee-notice");
  card.hidden = state.kind === "none";
  if (card.hidden) return;
  document.getElementById("fee-notice-text").innerHTML = state.text;
  const btn = document.getElementById("fee-notice-btn");
  btn.hidden = state.kind === "done";
  btn.disabled = state.kind === "blocked";
}

/** Shows who would be sent the notice and what each would read, in the
 * server's own text (GET /seasons/{id}/fee-notice), so what is approved
 * is what is sent.
 */
let noticeLoad = 0;
async function openFeeNotice() {
  const state = feeNoticeState(currentSeason, todayString());
  if (state.kind !== "ready") return;
  const ticket = ++noticeLoad;
  const list = document.getElementById("notice-list");
  const sample = document.getElementById("notice-sample");
  const send = document.getElementById("notice-send");
  list.innerHTML = blockLoadingHtml("載入中");
  sample.textContent = "";
  document.getElementById("notice-sample-name").textContent = "";
  send.disabled = true;
  send.textContent = "傳送";
  const skipped = document.getElementById("notice-skipped");
  skipped.hidden = state.withoutLine === 0;
  skipped.textContent = `另有 ${state.withoutLine} 位未使用 LINE，不傳送。`;
  document.getElementById("notice-backdrop").hidden = false;
  try {
    const res = await fetch(`${API_BASE}/seasons/${currentSeason.id}/fee-notice`, {
      headers: authHeader(),
    });
    if (!res.ok) throw new Error(`伺服器回應 ${res.status}`);
    const { recipients } = await res.json();
    if (ticket !== noticeLoad) return;
    if (!recipients.length) {
      list.innerHTML = '<div class="empty">無需通知的成員。</div>';
      return;
    }
    list.innerHTML = recipients
      .map((r) => `<div><span>${escapeHtml(r.name)}</span><span>應繳 $${Number(r.due)}</span></div>`)
      .join("");
    document.getElementById("notice-sample-name").textContent = recipients[0].name;
    sample.textContent = recipients[0].text;
    send.textContent = `傳送給 ${recipients.length} 人`;
    send.disabled = false;
  } catch (e) {
    if (ticket !== noticeLoad) return;
    list.innerHTML = `<div class="empty">讀取失敗：${escapeHtml(e.message)}</div>`;
  }
}

function closeFeeNotice() {
  document.getElementById("notice-backdrop").hidden = true;
}

async function sendFeeNotice(btn) {
  try {
    const result = await whileBusy(btn, async () =>
      postJson(API_BASE, `/seasons/${currentSeason.id}/fee-notice`, {})
    );
    closeFeeNotice();
    toast(
      result.unreachable
        ? `已通知 ${result.sent} 位成員。${result.unreachable} 位未能送達（尚未加入官方帳號）。`
        : `已通知 ${result.sent} 位成員。`,
      "good"
    );
    loadLedger(currentSeason.id);
  } catch (e) {
    toast("傳送失敗：" + translateApiError(e.message));
  }
}

document.getElementById("notice-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "notice-backdrop") closeFeeNotice();
});

function seasonBillableGames(season) {
  return season.games.filter((g) => g.status !== "cancelled_refunded").length;
}

// 當天臨打 tab

function renderDropInGamePicker() {
  const picker = document.getElementById("dropin-game-picker");
  // Tonight's game, else the latest one played, else the first to come.
  // Taiwan's date, not UTC's.
  const today = todayString();
  const games = currentSeason.games;
  const played = games.filter((g) => g.date <= today);
  const nearest =
    games.find((g) => g.date === today) || played[played.length - 1] || games[0];
  picker.innerHTML = currentSeason.games
    .map((g) => `<option value="${g.id}">${describeDate(g.date).label}（臨打 ${g.confirmed_drop_ins.length} 人）</option>`)
    .join("");
  if (nearest) picker.value = nearest.id;
  picker.onchange = renderDropInList;
}

async function renderDropInTab() {
  renderDropInGamePicker();
  await renderDropInList();
}

async function renderDropInList() {
  const gameId = Number(document.getElementById("dropin-game-picker").value);
  const game = currentSeason.games.find((g) => g.id === gameId);
  const list = document.getElementById("dropin-list");
  if (!game || game.confirmed_drop_ins.length === 0) {
    list.innerHTML = '<div class="empty">本場沒有臨打</div>';
    return;
  }
  list.innerHTML = game.confirmed_drop_ins
    .map((d) =>
      moneyRowHtml(
        d.player_id,
        d.player_name,
        d.gender,
        // Tonight's fee, not the season's.
        nightSplit(balanceOf(d.player_id), gameId),
        null,
        null
      )
    )
    .join("");
}

// 季末結算 tab

async function renderClosingTab() {
  const season = currentSeason;
  const settled = season.settled_at !== null;
  document.getElementById("closing-before").hidden = settled;
  document.getElementById("closing-after").hidden = !settled;

  if (!settled) {
    // Not offered before it is allowed; the server refuses the same
    // cases.
    const blocked = settleBlocker(season, todayString());
    document.getElementById("closing-blocked").hidden = !blocked;
    document.getElementById("closing-blocked").textContent = blocked || "";
    document.getElementById("closing-ready").hidden = !!blocked;
    if (!blocked) renderClosingChoices(season, settlementPreview);
    return;
  }

  document.getElementById("closing-settled-at").textContent = season.settled_at.slice(0, 16).replace("T", " ");
  // Up to this season, like everything on this tab.
  const kept = season.members.filter((m) => splitLedger(balanceOf(m.id)).due !== 0);
  document.getElementById("closing-all-clear").hidden = kept.length > 0;
  document.getElementById("closing-kept-hint").hidden = kept.length === 0;
  document.getElementById("closing-list").innerHTML = kept
    .map((m) => moneyRowHtml(m.id, m.name, m.gender, splitLedger(balanceOf(m.id)), null, null))
    .join("");
}

/* Settling in one pass: every member with a non-zero balance gets a
 * choice, and 結算本季 writes the refunds and the cash together. 保留至下一季 is
 * the default and writes nothing; a balance already carries over.
 */
const closingCash = new Map();
// What the block is showing, so the functions below depend only on what
// they were given.
let closingSeason = null;
let closingPreview = null;

/** When a season may be settled, the rule _why_not_settleable enforces:
 * from three weeks before the last game, once no game still to come has
 * an unfilled absence. `today` is "YYYY-MM-DD", passed in for tests.
 */
function settleBlocker(season, today) {
  const games = [...season.games].sort((a, b) => (a.date < b.date ? -1 : 1));
  if (!games.length) return null;
  const opens = shiftDate(games[games.length - 1].date, -21);
  if (today < opens) return `${monthDay(opens)} 起可結算（最後一場前 3 週）。`;
  const open = games.find(
    (g) => g.date >= today && g.status === "scheduled" && g.absences.some((a) => !a.filled_by)
  );
  return open ? `${monthDay(open.date)} 尚有缺額，補齊後可結算。` : null;
}

function shiftDate(iso, days) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + days);
  return localIso(d);
}

function localIso(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function todayString() {
  return localIso(new Date());
}

function monthDay(iso) {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

/** This member's balance up to this season; positive means the club owes
 * them.
 */
function closingDue(playerId) {
  return Number(splitLedger(balanceOf(playerId)).due);
}

function renderClosingChoices(season, preview) {
  closingSeason = season || closingSeason;
  closingPreview = preview || closingPreview;
  season = closingSeason;
  // Add the refund the settlement is about to credit, or someone owed
  // exactly their refund would read as 已結清.
  const refundOf = new Map(
    closingPreview.members.map((m) => [m.player_id, Number(m.refund)])
  );
  const rows = season.members
    .map((m) => ({
      id: m.id,
      name: m.name,
      gender: m.gender,
      due: closingDue(m.id) + (refundOf.get(m.id) || 0),
    }))
    .filter((m) => m.due !== 0);

  document.getElementById("closing-preview").innerHTML = rows.length
    ? rows.map(closingRowHtml).join("")
    : '<div class="empty">所有成員均已結清。</div>';
  renderClosingCash(rows);
}

function closingRowHtml(m) {
  const owed = m.due > 0;
  const picked = closingCash.has(m.id) ? "cash" : "keep";
  const amount = Math.abs(m.due);
  return `
    <div class="m-row">
      <div class="m-top">
        ${avatarHtml(m.name, avatarUrlOf(m.id))}
        <span class="m-name">${escapeHtml(m.name)}${genderTag(m.gender)}</span>
        <span class="m-net ${owed ? "owed" : "owe"}">${owed ? "應退" : "應收"} $${amount}</span>
      </div>
      <div class="seg-pick" role="radiogroup" aria-label="${escapeHtml(m.name)} 結算方式">
        <button type="button" role="radio" aria-checked="${picked === "keep"}"
          class="${picked === "keep" ? "selected" : ""}"
          onclick="pickClosing(${m.id}, 'keep')">保留至下一季</button>
        <button type="button" role="radio" aria-checked="${picked === "cash"}"
          class="${picked === "cash" ? "selected" : ""}"
          onclick="pickClosing(${m.id}, 'cash')">${owed ? "退款" : "已收"}</button>
      </div>
    </div>`;
}

/** Cash in less cash out, so the organizer can count what should be in
 * hand, and what 結算本季 will write. The choices above are a selection, not
 * a record, so the line says when they take effect.
 */
function renderClosingCash(rows) {
  let total = 0;
  let moves = 0;
  for (const m of rows) {
    if (!closingCash.has(m.id)) continue;
    total -= m.due;
    moves += 1;
  }
  const el = document.getElementById("closing-cash");
  el.textContent = moves === 0 ? "—" : (total > 0 ? "+" : total < 0 ? "−" : "") + "$" + Math.abs(total);
  el.className = "balance-value" + (moves === 0 ? " even" : total > 0 ? " owed" : " owe");
  const pending = document.getElementById("closing-pending");
  pending.hidden = rows.length === 0;
  pending.textContent = `結算時記錄：現金收付 ${moves} 筆，保留 ${rows.length - moves} 筆。`;
}

function pickClosing(playerId, how) {
  // The amount is read at settle time, so a refreshed preview is used.
  if (how === "cash") closingCash.set(playerId, true);
  else closingCash.delete(playerId);
  renderClosingChoices();
}

/** Undoes a settlement: each refund is cancelled by an opposite entry.
 * Cash already paid out cannot be undone and now reads as owed back, so
 * those members are named.
 */
async function unsettleSeason() {
  if (!confirm("確定復原結算？\n退費紀錄以沖銷方式保留。")) return;
  try {
    // postJson marks the control busy and translates any error.
    const result = await postJson(API_BASE, `/seasons/${currentSeason.id}/unsettle`, {});
    const paid = (result && result.already_paid_out) || [];
    loadLedger(currentSeason.id);
    if (paid.length) {
      toast(
        `已復原結算。已退現金：${paid
          .map((p) => `${p.player_name} $${p.amount}`)
          .join("、")}，已改記為應收。`
      );
    } else {
      toast("已復原結算", "good");
    }
  } catch (e) {
    toast(e.message);
  }
}

async function settleSeason() {
  if (!confirm("確定結算本季？\n結算後名單與場地費即鎖定，其餘場次無法請假或報名臨打。")) return;
  const btn = document.getElementById("settle-btn");
  // Read now from the figures on screen; members not in closingCash are
  // not sent.
  const refundOf = new Map(
    closingPreview.members.map((m) => [m.player_id, Number(m.refund)])
  );
  const cash = [...closingCash.keys()]
    .map((id) => ({
      player_id: id,
      amount: String(-(closingDue(id) + (refundOf.get(id) || 0))),
    }))
    .filter((entry) => entry.amount !== "0");
  try {
    await whileBusy(btn, async () => {
      await postJson(API_BASE, `/seasons/${currentSeason.id}/settle`, { cash });
    });
    closingCash.clear();
    loadLedger(currentSeason.id);
  } catch (e) {
    toast("結算失敗：" + e.message);
    btn.disabled = false;
    btn.textContent = "結算本季";
  }
}
