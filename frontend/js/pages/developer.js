const API_BASE = apiBase();
const LIFF_ID = "2011156233-6CouG6VI";

const CHEVRON =
  '<span class="lrow-go" aria-hidden="true"><svg viewBox="0 0 16 16" width="16" height="16"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';

/** The four figures that say whether the app is in use this week. */
const KPIS = [
  ["active_clubs", "活躍球隊"],
  ["games_this_week", "本週場次"],
  ["signups_this_week", "本週報名"],
  ["unread_reports", "未讀回報", true],
];

/** The other counts, under 其他統計. Counts only, no money; see
 * routes/developer.py.
 */
const GROUPS = [
  {
    title: "球隊與人",
    figures: [
      ["clubs", "球隊"],
      ["players", "所有球員"],
      ["club_members", "球隊成員"],
      ["pending_members", "等待核准"],
    ],
  },
  {
    title: "季別與場次",
    figures: [
      ["seasons", "季別"],
      ["settled_seasons", "已結算"],
      ["games", "場次"],
      ["upcoming_games", "未進行"],
    ],
  },
  {
    title: "出席與帳務",
    figures: [
      ["drop_ins", "有效臨打"],
      ["waitlist_entries", "候補中"],
      ["ledger_entries", "帳本筆數"],
    ],
  },
  {
    title: "問題回報",
    figures: [
      ["reports", "累計回報"],
      ["unread_reports", "未讀"],
    ],
  },
];

/** The page a failed request came from, by the name people know it by. */
const PAGE_NAMES = {
  "index.html": "首頁",
  "member.html": "會員頁",
  "ledger.html": "我的帳務",
  "profile.html": "個人資料",
  "report.html": "回報問題",
  "reports.html": "問題回報",
  "organizer.html": "管理・總覽",
  "organizer-members.html": "管理・名單",
  "organizer-ledger.html": "管理・帳務",
  "organizer-settings.html": "管理・設定",
  "developer.html": "後台總覽",
};

const ACTIVITY_SHOWN = 10;

let allClubs = [];
let showEmptyClubs = false;

function pageName(file) {
  if (!file) return "頁面不明";
  return PAGE_NAMES[file] || file;
}

/** "2026-10-14" → "10/14". */
function shortDate(iso) {
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso || "");
  return m ? `${Number(m[1])}/${Number(m[2])}` : iso || "";
}

/** A season as the trail names it, "2026-09-15~2026-12-08" → "9/15–12/8". */
function spanLabel(span) {
  if (!span) return "";
  const [first, last] = span.split("~");
  return last ? `${shortDate(first)}–${shortDate(last)}` : shortDate(first);
}

/** "season_roster.py:120 in add_member" → "season_roster.py 第 120
 * 行・add_member".
 */
function locationLabel(where) {
  const m = /^(.+):(\d+) in (.+)$/.exec(where || "");
  return m ? `${m[1]} 第 ${m[2]} 行・${m[3]}` : where || "";
}

/** One audit row as a sentence, using the words of the button that
 * made it (收款、復原、場地費照算…). Plain text; the caller escapes. */
function activitySentence(e) {
  const who = e.actor_name || "（已刪除的帳號）";
  const subject = e.subject_name || "（已刪除的成員）";
  const n = Number(e.amount);
  const dollars = `$${Math.abs(n)}`;
  const span = spanLabel(e.detail);
  switch (e.action) {
    case "payment": {
      const night = e.detail ? `（${shortDate(e.detail)} 場次）` : "";
      return `${who} 記錄 ${subject} ${n < 0 ? "退款" : "收款"} ${dollars}${night}`;
    }
    case "payment_undone":
      return `${who} 復原 ${subject} 的${n < 0 ? "退款" : "收款"} ${dollars}`;
    case "club_created":
      return `${who} 建立球隊`;
    case "club_deleted":
      return `${who} 刪除球隊`;
    case "member_approved":
      return `${who} 核准 ${subject} 加入球隊`;
    case "member_removed":
      return `${who} 將 ${subject} 移出球隊`;
    case "players_merged":
      return `${who} 將 ${e.detail || "重複的成員"} 合併至 ${subject}`;
    case "season_started":
      return `${who} 建立季別 ${span}，場地費 ${dollars}`;
    case "season_deleted":
      return `${who} 刪除季別 ${span}`;
    case "season_settled":
      return `${who} 完成季末結算（${span}）`;
    case "season_unsettled":
      return `${who} 復原結算（${span}）`;
    case "fee_notice_sent":
      return `${who} 發送繳費通知，共 ${e.detail} 人`;
    case "venue_cost_changed":
      return `${who} 將場地費由 $${e.detail} 改為 ${dollars}`;
    case "ac_surcharge_changed":
      return `${who} 將每場冷氣費用由 $${e.detail} 改為 ${dollars}`;
    case "capacity_changed":
      return `${who} 將人數上限由 ${e.detail} 人改為 ${n} 人`;
    case "ac_forecast_corrected":
      return `${who} 修改預定冷氣場次，修改後共 ${e.detail} 場`;
    case "ac_on":
      return `${who} 記錄 ${shortDate(e.detail)} 實際有開冷氣，場地費 +${dollars}`;
    case "ac_off":
      return `${who} 記錄 ${shortDate(e.detail)} 實際未開冷氣，場地費 −${dollars}`;
    case "game_cancelled_refunded":
      return `${who} 取消 ${shortDate(e.detail)} 場次，場地費不算這場`;
    case "game_cancelled":
      return `${who} 取消 ${shortDate(e.detail)} 場次，場地費照算`;
    case "game_updated":
      return `${who} 修改 ${shortDate(e.detail)} 場次`;
    case "roster_added":
      return `${who} 將 ${subject} 加入固定成員`;
    case "roster_removed":
      return `${who} 將 ${subject} 移出固定成員`;
    default:
      return `${who} 執行 ${e.action}`;
  }
}

/** Money moved, somebody or something went, or anything else. */
function activityKind(action) {
  if (/^(payment|ac_|venue_cost|fee_notice)/.test(action)) return "money";
  if (/(removed|deleted|cancelled)/.test(action)) return "remove";
  return "";
}

function kpisHtml(data) {
  return KPIS.map(([key, label, watch]) => {
    const loading = !data;
    const value = loading ? 0 : Number(data[key] || 0);
    const cls = watch && value > 0 ? "kpi attention" : "kpi";
    const num = loading
      ? '<i class="spinner sm" role="status" aria-label="載入中"></i>'
      : String(value);
    return `<div class="${cls}"><span class="kpi-num">${num}</span><span class="kpi-label">${label}</span></div>`;
  }).join("");
}

function errorRowHtml(e) {
  return `
    <div class="err-row">
      <div class="err-head"><span class="err-page">${escapeHtml(pageName(e.page))}</span><span>${escapeHtml(stampTime(e.occurred_at))}</span></div>
      <div class="err-what">${escapeHtml(e.error_type)}：${escapeHtml(e.message)}</div>
      <div class="err-kv"><span class="k">操作</span><code>${escapeHtml(`${e.method} ${e.path}`)}</code></div>
      <div class="err-kv"><span class="k">位置</span><code>${escapeHtml(locationLabel(e.location))}</code></div>
    </div>`;
}

function errorsHtml(errors) {
  if (!errors.length) {
    return '<div class="lrow static"><span class="lrow-k">最近 7 天沒有錯誤</span><span class="state-pill ok">正常</span></div>';
  }
  return errors.map(errorRowHtml).join("");
}

function activityRowHtml(e) {
  const where = e.club_name ? `・${escapeHtml(e.club_name)}` : "";
  return `
    <div class="act-row">
      <span class="act-dot ${activityKind(e.action)}" aria-hidden="true"></span>
      <div class="act-main">
        <div class="act-text">${escapeHtml(activitySentence(e))}</div>
        <div class="act-meta">${escapeHtml(stampTime(e.occurred_at))}${where}</div>
      </div>
    </div>`;
}

function activityHtml(events, expanded) {
  if (!events.length) {
    return '<div class="lrow static"><span class="lrow-k">尚無操作紀錄</span></div>';
  }
  const shown = expanded ? events : events.slice(0, ACTIVITY_SHOWN);
  return shown.map(activityRowHtml).join("");
}

/** A club that never got going: nothing ever charged or paid in it
 * (joining a season with games charges a fee, so a club in use always has
 * entries).
 */
function isEmptyClub(c) {
  return !c.last_activity;
}

/** The clubs to list. A search covers every club, folded ones included;
 * without one, clubs in use come first, most recent first.
 */
function visibleClubs(clubs, query, showEmpty) {
  const q = (query || "").trim().toLowerCase();
  const matches = (c) =>
    c.name.toLowerCase().includes(q) ||
    c.organizers.some((name) => name.toLowerCase().includes(q));
  const picked = q ? clubs.filter(matches) : clubs.filter((c) => showEmpty || !isEmptyClub(c));
  return [...picked].sort((a, b) => {
    const at = a.last_activity || "";
    const bt = b.last_activity || "";
    if (at !== bt) return at < bt ? 1 : -1;
    return b.id - a.id;
  });
}

function clubLineHtml(c) {
  const who = c.organizers.length ? c.organizers.join("、") : "無管理員";
  const when = c.last_activity ? `最近 ${stampTime(c.last_activity)}` : "尚無帳務紀錄";
  return `
    <button type="button" class="lrow dclub" onclick="openClub(${c.id})">
      <span class="lrow-k">
        <span class="lrow-name">${escapeHtml(c.name)}</span>
        <span class="dclub-meta">${escapeHtml(who)}・成員 ${c.members}・季別 ${c.seasons}・${escapeHtml(when)}</span>
      </span>
      ${CHEVRON}
    </button>`;
}

function renderClubs() {
  const query = document.getElementById("club-search").value;
  const list = visibleClubs(allClubs, query, showEmptyClubs);
  const empties = allClubs.filter(isEmptyClub).length;
  document.getElementById("club-count").textContent = `共 ${allClubs.length} 個`;
  document.getElementById("clubs").innerHTML = list.length
    ? list.map(clubLineHtml).join("")
    : `<div class="lrow static"><span class="lrow-k">${query.trim() ? "沒有符合的球隊" : "尚無使用中的球隊"}</span></div>`;
  const toggle = document.getElementById("clubs-empty-toggle");
  toggle.hidden = !empties || Boolean(query.trim());
  toggle.textContent = showEmptyClubs
    ? "隱藏尚無帳務紀錄的球隊"
    : `顯示 ${empties} 個尚無帳務紀錄的球隊`;
}

function statsHtml(data) {
  const groups = GROUPS.map((group) => {
    const rows = group.figures
      .map(
        ([key, label]) =>
          `<div class="lrow static"><span class="lrow-k">${label}</span><span class="lrow-v num">${Number(data[key] || 0)}</span></div>`
      )
      .join("");
    return `<div class="lgroup">${group.title}</div><div class="lcard">${rows}</div>`;
  }).join("");
  const newest = (data.newest_clubs || []).length
    ? `<div class="lgroup">最新建立的球隊</div><div class="chips">${data.newest_clubs
        .map((name) => `<span class="chip">${escapeHtml(name)}</span>`)
        .join("")}</div>`
    : "";
  return groups + newest;
}

function openClub(clubId) {
  rememberId(CLUB_STORAGE_KEY, String(clubId));
  location.href = "organizer.html";
}

/** Fills one block from its own request, so a slow or missing list
 * (a deploy still on its way) never holds up the others. */
async function fillBlock(path, box, render) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: authHeader() });
    if (!res.ok) throw new Error(res.statusText || String(res.status));
    render(await res.json());
  } catch (e) {
    box.innerHTML = `<div class="lrow static"><span class="lrow-k">暫時無法讀取</span><span class="state-pill warn">${escapeHtml(e.message || "錯誤")}</span></div>`;
  }
}

(async function init() {
  const box = document.getElementById("overview");
  const sub = document.getElementById("overview-sub");
  const kpis = document.getElementById("kpis");
  const errors = document.getElementById("errors");
  const activity = document.getElementById("activity");
  const clubs = document.getElementById("clubs");
  kpis.innerHTML = kpisHtml(null);
  errors.innerHTML = blockLoadingHtml();
  activity.innerHTML = blockLoadingHtml();
  clubs.innerHTML = blockLoadingHtml();

  try {
    const identified = await initLiffIdentity(API_BASE, LIFF_ID);
    if (!identified) {
      box.innerHTML = signInFailureHtml(identified);
      return;
    }
    const overviewReq = fetch(`${API_BASE}/developer/overview`, { headers: authHeader() });

    fillBlock("/developer/errors", errors, (rows) => {
      errors.innerHTML = errorsHtml(rows);
    });
    fillBlock("/developer/activity?limit=50", activity, (rows) => {
      const more = document.getElementById("activity-more");
      let expanded = false;
      const draw = () => {
        activity.innerHTML = activityHtml(rows, expanded);
        more.hidden = rows.length <= ACTIVITY_SHOWN;
        more.textContent = expanded ? "只顯示最近 10 筆" : `顯示全部 ${rows.length} 筆`;
      };
      more.onclick = () => {
        expanded = !expanded;
        draw();
      };
      draw();
    });
    fillBlock("/developer/clubs", clubs, (rows) => {
      allClubs = rows;
      renderClubs();
    });

    const res = await overviewReq;
    if (res.status === 403) {
      box.innerHTML = emptyStateHtml("沒有查看權限", "這個頁面僅供開發者查看。");
      return;
    }
    if (res.status === 503) {
      box.innerHTML = emptyStateHtml("尚未設定", "伺服器尚未設定開發者身分，暫時無法查看。");
      return;
    }
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    sub.textContent = `於 ${new Date().toLocaleString("zh-TW", { hour12: false })} 讀取`;
    kpis.innerHTML = kpisHtml(data);
    const unread = Number(data.unread_reports || 0);
    document.getElementById("reports-unread").textContent = unread ? `未讀 ${unread}` : "";
    document.getElementById("stats").innerHTML = statsHtml(data);
  } catch (e) {
    showPageError(box, e.message);
  }
})();

document.getElementById("club-search").addEventListener("input", renderClubs);
document.getElementById("clubs-empty-toggle").addEventListener("click", () => {
  showEmptyClubs = !showEmptyClubs;
  renderClubs();
});
document.getElementById("stats-toggle").addEventListener("click", () => {
  const stats = document.getElementById("stats");
  const toggle = document.getElementById("stats-toggle");
  stats.hidden = !stats.hidden;
  toggle.setAttribute("aria-expanded", String(!stats.hidden));
});
