// The local API on a development machine, production elsewhere; see
// apiBase.
const API_BASE = apiBase();
// The club is chosen from the top bar, as on the member page.
mountClubChip(document.getElementById("club-chip"), document.getElementById("club-picker"));
const LIFF_ID = "2011156233-6CouG6VI";

/* 本季／場次／球隊 tabs */
// Remembered for this visit, so returning from 帳務 opens the same tab.
let setTab = "season";
try {
  const saved = sessionStorage.getItem("vf_set_tab");
  if (["season", "games", "club"].includes(saved)) setTab = saved;
} catch {}
let clubChosen = false;

function showSetTab(tab) {
  setTab = tab;
  try { sessionStorage.setItem("vf_set_tab", tab); } catch {}
  for (const b of document.querySelectorAll("[data-set-tab]")) {
    const on = b.dataset.setTab === tab;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
  }
  for (const panel of document.querySelectorAll("[data-set-panel]")) {
    panel.hidden = panel.dataset.setPanel !== tab || (panel.id === "club-panel" && !clubChosen);
  }
}
document.getElementById("set-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-set-tab]");
  if (b) showSetTab(b.dataset.setTab);
});

// 進階 and 已結束場次 open and close in place.
document.addEventListener("click", (e) => {
  const fold = e.target.closest("[data-fold]");
  if (!fold) return;
  const body = document.querySelector(`[data-fold-body="${fold.dataset.fold}"]`);
  if (!body) return;
  body.hidden = !body.hidden;
  fold.setAttribute("aria-expanded", String(!body.hidden));
});

function openSheet(id) { document.getElementById(id).hidden = false; }
function closeSheet(id) { document.getElementById(id).hidden = true; }

const LIST_GO =
  '<span class="lrow-go" aria-hidden="true"><svg viewBox="0 0 16 16" width="16" height="16"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';
const FACT_ICONS = {
  people: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.6-3.3 3-5 6-5s5.4 1.7 6 5"/><circle cx="17" cy="9" r="2.4"/><path d="M16.5 14.2c2.3.2 3.9 1.8 4.5 4.8"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  pin: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
};
function factIcon(name) {
  return `<span class="st-ic"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${FACT_ICONS[name]}</svg></span>`;
}

/** Today in the browser's calendar (Taiwan's for this app's users);
 * toISOString would give UTC's.
 */
function todayKey() {
  return dateKey(new Date());
}

/** The season's games as an even grid, seven to a row, shared by the 場次
 * card, the 修改預定 sheet and the wizard. `items` are {iso, on, past, off};
 * `attr` names the data attribute a tappable cell carries.
 */
function dayGridHtml(items, o = {}) {
  if (!items.length) return '<p class="st-note" style="margin:0">尚無場次，請先產生場次。</p>';
  const legend = o.legend === false
    ? ""
    : `<div class="day-legend"><span><i class="on"></i>${o.onLabel || "預定冷氣"}</span><span><i></i>一般場次</span>${
        o.lockPast && items.some((it) => it.past) ? '<span><i class="past"></i>已結束</span>' : ""
      }</div>`;
  const cells = items.map((it) => {
    const d = new Date(it.iso + "T00:00:00");
    const locked = (o.lockPast && it.past) || it.off;
    const cls = "day-cell" + (it.on && !it.off ? " on" : "") + (o.lockPast && it.past ? " done" : "") +
      (it.off ? " off" : "") + (o.removing ? " removing" : "");
    const act = o.attr && !locked ? ` ${o.attr}="${it.iso}"` : "";
    return `<button type="button" class="${cls}"${act}${act ? "" : " disabled"} aria-pressed="${!!it.on}" aria-label="${d.getMonth() + 1}/${d.getDate()}${it.on ? " 冷氣" : ""}"><b>${d.getDate()}</b><small>${d.getMonth() + 1}月</small>${o.removing ? '<span class="del" aria-hidden="true">✕</span>' : ""}</button>`;
  });
  return legend + `<div class="day-grid">${cells.join("")}</div>`;
}

let currentSeasonId = null;
let currentSeason = null;
let originalVenueCost = null;
let cancelGameId = null;
let cancelRefunded = true;

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

showPageLoading(document.getElementById("page-state"));
document.getElementById("settings-form").hidden = true;

// Every read carries the caller's token, so nothing is fetched before
// identity resolves.
identityReady.then((identified) => {
  if (!identified) {
    stopLoadingEscalation();
    document.getElementById("page-state").innerHTML = signInFailureHtml(identified);
    return;
  }
  initClubAndSeasonPickers(
    API_BASE,
    document.getElementById("club-picker"),
    document.getElementById("season-picker"),
    "vf_org_season",
    (id) => {
    stopLoadingEscalation();
    if (!id) document.getElementById("page-state").innerHTML = "";
    const hasClub = !!currentClubId();
    document.getElementById("no-club-note").hidden = hasClub;
    // The tabs need a club; 本季 and 場次 also need a season and stay inside
    // #settings-form, which loadSettings reveals.
    document.getElementById("set-tabs").hidden = !hasClub;
    clubChosen = hasClub;
    showSetTab(setTab);
    if (hasClub) fillClubName();
    setWizardOpen((hasClub && !id) || creatingNextSeason);
    // Revealed by loadSettings once the season arrives, not as an empty
    // form. No picker when creating the first season is the only option.
      document.getElementById("season-picker-field").hidden = !id;
      document.getElementById("season-picker").hidden = !id;
      if (id) loadSettings(id);
    },
    (e) => showPageError(document.getElementById("page-state"), e.message),
    // Only clubs this person organizes; see managesClub.
    managesClub
  );
});

// 開新一季 wizard: cost, then dates, then members, with each night's price
// recomputed at every step. Dates are generated ("every <weekday> from
// <date> for <n> weeks") and then pruned.

let wizardDates = [];
let extraNames = [];
let pickedIds = new Set();
const wizardAcDates = new Set();
// Set while 開新一季 is open: who to tick in step 3; also keeps a late
// repaint from hiding the wizard.
let nextSeasonMemberIds = null;
let creatingNextSeason = false;
let clubRoster = [];
// player_id -> what the club owes them (positive) or they owe it, shown
// beside each name while picking the roster.
let rosterBalances = new Map();

document.getElementById("create-season-form").addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) showWizardStep(Number(go.dataset.go));
});

/** Hours and minutes as two selects: a native time input shows the phone's
 * own clock format (下午 06:30), and the venue writes 18:30.
 */
function fillTimeSelects(prefix) {
  const hours = ['<option value="">--</option>'];
  for (let h = 0; h < 24; h += 1) {
    const v = String(h).padStart(2, "0");
    hours.push(`<option value="${v}">${v}</option>`);
  }
  const minutes = [];
  for (let m = 0; m < 60; m += 5) {
    const v = String(m).padStart(2, "0");
    minutes.push(`<option value="${v}">${v}</option>`);
  }
  document.getElementById(prefix + "-h").innerHTML = hours.join("");
  document.getElementById(prefix + "-m").innerHTML = minutes.join("");
}

// g-start / g-end belong to one game's edit sheet.
for (const prefix of ["c-start", "c-end", "f-start", "f-end", "g-start", "g-end"]) {
  fillTimeSelects(prefix);
}

/** "18:30" out of the two selects, or null while no hour is chosen. */
function readTimeValue(prefix) {
  const hour = document.getElementById(prefix + "-h").value;
  if (!hour) return null;
  return `${hour}:${document.getElementById(prefix + "-m").value || "00"}`;
}

function writeTimeValue(prefix, value) {
  const [hour, minute] = (value || "").slice(0, 5).split(":");
  document.getElementById(prefix + "-h").value = hour || "";
  // A minute off the five-minute grid selects the nearest option rather
  // than becoming :00.
  const select = document.getElementById(prefix + "-m");
  const wanted = minute || "00";
  select.value = [...select.options].some((o) => o.value === wanted)
    ? wanted
    : String((Math.round(Number(wanted) / 5) * 5) % 60).padStart(2, "0");
}

/** Opens or closes the wizard's screen. It covers the club and season
 * pickers, so a new season is not mistaken for edits to the old one. The
 * close button appears only when there is a season to go back to.
 */
function setWizardOpen(open) {
  document.getElementById("wizard-backdrop").hidden = !open;
  document.getElementById("wizard-close").hidden = !creatingNextSeason;
  document.body.style.overflow = open ? "hidden" : "";
  if (open) document.querySelector("#wizard-backdrop .gsheet").scrollTop = 0;
}

function showWizardStep(step) {
  document.getElementById("wizard-step-count").textContent = `第 ${step} 步／共 4 步`;
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.hidden = Number(panel.dataset.panel) !== step;
  }
  for (const dot of document.querySelectorAll(".step")) {
    const n = Number(dot.dataset.step);
    dot.classList.toggle("on", n === step);
    dot.classList.toggle("done", n < step);
    dot.querySelector("i").textContent = n < step ? "✓" : String(n);
  }
  if (step === 2 && wizardDates.length === 0) {
    // Default to the next occurrence of the chosen weekday.
    const start = document.getElementById("g-start");
    if (!start.value) start.value = new Date().toISOString().slice(0, 10);
  }
  if (step === 3) loadClubRoster();
  if (step === 4) renderConfirm();
  renderWizard();
  document.querySelector("#wizard-backdrop .gsheet").scrollTo({ top: 0, behavior: "smooth" });
}

function startNextSeason() {
  const s = currentSeason;
  if (!s) return;
  const set = (id, value) => (document.getElementById(id).value = value);
  set("c-cost", s.total_venue_cost);
  set("c-ac", s.ac_surcharge);
  set("c-capacity", s.capacity);
  set("c-min-roster", s.minimum_roster);
  writeTimeValue("c-start", s.game_start_time);
  writeTimeValue("c-end", s.game_end_time);
  set("c-location", s.location || "");
  set("c-deadline", String(s.change_deadline_hours ?? 24));
  wizardDates = [];
  wizardAcDates.clear();
  extraNames = [];
  clubRoster = [];
  nextSeasonMemberIds = new Set(s.members.map((m) => m.id));
  creatingNextSeason = true;
  const dates = s.games.map((g) => g.date).sort();
  if (dates.length) {
    const next = new Date(dates[dates.length - 1] + "T00:00:00");
    set("g-weekday", String(next.getDay()));
    next.setDate(next.getDate() + 1);
    set("g-start", dateKey(next));
    set("g-weeks", String(dates.length));
    generateDates();
    // The air-conditioning forecast carries over only when the last
    // season was all cooled or all not.
    const cooled = s.games.filter((g) => g.air_conditioned).length;
    if (cooled > 0 && cooled === s.games.length) setAllAc(true);
  }
  document.getElementById("create-season-title").textContent = "開新一季";
  // Hide the intro box so the stepper starts near the top.
  document.getElementById("create-season-blurb").hidden = true;
  setWizardOpen(true);
  showWizardStep(1);
}

function cancelNextSeason() {
  creatingNextSeason = false;
  nextSeasonMemberIds = null;
  document.getElementById("create-season-title").textContent = "建立第一季";
  document.getElementById("create-season-blurb").hidden = false;
  setWizardOpen(false);
}

function generateDates() {
  const weekday = Number(document.getElementById("g-weekday").value);
  const startValue = document.getElementById("g-start").value;
  const weeks = Number(document.getElementById("g-weeks").value) || 0;
  if (!startValue || weeks < 1) {
    toast("請填寫起始日期與場次數");
    return;
  }
  // First matching weekday on or after the start, then a week at a time.
  const first = new Date(startValue + "T00:00:00");
  while (first.getDay() !== weekday) first.setDate(first.getDate() + 1);

  const dates = [];
  for (let i = 0; i < weeks; i++) {
    const d = new Date(first);
    d.setDate(first.getDate() + i * 7);
    dates.push(dateKey(d));
  }
  wizardDates = dates;
  renderWizard();
}

function removeDate(iso) {
  wizardDates = wizardDates.filter((d) => d !== iso);
  wizardAcDates.delete(iso);
  renderWizard();
}

function toggleAcDate(iso) {
  if (wizardAcDates.has(iso)) wizardAcDates.delete(iso);
  else wizardAcDates.add(iso);
  renderWizard();
}

function setAllAc(on) {
  wizardAcDates.clear();
  if (on) wizardDates.forEach((d) => wizardAcDates.add(d));
  renderWizard();
}

function toggleAllAc() {
  const acSurcharge = Number(document.getElementById("c-ac").value) || 0;
  setAllAc(wizardDates.some((d) => !(acSurcharge > 0 && wizardAcDates.has(d))));
}

// 移除場次 makes a tap remove the date instead of toggling its air
// conditioning, until 完成.
let wizardRemoving = false;
function toggleRemoving() {
  wizardRemoving = !wizardRemoving;
  renderWizard();
}
document.getElementById("date-grid").addEventListener("click", (e) => {
  const ac = e.target.closest("[data-wz-ac]");
  if (ac) { toggleAcDate(ac.dataset.wzAc); return; }
  const del = e.target.closest("[data-wz-del]");
  if (del) removeDate(del.dataset.wzDel);
});

function addOneOffDate() {
  const value = prompt("請輸入日期（格式：YYYY-MM-DD）");
  if (!value) return;
  const iso = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    toast("日期格式應為 2026-07-07");
    return;
  }
  if (!wizardDates.includes(iso)) wizardDates.push(iso);
  wizardDates.sort();
  renderWizard();
}

function addExtraName() {
  const input = document.getElementById("c-extra-name");
  const name = input.value.trim();
  if (!name) return;
  if (!extraNames.includes(name)) extraNames.push(name);
  input.value = "";
  renderWizard();
}

function removeExtraName(name) {
  extraNames = extraNames.filter((n) => n !== name);
  renderWizard();
}

async function loadClubRoster() {
  if (clubRoster.length) return;
  clubRoster = await fetchClubMembers(API_BASE, currentClubId());
  // Everyone pre-ticked: the common case is confirming.
  pickedIds = new Set(
    clubRoster.filter((m) => !nextSeasonMemberIds || nextSeasonMemberIds.has(m.id)).map((m) => m.id)
  );
  renderWizard();
  // Each person's balance, so leaving somebody out is decided with the
  // money in view. Optional: the names are what matters.
  try {
    const balances = await (
      await fetch(`${API_BASE}/clubs/${currentClubId()}/balances`, { headers: authHeader() })
    ).json();
    rosterBalances = new Map(balances.map((b) => [b.player_id, Number(b.balance)]));
    renderWizard();
  } catch {
    // Rows without a figure rather than a blocked step.
  }
}

function pickAll(on) {
  pickedIds = on ? new Set(clubRoster.map((m) => m.id)) : new Set();
  renderWizard();
}

function openRosterSheet() {
  document.getElementById("roster-backdrop").hidden = false;
}

function closeRosterSheet() {
  document.getElementById("roster-backdrop").hidden = true;
  renderWizard();
}

function selectedNames() {
  return clubRoster.filter((m) => pickedIds.has(m.id)).map((m) => m.name).concat(extraNames);
}

function renderWizard() {
  // Step 2: the dates and the running total. With no air-conditioning
  // charge, cells are tappable only for 移除場次.
  const acSurcharge = Number(document.getElementById("c-ac").value) || 0;
  const acOn = (d) => acSurcharge > 0 && wizardAcDates.has(d);
  if (!wizardDates.length) wizardRemoving = false;
  document.getElementById("date-grid").innerHTML = dayGridHtml(
    wizardDates.map((iso) => ({ iso, on: acOn(iso), past: false, off: false })),
    {
      attr: wizardRemoving ? "data-wz-del" : acSurcharge > 0 ? "data-wz-ac" : null,
      removing: wizardRemoving,
      legend: acSurcharge > 0,
    }
  );
  document.getElementById("wz-hint").textContent = wizardRemoving
    ? "點選日期以移除該場次。"
    : acSurcharge > 0
      ? "點選日期切換是否開冷氣。"
      : "如需刪除日期，請按「移除場次」。";
  const anyOff = wizardDates.some((d) => !acOn(d));
  const allBtn = document.getElementById("wz-all-ac");
  allBtn.hidden = !(acSurcharge > 0 && wizardDates.length);
  allBtn.textContent = anyOff ? "全部開啟" : "全部關閉";
  const removeBtn = document.getElementById("wz-remove");
  removeBtn.textContent = wizardRemoving ? "完成" : "移除場次";
  removeBtn.disabled = !wizardDates.length;

  const cost = Number(document.getElementById("c-cost").value) || 0;
  const memberCount = selectedNames().length;
  // Divided by capacity, not by the names picked.
  const capacity = Number(document.getElementById("c-capacity").value) || 0;
  const cooledCount = wizardDates.filter(acOn).length;
  const prices = previewSharePerGame(cost, wizardDates.length, capacity, acSurcharge, cooledCount);
  const share = prices ? prices.plain : null;

  const summary = document.getElementById("gen-summary");
  if (wizardDates.length === 0) {
    summary.innerHTML = "<span>尚無場次</span>";
  } else {
    const span = `${describeDate(wizardDates[0]).label} – ${describeDate(wizardDates[wizardDates.length - 1]).label}`;
    summary.innerHTML =
      `<b>${wizardDates.length} 場</b><span>${span}</span>` +
      (share
        ? `<span>・${cooledCount ? "一般場次" : ""}每人 $${share}${cooledCount ? `・冷氣場次每人 $${prices.cooled}` : ""}</span>`
        : "<span>・填妥場地費與人數上限後顯示金額</span>");
  }

  // Step 3: one summary line; the roster itself is in a sheet.
  const owedLabel = (id) => {
    const amount = Number(rosterBalances.get(id) || 0);
    if (!amount) return "";
    return amount < 0
      ? `<span class="pk-bal owe">應收 $${Math.abs(amount)}</span>`
      : `<span class="pk-bal credit">餘 $${amount}</span>`;
  };
  document.getElementById("roster-list").innerHTML = clubRoster.length
    ? clubRoster
        .map(
          (m) => `
            <div class="pick-row${pickedIds.has(m.id) ? " on" : ""}" onclick="togglePick(${m.id})">
              <i class="box">✓</i>
              ${avatarHtml(m.name, m.avatar_url)}
              <span class="pk-name">${escapeHtml(m.name)}</span>
              ${m.linked === false ? '<span class="guest-tag">訪客</span>' : ""}
              ${m.role === "organizer" ? '<span class="role-tag">管理員</span>' : ""}
              ${owedLabel(m.id)}
            </div>`
        )
        .join("")
    : '<div class="empty">球隊中尚無其他成員，請於下方直接輸入姓名</div>';

  const picked = selectedNames();
  const rosterLine = document.getElementById("roster-summary");
  if (rosterLine) {
    rosterLine.textContent = picked.length
      ? `${picked.length} 人・${picked.slice(0, 3).join("、")}${picked.length > 3 ? " 等" : ""}`
      : "尚未選擇成員";
    const go = document.getElementById("roster-go");
    if (go) go.textContent = picked.length ? "編輯 ›" : "選擇成員 ›";
  }

  // People with a balance who are left out of the new season: allowed,
  // but worth seeing.
  const leftOut = clubRoster.filter(
    (m) => !pickedIds.has(m.id) && Number(rosterBalances.get(m.id) || 0) !== 0
  );
  const alertRow = document.getElementById("roster-alert");
  if (alertRow) {
    alertRow.hidden = leftOut.length === 0;
    if (leftOut.length) {
      document.getElementById("roster-alert-text").textContent =
        `${leftOut.length} 位有餘額但不在本季名單：` +
        leftOut.slice(0, 2).map((m) => m.name).join("、") +
        (leftOut.length > 2 ? " 等" : "");
    }
  }

  document.getElementById("extra-names").innerHTML = extraNames
    .map(
      (n) => `<span class="chip">${escapeHtml(n)}<button type="button" aria-label="移除" onclick="removeExtraName('${n.replace(/'/g, "\\'")}')">✕</button></span>`
    )
    .join("");

  const preview = document.getElementById("create-preview");
  if (!cost || !wizardDates.length || !capacity) {
    preview.innerHTML = emptyStateHtml(
      "資料尚未齊全",
      `目前：場地費 $${cost || 0}、${wizardDates.length} 場、人數上限 ${capacity || 0} 人。三項皆填妥後才能計算每人金額。`
    );
    return;
  }
  if (!prices) {
    preview.innerHTML = emptyStateHtml(
      "冷氣費用超過場地費總額",
      `已選 ${cooledCount} 場冷氣、每場 $${acSurcharge}，合計超過場地費總額 $${cost}。請檢查金額或減少冷氣場次。`
    );
    return;
  }
  const seasonFee = prices.plain * (wizardDates.length - cooledCount) + prices.cooled * cooledCount;
  preview.innerHTML = `
    <div class="balance" style="margin-bottom:8px">
      <span class="balance-label">每人季費</span>
      <span class="balance-value">$${seasonFee}</span>
    </div>
    <p class="hint" style="margin-top:0">
      ${cooledCount
        ? `場地費總額已包含冷氣：一般場次每人 $${prices.plain}，冷氣場次 ${cooledCount} 場每人 $${prices.cooled}，整季每人 $${seasonFee}。`
        : `$${cost} ÷ ${wizardDates.length} 場 ÷ 人數上限 ${capacity} 人 = 每人每場 $${share}，整季每人 $${seasonFee}。`}
      ${acSurcharge > 0 ? "開季後仍可修改預定冷氣場次。" : ""}
      ${memberCount ? `建立後將記入 ${memberCount} 位成員的帳，可於「帳務 › 季費」收款。` : "尚未選擇成員，可於開季後新增；新增成員不影響其他人的金額。"}
    </p>
    <p class="hint" style="margin-top:6px">
      金額依<b>人數上限</b>計算，之後成員增減只影響該成員本人的帳。
      名單未滿且無臨打補上的場次，實收將少於場地費。
    </p>
  `;
}

function togglePick(id) {
  if (pickedIds.has(id)) pickedIds.delete(id);
  else pickedIds.add(id);
  renderWizard();
}

/** Step 4: what is about to be created, compared with the season it
 * continues. Creating it charges every member at once, so this is a read,
 * not another form.
 */
function renderConfirm() {
  const value = (id) => document.getElementById(id).value.trim();
  const last = creatingNextSeason ? currentSeason : null;
  const cost = Number(value("c-cost")) || 0;
  const capacity = Number(value("c-capacity")) || 0;
  const minRoster = Number(value("c-min-roster")) || 0;
  const acSurcharge = Number(value("c-ac")) || 0;
  const cooled = acSurcharge > 0 ? wizardDates.filter((d) => wizardAcDates.has(d)).length : 0;
  const picked = selectedNames();

  const rows = [];
  const row = (label, shown, note) =>
    rows.push(
      `<div class="set-row"><span class="set-label">${label}` +
        (note ? `<span class="set-sub">${note}</span>` : "") +
        `</span><b>${shown}</b></div>`
    );
  const changed = (now, before, unit = "") =>
    last && String(now) !== String(before) ? `上一季 ${before}${unit}` : last ? "沿用上一季" : "";

  row("場地費總額", `$${cost}`, changed(cost, Number(last ? last.total_venue_cost : 0), ""));
  if (wizardDates.length) {
    const span = `${describeDate(wizardDates[0]).label} – ${describeDate(wizardDates[wizardDates.length - 1]).label}`;
    row("場次", `${wizardDates.length} 場`, span);
  } else {
    row("場次", "尚未安排", "請回上一步產生場次");
  }
  if (acSurcharge > 0) row("冷氣場次", `${cooled} 場`, `每場冷氣費用 $${acSurcharge}`);
  row("人數上限", `${capacity} 人`, changed(capacity, last ? last.capacity : 0, " 人"));
  row("最低人數", `${minRoster} 人`, changed(minRoster, last ? last.minimum_roster : 0, " 人"));
  const startsAt = readTimeValue("c-start");
  const where = [startsAt && `${startsAt}–${readTimeValue("c-end") || ""}`, value("c-location")]
    .filter(Boolean)
    .join(" · ");
  if (where) row("時間地點", escapeHtml(where), "");

  if (last) {
    const before = last.members.map((m) => m.name);
    const added = picked.filter((n) => !before.includes(n));
    const gone = before.filter((n) => !picked.includes(n));
    const note = added.length || gone.length
      ? [added.length ? `新增 ${added.join("、")}` : "", gone.length ? `少了 ${gone.join("、")}` : ""]
          .filter(Boolean)
          .join("；")
      : "與上一季相同";
    row("固定成員", `${picked.length} 人`, escapeHtml(note));
  } else {
    row("固定成員", `${picked.length} 人`, escapeHtml(picked.slice(0, 4).join("、")));
  }

  document.getElementById("confirm-diff").innerHTML = rows.join("");
}

// The re-pricing warning appears only once the cost actually changes.
document.getElementById("f-cost").addEventListener("input", () => {
  const field = document.getElementById("f-cost");
  document.getElementById("cost-warn-note").hidden =
    field.disabled || field.value.trim() === String(originalVenueCost);
});

// Cost is on step 1 but drives the figures on steps 2 and 3.
document.getElementById("c-cost").addEventListener("input", renderWizard);
document.getElementById("c-ac").addEventListener("input", renderWizard);
document.getElementById("c-capacity").addEventListener("input", renderWizard);

async function createSeason() {
  const gameDates = wizardDates;
  const memberNames = selectedNames();

  if (gameDates.length === 0) {
    toast("請至少安排一個場次");
    return;
  }
  if (memberNames.length === 0) {
    toast("請至少選擇一位固定成員");
    return;
  }

  const deadlineValue = document.getElementById("c-deadline").value;
  const payload = {
    total_venue_cost: document.getElementById("c-cost").value.trim() || "0",
    ac_surcharge: document.getElementById("c-ac").value.trim() || "0",
    game_dates: gameDates,
    air_conditioned_dates:
      Number(document.getElementById("c-ac").value) > 0
        ? gameDates.filter((d) => wizardAcDates.has(d))
        : [],
    member_names: memberNames,
    capacity: Number(document.getElementById("c-capacity").value) || 18,
    minimum_roster: Number(document.getElementById("c-min-roster").value) || 0,
    game_start_time: readTimeValue("c-start"),
    game_end_time: readTimeValue("c-end"),
    location: document.getElementById("c-location").value.trim() || null,
    change_deadline_hours: deadlineValue === "" ? 24 : Number(deadlineValue),
  };

  try {
    const season = await postJson(API_BASE, `/clubs/${currentClubId()}/seasons`, payload);
    rememberId("vf_org_season", String(season.id));
    // Straight to collecting the fees just charged.
    toast("已建立本季，請接著收取季費");
    location.href = "organizer-ledger.html";
  } catch (e) {
    toast("建立失敗：" + e.message);
  }
}

const settingsLoad = staleGuard();

/** Loads the season into the form, which stays hidden behind the spinner
 * until then. Read through getJsonSWR so a second visit paints at once.
 */
async function loadSettings(seasonId) {
  const ticket = settingsLoad.take();
  currentSeasonId = seasonId;
  const errorEl = document.getElementById("error");
  errorEl.textContent = "";
  try {
    await getJsonSWR(
      `${API_BASE}/seasons/${seasonId}`,
      (season) => {
        if (!settingsLoad.current(ticket)) return;
        currentSeason = season;
        renderSeasonTab(season);
        renderGames(season);
        renderAcDates(season);
        document.getElementById("page-state").innerHTML = "";
        document.getElementById("settings-form").hidden = creatingNextSeason;
      },
      { headers: authHeader() }
    );
  } catch (e) {
    if (!settingsLoad.current(ticket)) return;
    document.getElementById("page-state").innerHTML = "";
    errorEl.textContent = "連不上伺服器：" + e.message;
  }
}

/** 本季: the season's span, a night's price, the rules, and links to 名單,
 * 季末結算 and the next season. Prices are the server's (`share`, rounded
 * once by pricing.shares_by_game); this only picks and adds them.
 */
function renderSeasonTab(season) {
  const settled = season.settled_at !== null;
  originalVenueCost = season.total_venue_cost;
  document.getElementById("settled-note").hidden = !settled;

  const dates = season.games.map((g) => g.date).sort();
  document.getElementById("season-title").textContent = seasonSpanLabel(dates);
  const weekdays = new Set(dates.map((d) => new Date(d + "T00:00:00").getDay()));
  const weekly = weekdays.size === 1 && dates.length ? `每${describeDate(dates[0]).weekday}・` : "";
  const cancelled = season.games.filter((g) => g.status !== "scheduled").length;
  document.getElementById("season-meta").textContent =
    `${weekly}共 ${dates.length} 場次` + (cancelled ? `，其中 ${cancelled} 場已取消` : "");

  // A night's price with nothing else moving it.
  const typical = (cooled) =>
    season.games.find(
      (g) => g.status === "scheduled" && !!g.air_conditioned === cooled && Number(g.venue_cost_delta || 0) === 0
    );
  const plainGame = typical(false);
  const cooledGame = typical(true);
  const plain = plainGame ? Number(plainGame.share) : Number(season.share_per_game);
  const pricesAc = Number(season.ac_surcharge) > 0;
  const tile = (label, value, cool) =>
    `<div class="st-price${cool ? " cool" : ""}"><div class="st-price-label">${cool ? snowIcon(15) : ""}${label}</div><div class="st-price-value">${
      value == null || Number.isNaN(value) ? "—" : `$${value}`
    }</div></div>`;
  document.getElementById("price-tiles").innerHTML = pricesAc
    ? tile("一般場次", plain, false) +
      tile("冷氣場次", cooledGame ? Number(cooledGame.share) : plain + acExtraEach(season, null), true)
    : tile("每人每場", plain, false);
  const fee = season.games
    .filter((g) => g.status !== "cancelled_refunded")
    .reduce((sum, g) => sum + Number(g.share || 0), 0);
  document.getElementById("season-fee").textContent = `$${fee.toLocaleString("en-US")}`;
  document.getElementById("edit-cost").hidden = settled;
  document.getElementById("cost-locked-note").hidden = !settled;

  const time = season.game_start_time
    ? `${season.game_start_time.slice(0, 5)}–${(season.game_end_time || "").slice(0, 5)}`
    : "未設定";
  const fact = (icon, k, v) =>
    `<div class="st-fact">${factIcon(icon)}<span class="st-fk">${k}</span><span class="st-fv">${v}</span></div>`;
  document.getElementById("rule-facts").innerHTML =
    fact("people", "人數上限", `${season.capacity} 人`) +
    fact("people", "最低人數", `${season.minimum_roster} 人`) +
    fact("clock", "時間", escapeHtml(time)) +
    fact("pin", "地點", escapeHtml(season.location || "未設定")) +
    fact("lock", "更動截止", `開賽前 ${season.change_deadline_hours} 小時`);

  document.getElementById("roster-count").textContent = `${season.members.length} 人`;
  // The same window the ledger uses (see settleBlocker): from three weeks
  // before the last game.
  document.getElementById("settle-row").hidden = settled;
  const last = dates[dates.length - 1];
  let sub = "已開放";
  if (last) {
    const opens = new Date(last + "T00:00:00");
    opens.setDate(opens.getDate() - 21);
    if (opens > new Date()) sub = `${opens.getMonth() + 1}/${opens.getDate()} 開放`;
  }
  document.getElementById("settle-sub").textContent = sub;
}

/** "2026 年 9–12 月", or across a new year "2025 年 12 月–2026 年 2 月". */
function seasonSpanLabel(dates) {
  if (!dates.length) return "尚無場次";
  const a = new Date(dates[0] + "T00:00:00");
  const b = new Date(dates[dates.length - 1] + "T00:00:00");
  if (a.getFullYear() !== b.getFullYear()) {
    return `${a.getFullYear()} 年 ${a.getMonth() + 1} 月–${b.getFullYear()} 年 ${b.getMonth() + 1} 月`;
  }
  const months = a.getMonth() === b.getMonth() ? `${a.getMonth() + 1}` : `${a.getMonth() + 1}–${b.getMonth() + 1}`;
  return `${a.getFullYear()} 年 ${months} 月`;
}

function gameStatusLabel(status) {
  if (status === "cancelled_unrefunded") return "已取消・場地費照算";
  if (status === "cancelled_refunded") return "已取消・場地費不算";
  return "";
}

/* 冷氣場次: correcting the air-conditioning forecast. The total stays and
 * only which nights carry the AC portion moves; the per-game switch on 總覽
 * records what actually ran and moves the total. Games already played are
 * not offered, and the server refuses them too.
 */
let acDates = new Set();
// The season this block shows, so the functions below depend only on what
// renderAcDates was given.
let acSeason = null;

function acItems(season, chosen) {
  const today = todayKey();
  return season.games
    .slice()
    .sort((a, b) => (a.date < b.date ? -1 : 1))
    .map((g) => ({
      iso: g.date,
      on: chosen ? chosen.has(g.date) : !!g.air_conditioned,
      past: g.date < today,
      off: g.status !== "scheduled",
    }));
}

function renderAcDates(season) {
  acSeason = season;
  const block = document.getElementById("ac-block");
  // Nothing to place when the AC is not charged.
  block.hidden = !(Number(season.ac_surcharge) > 0);
  if (block.hidden) return;
  const cooled = season.games.filter((g) => g.air_conditioned && g.status === "scheduled").length;
  document.getElementById("ac-sum").textContent =
    `預定 ${cooled} 場・每場冷氣費用 $${Number(season.ac_surcharge).toLocaleString("en-US")}`;
  document.getElementById("ac-grid").innerHTML = dayGridHtml(acItems(season), { lockPast: true });
  // Only while a game is still to come.
  const today = todayKey();
  const correctable = season.games.some((g) => g.date >= today && g.status === "scheduled");
  document.getElementById("forecast-open").hidden = season.settled_at !== null || !correctable;
}

function openForecastSheet() {
  if (!acSeason) return;
  acDates = new Set(acSeason.games.filter((g) => g.air_conditioned).map((g) => g.date));
  drawForecast();
  openSheet("forecast-backdrop");
}

document.getElementById("forecast-grid").addEventListener("click", (e) => {
  const cell = e.target.closest("[data-fc]");
  if (cell) toggleSeasonAcDate(cell.dataset.fc);
});

function toggleSeasonAcDate(iso) {
  // Local until 儲存.
  if (acDates.has(iso)) acDates.delete(iso);
  else acDates.add(iso);
  drawForecast();
}

const forecastLoad = staleGuard();

/** The grid and what the change would cost, using the server's preview
 * (POST .../air-conditioned-dates/preview) rather than repeating the
 * rounding rules here.
 */
function drawForecast() {
  const season = acSeason;
  document.getElementById("forecast-grid").innerHTML = dayGridHtml(acItems(season, acDates), {
    attr: "data-fc",
    lockPast: true,
  });
  const before = season.games.filter((g) => g.air_conditioned && g.status === "scheduled").length;
  const after = season.games.filter((g) => acDates.has(g.date) && g.status === "scheduled").length;
  const countRow = `<div class="st-fact"><span class="st-fk">冷氣場次</span><span class="st-fv">${
    before === after ? `${after} 場` : `<span class="was">${before}</span> → ${after} 場`
  }</span></div>`;
  const preview = document.getElementById("forecast-preview");
  preview.innerHTML =
    countRow +
    '<div class="st-fact"><span class="st-fk">每人費用</span><span class="st-fv"><i class="spinner sm" aria-hidden="true"></i> 計算中</span></div>';
  const ticket = forecastLoad.take();
  // A read, so plain fetch: postJson would show a saving marker.
  previewAcDates(season.id, [...acDates])
    .then((result) => {
      if (!forecastLoad.current(ticket)) return;
      preview.innerHTML = countRow + forecastFigures(season, result);
    })
    .catch((e) => {
      if (!forecastLoad.current(ticket)) return;
      preview.innerHTML = countRow +
        `<div class="st-fact"><span class="st-fk">每人費用</span><span class="st-fv">${escapeHtml(e.message)}</span></div>`;
    });
}

async function previewAcDates(seasonId, dates) {
  const res = await fetch(`${API_BASE}/seasons/${seasonId}/air-conditioned-dates/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ dates }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(translateApiError(data.detail || String(res.status)));
  return data;
}

/** Before and after for a plain night, a cooled night and the season fee.
 * A plain or cooled night is one with nothing else moving its price.
 */
function forecastFigures(season, result) {
  const byDate = new Map(season.games.map((g) => [g.date, g]));
  const ordinary = result.games.filter((g) => {
    const game = byDate.get(g.date);
    return game && game.status === "scheduled" && Number(game.venue_cost_delta || 0) === 0;
  });
  const pick = (cooled, when) => {
    const g = ordinary.find((x) => (when === "before" ? !!byDate.get(x.date).air_conditioned : x.air_conditioned) === cooled);
    return g ? Number(when === "before" ? g.share_before : g.share_after) : null;
  };
  const change = (a, b) =>
    a == null && b == null
      ? "—"
      : a === b || a == null
        ? `$${b}`
        : b == null
          ? "—"
          : `<span class="was">$${a}</span> → $${b}`;
  const fee = (n) => `$${Number(n).toLocaleString("en-US")}`;
  const feeBefore = Number(result.season_fee_before);
  const feeAfter = Number(result.season_fee_after);
  const row = (k, v) => `<div class="st-fact"><span class="st-fk">${k}</span><span class="st-fv">${v}</span></div>`;
  return (
    row("一般場次每人", change(pick(false, "before"), pick(false, "after"))) +
    row("冷氣場次每人", change(pick(true, "before"), pick(true, "after"))) +
    row("每人季費", feeBefore === feeAfter ? fee(feeAfter) : `<span class="was">${fee(feeBefore)}</span> → ${fee(feeAfter)}`)
  );
}

async function saveAcDates() {
  try {
    await postJson(
      API_BASE,
      `/seasons/${acSeason.id}/air-conditioned-dates`,
      { dates: [...acDates] },
      "PUT"
    );
    closeSheet("forecast-backdrop");
    toast("已更新預定冷氣場次", "good");
    // Re-read: every game's share and the season fee moved.
    await loadSettings(acSeason.id);
  } catch (e) {
    toast("更新失敗：" + e.message);
  }
}

/** One row per game: the date, what makes the night different, its air
 * conditioning and price. Opens the game's sheet.
 */
function gameRowHtml(g, pricesAc) {
  const info = describeDate(g.date);
  const d = new Date(g.date + "T00:00:00");
  const notes = [];
  const status = gameStatusLabel(g.status);
  if (status) notes.push(`<small class="off">${status}</small>`);
  const delta = Number(g.venue_cost_delta || 0);
  if (delta) notes.push(`<small>場地差額 ${delta > 0 ? "+" : "−"}$${Math.abs(delta)}</small>`);
  const own = [g.start_time ? g.start_time.slice(0, 5) : "", g.location || ""].filter(Boolean).join("・");
  if (own) notes.push(`<small class="own">${escapeHtml(own)}</small>`);
  const ac = pricesAc && g.air_conditioned && g.status === "scheduled"
    ? `<span class="ac-tag">${snowIcon(13)}冷氣</span>`
    : "";
  const price = g.status === "cancelled_refunded" ? "—" : `$${g.share}`;
  return `<button type="button" class="lrow" onclick="openGameSheet(${g.id})"><span class="st-gdate"><b>${d.getMonth() + 1}/${d.getDate()}<span>${info.weekday}</span></b>${notes.join("")}</span>${ac}<span class="st-price-cell">${price}</span>${LIST_GO}</button>`;
}

function renderGames(season) {
  const today = todayKey();
  const sorted = season.games.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
  const upcoming = sorted.filter((g) => g.date >= today);
  const past = sorted.filter((g) => g.date < today);
  const pricesAc = Number(season.ac_surcharge) > 0;

  document.getElementById("upcoming-games").innerHTML =
    upcoming.map((g) => gameRowHtml(g, pricesAc)).join("") ||
    '<div class="lrow"><span class="lrow-k">尚無待進行場次</span></div>';

  const pastToggle = document.getElementById("past-games-toggle");
  const pastList = document.getElementById("past-games");
  if (past.length === 0) {
    pastToggle.hidden = true;
    pastList.hidden = true;
    return;
  }
  pastToggle.hidden = false;
  pastToggle.innerHTML = `已結束場次（${past.length}）<span class="car">▾</span>`;
  pastList.innerHTML = past.map((g) => gameRowHtml(g, pricesAc)).join("");
  pastToggle.setAttribute("aria-expanded", String(!pastList.hidden));
  pastToggle.onclick = () => {
    pastList.hidden = !pastList.hidden;
    pastToggle.setAttribute("aria-expanded", String(!pastList.hidden));
  };
}

let dateSheetGameId = null;

/** One game: its price and air conditioning, then what may still be done.
 * A game to come can be moved, re-timed, given its own venue or venue
 * cost, or called off; a game already played can only be called off; a
 * cancelled game, or any game of a settled season, is read only.
 */
function openGameSheet(gameId) {
  const game = currentSeason.games.find((g) => g.id === gameId);
  if (!game) return;
  const settled = currentSeason.settled_at !== null;
  const scheduled = game.status === "scheduled";
  const past = game.date < todayKey();
  const editable = !settled && scheduled && !past;
  const cancellable = !settled && scheduled;
  dateSheetGameId = gameId;

  document.getElementById("date-sheet-title").textContent = `${describeDate(game.date).label} 場次`;
  const rows = [];
  const infoRow = (k, v, extra) =>
    `<div class="lrow"><span class="lrow-k">${k}</span><span class="lrow-v">${v}</span>${extra || ""}</div>`;
  if (Number(currentSeason.ac_surcharge) > 0 && scheduled) {
    const forecastable = editable;
    rows.push(
      infoRow(
        "冷氣",
        past ? (game.air_conditioned ? "開啟" : "未開啟") : game.air_conditioned ? "預定開啟" : "預定不開啟",
        forecastable
          ? '<button type="button" class="st-edit" onclick="closeDateSheet(); openForecastSheet()">修改預定</button>'
          : ""
      )
    );
  }
  rows.push(infoRow("每人費用", game.status === "cancelled_refunded" ? "不計費" : `$${game.share}`));
  const status = gameStatusLabel(game.status);
  if (status) rows.push(infoRow("狀態", status));
  document.getElementById("game-sheet-info").innerHTML = rows.join("");

  const note = document.getElementById("game-sheet-note");
  note.textContent = settled
    ? "本季已結算，場次無法修改。"
    : !scheduled
      ? ""
      : past
        ? "已結束的場次無法修改日期、時間與地點。"
        : "";
  note.hidden = !note.textContent;
  document.getElementById("game-edit-fields").hidden = !editable;
  document.getElementById("game-save").hidden = !editable;
  document.getElementById("game-cancel-zone").hidden = !cancellable;

  if (editable) fillGameFields(game);
  document.getElementById("date-sheet-backdrop").hidden = false;
}

function cancelFromGameSheet() {
  const id = dateSheetGameId;
  closeDateSheet();
  openCancelSheet(id);
}

/** Moves a night rather than calling it off and booking another: no money
 * moves, and everything recorded against the game moves with it. See
 * update_game in routes/games.py for what is refused.
 */
let originalDelta = 0;

function fillGameFields(game) {
  originalDelta = Number(game.venue_cost_delta || 0);
  const cost = document.getElementById("g-cost-delta");
  cost.value = originalDelta === 0 ? "" : String(originalDelta);
  document.getElementById("g-cost-hint").textContent =
    `本場目前每人 $${game.share}。場地費較高時填入差額，較低時填負數；留空表示與平常相同。`;
  document.getElementById("g-cost-warn").hidden = true;
  // Assigned rather than added: the sheet is reopened for every game.
  cost.oninput = () => {
    const now = Number(cost.value || 0);
    const warn = document.getElementById("g-cost-warn");
    if (now === originalDelta) {
      warn.hidden = true;
      return;
    }
    // Shows how the season total moves, not a new per-person price:
    // rounding happens only on the server.
    const moved = now - originalDelta;
    const total = Number(currentSeason.total_venue_cost) + moved;
    warn.hidden = false;
    warn.textContent =
      `場地費總額將從 $${currentSeason.total_venue_cost} 變為 $${total}。` +
      `僅本場的每人金額變動，其他場次不受影響；` +
      `每人帳上將補記差額，已收款項不變。`;
  };
  document.getElementById("date-sheet-input").value = game.date;
  writeTimeValue("g-start", game.start_time);
  writeTimeValue("g-end", game.end_time);
  const loc = document.getElementById("g-location");
  loc.value = game.location || "";
  // Empty means the season's venue, so that is the placeholder.
  loc.placeholder = currentSeason.location || "例如：晴光館";
  document.getElementById("g-location-hint").textContent = currentSeason.location
    ? `留空表示與本季相同：${currentSeason.location}`
    : "留空表示與本季相同";
}

function closeDateSheet() {
  document.getElementById("date-sheet-backdrop").hidden = true;
}

async function saveGameEdit() {
  const value = document.getElementById("date-sheet-input").value;
  if (!value) {
    toast("請選擇日期");
    return;
  }
  // Every field is sent: null means "back to the season's", which differs
  // from leaving a field out. See GameUpdate in schemas.py.
  const cost = document.getElementById("g-cost-delta").value.trim();
  const payload = {
    date: value,
    location: document.getElementById("g-location").value.trim() || null,
    start_time: readTimeValue("g-start"),
    end_time: readTimeValue("g-end"),
    // Empty means zero; sent as typed so decimals keep their precision.
    venue_cost_delta: cost === "" ? null : cost,
  };
  // postJson already marks the pressed control and translates errors.
  try {
    await postJson(API_BASE, `/games/${dateSheetGameId}`, payload, "PATCH");
    closeDateSheet();
    toast("已更新本場次設定", "good");
    await loadSettings(currentSeasonId);
  } catch (e) {
    toast(e.message);
  }
}

function openCancelSheet(gameId) {
  const game = currentSeason.games.find((g) => g.id === gameId);
  if (!game) return;
  cancelGameId = gameId;
  cancelRefunded = true;
  document.getElementById("cancel-game-title").textContent = `取消 ${describeDate(game.date).label} 場次`;
  document.getElementById("cancel-current-share").textContent = `$${game.share}`;
  document.querySelectorAll("#cancel-game-backdrop .choice-card").forEach((card) => {
    card.classList.toggle("on", card.dataset.refunded === "true");
  });
  document.getElementById("cancel-game-backdrop").hidden = false;
}
function closeCancelSheet() {
  document.getElementById("cancel-game-backdrop").hidden = true;
}
document.querySelectorAll("#cancel-game-backdrop .choice-card").forEach((card) => {
  card.addEventListener("click", () => {
    cancelRefunded = card.dataset.refunded === "true";
    document.querySelectorAll("#cancel-game-backdrop .choice-card").forEach((c) => c.classList.toggle("on", c === card));
  });
});

async function confirmCancelGame() {
  try {
    await postJson(API_BASE, `/games/${cancelGameId}/cancel`, { refunded: cancelRefunded });
    closeCancelSheet();
    toast("已取消本場次", "good");
    loadSettings(currentSeasonId);
  } catch (e) {
    toast("取消失敗：" + e.message);
  }
}

async function deleteSeason() {
  if (!currentSeason) return;
  const label = `${currentSeason.games.length} 場、${currentSeason.members.length} 位成員`;
  if (!confirm(`確定要刪除本季嗎？

${label} 及已記入每人帳上的季費將一併刪除，無法復原。`)) {
    return;
  }
  try {
    await deleteJson(API_BASE, `/seasons/${currentSeasonId}`);
    forgetId("vf_org_season");
    toast("已刪除本季");
    location.reload();
  } catch (e) {
    toast("刪除失敗：" + e.message);
  }
}

let loadedClubName = "";

function fillClubName() {
  getJsonSWR(
    `${API_BASE}/clubs/${currentClubId()}`,
    (club) => {
      loadedClubName = club.name;
      document.getElementById("club-name-value").textContent = club.name;
      const input = document.getElementById("club-name");
      if (document.activeElement !== input) input.value = club.name;
      updateClubNameRow();
    },
    { headers: authHeader() }
  ).catch(() => {});
}

const clubNameInput = document.getElementById("club-name");
clubNameInput.addEventListener("input", updateClubNameRow);

// 儲存 is enabled, and the remaining length shown, only once the name
// changes.
function updateClubNameRow() {
  const value = clubNameInput.value.trim();
  const changed = value.length > 0 && value !== loadedClubName;
  document.getElementById("club-name-save").disabled = !changed;
  document.getElementById("club-name-hint").textContent = changed
    ? `尚可輸入 ${CLUB_NAME_MAX - clubNameInput.value.length} 字`
    : "顯示於每位成員畫面的左上角，最多 20 字。";
}

function openClubSheet() {
  clubNameInput.value = loadedClubName;
  updateClubNameRow();
  openSheet("club-backdrop");
  clubNameInput.focus();
}

async function renameClub(btn) {
  const name = document.getElementById("club-name").value.trim();
  if (!name) {
    toast("球隊名稱不可空白");
    return;
  }
  if (name.length > CLUB_NAME_MAX) {
    toast(`球隊名稱最多 ${CLUB_NAME_MAX} 個字`);
    return;
  }
  const done = markBusy(btn);
  try {
    const club = await postJson(API_BASE, `/clubs/${currentClubId()}`, { name }, "PATCH");
    const picker = document.getElementById("club-picker");
    const option = picker.options[picker.selectedIndex];
    if (option && option.textContent === loadedClubName) option.textContent = club.name;
    loadedClubName = club.name;
    document.getElementById("club-name").value = club.name;
    document.getElementById("club-name-value").textContent = club.name;
    updateClubNameRow();
    closeSheet("club-backdrop");
    toast("已更新球隊名稱", "good");
  } catch (e) {
    toast("改名失敗：" + e.message);
  } finally {
    done();
  }
}

async function deleteClub() {
  const warning =
    "確定要刪除整個球隊嗎？\n\n" +
    "所有季別、場次與帳務紀錄將一併刪除，無法復原。\n" +
    "成員的 LINE 帳號不受影響，僅不再屬於此球隊。";
  if (!confirm(warning)) {
    return;
  }
  try {
    await deleteJson(API_BASE, `/clubs/${currentClubId()}`);
    forgetId(CLUB_STORAGE_KEY);
    forgetId("vf_org_season");
    toast("已刪除這個球隊");
    location.href = "member.html";
  } catch (e) {
    toast("刪除失敗：" + e.message);
  }
}

function goToSettlement() {
  rememberId("vf_org_season", String(currentSeasonId));
  location.href = "organizer-ledger.html#settle";
}

function openCostSheet() {
  const s = currentSeason;
  if (!s || s.settled_at !== null) return;
  document.getElementById("f-cost").value = s.total_venue_cost;
  document.getElementById("f-ac").value = s.ac_surcharge;
  // Hidden until the figure changes; see the f-cost listener.
  document.getElementById("cost-warn-note").hidden = true;
  openSheet("cost-backdrop");
}

function openRulesSheet() {
  const s = currentSeason;
  if (!s) return;
  document.getElementById("f-capacity").value = s.capacity;
  document.getElementById("f-min-roster").value = s.minimum_roster;
  writeTimeValue("f-start", s.game_start_time);
  writeTimeValue("f-end", s.game_end_time);
  document.getElementById("f-location").value = s.location || "";
  document.getElementById("f-deadline").value = s.change_deadline_hours;
  openSheet("rules-backdrop");
}

/** Saves the venue bill and the air-conditioning surcharge; both price
 * every game, so they are locked once settled, as on the server.
 */
async function saveCost() {
  const newCost = document.getElementById("f-cost").value.trim();
  if (newCost !== String(originalVenueCost)) {
    if (!confirm("修改場地費總額將重新計算整季每人每場金額（含已結束的場次）。確定要儲存嗎？")) {
      return;
    }
  }
  const payload = {
    total_venue_cost: newCost,
    ac_surcharge: document.getElementById("f-ac").value.trim() || "0",
  };
  try {
    await postJson(API_BASE, `/seasons/${currentSeasonId}`, payload, "PATCH");
    closeSheet("cost-backdrop");
    toast("已更新費用", "good");
    loadSettings(currentSeasonId);
  } catch (e) {
    toast("儲存失敗：" + e.message);
  }
}

/** Saves capacity, times, venue and the change deadline. Capacity
 * re-prices the season, so the server refuses changing it once settled;
 * the rest stays editable.
 */
async function saveRules() {
  const deadlineValue = document.getElementById("f-deadline").value;
  const payload = {
    capacity: Number(document.getElementById("f-capacity").value),
    minimum_roster: Number(document.getElementById("f-min-roster").value),
    game_start_time: readTimeValue("f-start"),
    game_end_time: readTimeValue("f-end"),
    location: document.getElementById("f-location").value.trim() || null,
    change_deadline_hours: deadlineValue === "" ? 24 : Number(deadlineValue),
  };
  try {
    await postJson(API_BASE, `/seasons/${currentSeasonId}`, payload, "PATCH");
    closeSheet("rules-backdrop");
    toast("已更新場地與規則", "good");
    loadSettings(currentSeasonId);
  } catch (e) {
    toast("儲存失敗：" + e.message);
  }
}
