// The game sheet: its roster tabs, the person picker, and roster changes
// shown before the server confirms them.

/** The roster's three groups in tab order, playing first. */
const TAB_ORDER = [
  { key: "attending", label: "出席" },
  { key: "absent", label: "請假" },
  { key: "queued", label: "候補" },
];

/** A two-stage cue for a roster move. The sheet stays on the tab the
 * person chose, so a row moving to another tab could go unnoticed: the
 * first render marks the destination tab busy, and the server's answer
 * paints a completion cue with the success message. Each cue is shown
 * once.
 */
const _gameDetailChanges = new WeakMap();
const _activeGameDetailChanges = new WeakMap();
let _gameDetailChangeSequence = 0;

function rosterChangeSurface(container) {
  if (typeof document === "undefined") return null;
  return document.getElementById("hero-wrap") || container;
}

function setRosterChangeSurfacePhase(container, phase, token) {
  const surface = rosterChangeSurface(container);
  if (!surface || !surface.classList) return;
  surface.classList.remove("roster-surface-pending", "roster-surface-complete");
  if (!phase) {
    delete surface.dataset.rosterChangeToken;
    return;
  }
  surface.dataset.rosterChangeToken = String(token);
  surface.classList.add(`roster-surface-${phase}`);
  if (phase === "complete") {
    setTimeout(() => {
      if (surface.dataset.rosterChangeToken !== String(token)) return;
      surface.classList.remove("roster-surface-complete");
      delete surface.dataset.rosterChangeToken;
    }, 700);
  }
}

/** Starts or refines one visible roster move. The returned token ties the
 * server's answer to this tap, so an older response cannot overwrite a
 * newer tap's cue.
 */
function markGameDetailChange(container, tab, names, token) {
  if (!container || !TAB_ORDER.some((item) => item.key === tab)) return;
  const active = _activeGameDetailChanges.get(container);
  const changeToken = token && active && active.token === token
    ? token
    : ++_gameDetailChangeSequence;
  const change = {
    token: changeToken,
    tab,
    names: new Set((names || []).filter(Boolean)),
    phase: "pending",
  };
  _activeGameDetailChanges.set(container, change);
  _gameDetailChanges.set(container, change);
  setRosterChangeSurfacePhase(container, "pending", changeToken);
  return changeToken;
}

/** Turns the pending move into its completion cue; responses for an older
 * tap are ignored.
 */
function completeGameDetailChange(container, token) {
  const active = container && _activeGameDetailChanges.get(container);
  if (!active || active.token !== token) return false;
  const change = { ...active, phase: "complete" };
  _activeGameDetailChanges.delete(container);
  _gameDetailChanges.set(container, change);
  setRosterChangeSurfacePhase(container, "complete", token);
  return true;
}

function clearGameDetailChange(container, token) {
  const active = container && _activeGameDetailChanges.get(container);
  if (!active || active.token !== token) return false;
  _activeGameDetailChanges.delete(container);
  _gameDetailChanges.delete(container);
  setRosterChangeSurfacePhase(container, null, token);
  return true;
}

/** The control that opens a roster row: a large chevron that becomes ✕
 * while open. Drawn, because the › glyph sits high in CJK fonts and reads
 * as punctuation.
 */
const ROW_TOGGLE =
  '<button type="button" class="att-go" data-row-toggle aria-expanded="false" aria-label="操作">' +
  '<svg class="i-open" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
  '<svg class="i-close" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>' +
  "</button>";

/** Pickers a page may open from outside a rendered sheet (the card's 請假
 * opens 請假並指定代打). Set on every render, keyed by container.
 */
const _gameDetailOpeners = new WeakMap();

function openAbsenceWithSubstitute(container, name) {
  const openers = _gameDetailOpeners.get(container);
  return !!(openers && openers.absentWithSub(name));
}

/** An empty group says so; a blank panel looks like a failed load. */
function emptyPanel(text) {
  return `<div class="empty gd-empty">${text}</div>`;
}

let _requestQueue = Promise.resolve();

/** Sends changes one at a time in the order they were tapped, without
 * making the screen wait: the screen updates optimistically, and only the
 * requests are serialized. A change needing an id from an earlier request
 * resolves it in its own turn. A failure does not stall the queue; that
 * change rolls back its own update.
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

/** Runs a change on the screen first and the server second.
 * `mutateLocally` edits the page's copy in the same frame, `request` is
 * queued behind changes in flight, and `reconcile` folds the server's ids
 * into what is drawn. On failure the snapshot is restored and the reason
 * shown.
 */
function optimisticRunner({ getState, setState, repaint }) {
  return async function optimistically(mutateLocally, request, failureMessage, reconcile) {
    const snapshot = JSON.parse(JSON.stringify(getState()));
    mutateLocally(getState());
    // Any read already in flight is now out of date; see localRevision.
    bumpLocalRevision();
    repaint();
    try {
      const result = await enqueueRequest(request);
      if (reconcile) {
        reconcile(result, getState());
        // Repaint so handlers bound to the placeholder id get the real
        // one.
        repaint();
      }
      return result;
    } catch (e) {
      setState(snapshot);
      bumpLocalRevision();
      repaint();
      toast(failureMessage + e.message);
      return null;
    }
  };
}

/** Whether a finished write needs the season re-read. Recording an absence
 * answers with `promoted_from_waitlist` and cancelling one with
 * `released_player_id`; when both are null the screen is already right. A
 * failed write always refreshes.
 */
function needsRefreshAfter(result) {
  if (!result) return true; // the write failed; make sure of the truth
  return Boolean(result.promoted_from_waitlist || result.released_player_id);
}

/** Wraps a "re-read everything" function so it can never undo a change: it
 * runs through the same queue as the writes, so it never reads before a
 * queued tap is sent, and only one is ever waiting, so several quick taps
 * cause one re-read.
 */
function coalescedRefresh(load) {
  let busy = false;
  return function refresh() {
    // One already queued or running covers this request: the queue is
    // serial, so a running refresh started after every finished change.
    if (busy) return;
    busy = true;
    enqueueRequest(async () => {
      try {
        await load();
      } catch (e) {
        // A failed background read changes nothing visible.
        console.warn("Background refresh failed:", e);
      } finally {
        busy = false;
      }
    });
  };
}

/** Takes a drop-in off the local copy, and off the absence that named
 * them, which becomes a gap again.
 */
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

/** Draws a server-confirmed substitute without another season read: the
 * new signup and, on a full court, who went back to the queue. A quiet
 * refresh may follow to recover details the response does not include.
 */
function applySubstituteLocally(
  season,
  gameId,
  absenceId,
  result,
  { name, gender }
) {
  const game = season.games.find((item) => item.id === gameId);
  if (!game) return { displacedName: null };
  const absence = game.absences.find((item) => item.id === absenceId);
  if (!absence) return { displacedName: null };

  game.confirmed_drop_ins = game.confirmed_drop_ins.filter(
    (dropIn) => dropIn.covering !== absence.player_name
  );

  let displaced = null;
  if (result && result.displaced_player_id) {
    displaced = game.confirmed_drop_ins.find(
      (dropIn) => dropIn.player_id === result.displaced_player_id
    );
    if (displaced) {
      game.confirmed_drop_ins = game.confirmed_drop_ins.filter(
        (dropIn) => dropIn !== displaced
      );
      game.waitlist_entries.unshift({
        id: -1,
        player_id: displaced.player_id,
        player_name: displaced.player_name,
        gender: displaced.gender || null,
        signed_up_by_me: displaced.signed_up_by_me === true,
      });
    }
  }

  const queued = game.waitlist_entries.find(
    (entry) =>
      (result && entry.player_id === result.player_id) || entry.player_name === name
  );
  game.waitlist_entries = game.waitlist_entries.filter((entry) => entry !== queued);
  game.confirmed_drop_ins.push({
    id: result.id,
    player_id: result.player_id,
    player_name: name,
    gender: gender || (queued && queued.gender) || null,
    covering: absence.player_name,
    linked: queued ? queued.linked : false,
    signed_up_by_me: true,
  });
  absence.covered_by = name;
  absence.filled_by = name;
  return { displacedName: displaced ? displaced.player_name : null };
}

/** Draws 請假並指定代打 once the server answers: the absence and the substitute
 * in its slot. Nobody comes off the queue.
 */
function applyAbsenceWithSubstituteLocally(season, gameId, absentName, result, person) {
  const game = season.games.find((item) => item.id === gameId);
  if (!game || !result || !result.substitute) return { displacedName: null };
  game.absences.push({
    id: result.id,
    player_name: absentName,
    covered_by: null,
    filled_by: null,
  });
  return applySubstituteLocally(season, gameId, result.id, result.substitute, person);
}

/** Draws an organizer's queue promotion; on a full court the replaced
 * person moves to the queue in the same frame.
 */
function promoteWaitlistLocally(season, gameId, entryId, replacingDropInId, result) {
  const game = season.games.find((item) => item.id === gameId);
  if (!game) return null;
  const entry = game.waitlist_entries.find((item) => item.id === entryId);
  if (!entry) return null;

  game.waitlist_entries = game.waitlist_entries.filter((item) => item !== entry);
  let replaced = null;
  if (replacingDropInId) {
    replaced = game.confirmed_drop_ins.find((item) => item.id === replacingDropInId);
    game.confirmed_drop_ins = game.confirmed_drop_ins.filter(
      (item) => item.id !== replacingDropInId
    );
    if (replaced) {
      game.waitlist_entries.unshift({
        id: -1,
        player_id: replaced.player_id,
        player_name: replaced.player_name,
        gender: replaced.gender || null,
        signed_up_by_me: replaced.signed_up_by_me === true,
      });
    }
  }

  game.confirmed_drop_ins.push({
    id: result.drop_in_id,
    player_id: result.player_id,
    player_name: entry.player_name,
    gender: entry.gender || null,
    covering: null,
    linked: entry.linked,
    signed_up_by_me: entry.signed_up_by_me === true,
  });
  return { promoted: entry.player_name, replaced: replaced && replaced.player_name };
}

/** Draws an automatic promotion from the queue. The server returns the
 * promoted player but not the new row id, so the row keeps a placeholder
 * until the quiet refresh.
 *
 * `fallback` covers a substitute who came from the queue: cancelling
 * gives their place back, and they may be first in line again.
 */
function applyAutomaticPromotionLocally(season, gameId, playerId, fallback) {
  if (!playerId) return null;
  const game = season.games.find((item) => item.id === gameId);
  if (!game) return null;
  const queued = game.waitlist_entries.find((item) => item.player_id === playerId);
  const person = queued || (fallback && fallback.player_id === playerId ? fallback : null);
  if (!person) return null;

  if (queued) {
    game.waitlist_entries = game.waitlist_entries.filter((item) => item !== queued);
  }
  if (!game.confirmed_drop_ins.some((item) => item.player_id === playerId)) {
    game.confirmed_drop_ins.push({
      id: -1,
      player_id: playerId,
      player_name: person.player_name,
      gender: person.gender || null,
      covering: null,
      linked: person.linked,
      signed_up_by_me: person.signed_up_by_me === true,
    });
  }
  return person.player_name;
}

/** The people in a picker under a heading per group (候補 / 球隊成員 / 訪客).
 * Anybody who cannot be chosen is still shown, greyed with the reason, so
 * nobody seems lost. `attrs(c)` makes a row choosable; a row with a
 * `disabled` reason gets none.
 */
function pickGroupsHtml(candidates, attrs) {
  const groups = [];
  for (const c of candidates) {
    const name = c.group || "";
    let group = groups.find((g) => g.name === name);
    if (!group) groups.push((group = { name, rows: [] }));
    group.rows.push(c);
  }
  return groups
    .map(
      (g) =>
        (g.name ? `<div class="pick-group">${escapeHtml(g.name)}</div>` : "") +
        `<div class="sub-pick">${g.rows
          .map(
            (c) => `
      <div class="pick-row${c.disabled ? " is-disabled" : ""}" ${c.disabled ? 'aria-disabled="true"' : attrs(c)}>
        <i class="radio"></i>${avatarHtml(c.name, c.avatar_url)}
        <span class="pk-name">${escapeHtml(c.name)}</span>${genderTag(c.gender)}
        <span class="pk-note">${escapeHtml(c.disabled || c.note || "")}</span>
      </div>`
          )
          .join("")}</div>`
    )
    .join("");
}

/** The one panel for choosing a person (a 代打, people to sign up): the
 * people you already know as taps, and a text field for anyone else.
 *
 * `candidates` are `{ id, name, gender, note }`. Picking keeps `id`, so a
 * regular stays one person; a typed name always means somebody new,
 * because two people can share a name.
 */
function renderPersonPicker(container, { title, candidates, hint }) {
  const list = candidates || [];
  const grouped = list.some((c) => c.group);
  const rows = list.length
    ? pickGroupsHtml(
        list,
        (c) => `data-person-pick
        data-person-id="${c.id === null || c.id === undefined ? "" : c.id}"
        data-person-name="${escapeHtml(c.name)}"
        data-person-gender="${c.gender || ""}"`
      )
    : "";

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
            ? `${grouped ? "" : '<div class="or-line"><span>曾報名過的對象</span></div>'}
               ${rows}
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

/** Opens the picker and resolves with the person chosen, or null if
 * closed.
 */
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

/** Shows one of the sheet's three groups, from the tab strip or from
 * anything that needs to send the reader to one.
 */
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

/** Every control on the sheet goes through one of these callbacks, so
 * withholding them makes the sheet read-only in one place.
 */
const GAME_SHEET_ACTIONS = [
  "onAddDropIn",
  "onAssignSubstitute",
  "onCancelAbsence",
  "onCancelSubstitute",
  "onLeaveWaitlist",
  "onPromoteFromWaitlist",
  "onRecordAbsence",
  "onRemoveDropIn",
];

/** One game's sheet: the game card, then the roster in three tabs
 * (playing, away, queued) with the controls for each row.
 *
 * `options`:
 *   extraHtml - a page's own content, shown at the end.
 *   heroMetaPills - extra `<span class="meta-pill">` HTML for the card.
 *   clubMembers - [{id, name, gender}], the people a substitute can be picked from; typing a name still works.
 *   viewerName - the viewer's name, to mark "(你)" on their queue row.
 *   onAssignSubstitute(absenceId, name, gender, button) - offers 指定代打 on an absence, even after the deadline (the slot stays filled either way).
 *   onCancelSubstitute(dropInId) - offers 取消代打, before the deadline only.
 *   canAssignSubstitute(absence) - which absences get those controls (default all; member.html limits it to the viewer's own).
 */
function renderGameDetail(container, season, game, options) {
  const change = _gameDetailChanges.get(container) || null;
  _gameDetailChanges.delete(container);
  const given = options || {};
  // A settled season is closed to everybody, the organizer included, so
  // its controls are removed rather than offered and refused.
  const closed = !!season.settled_at;
  const opts = closed
    ? Object.fromEntries(
        Object.entries(given).filter(([key]) => !GAME_SHEET_ACTIONS.includes(key))
      )
    : given;
  const extraHtml = opts.extraHtml || "";
  const clubMembers = opts.clubMembers || [];
  // The member screen holds everybody to the deadline, the organizer too
  // (`locked` is never true for them); the management screen omits
  // asMember.
  const locked = game.locked || (opts.asMember === true && game.past_deadline === true);
  // Nothing that changes who is playing is offered once the night is
  // locked or the season settled.
  const frozen = locked || !!season.settled_at;
  const viewerName = opts.viewerName || "";
  const onAssignSubstitute = opts.onAssignSubstitute;
  const onCancelSubstitute = opts.onCancelSubstitute;
  const canAssignSubstitute = opts.canAssignSubstitute || (() => true);

  function initial(name) {
    return escapeHtml(firstGrapheme(name));
  }

  // Who can be put on this game, grouped, with a reason for anyone who
  // cannot; shared by the 代打 and 新增臨打 pickers. 球隊成員 have their own LINE
  // account; 訪客 were typed in by somebody. The whole club is offered only
  // on the management screen: the organizer may put anybody on, a member
  // only somebody without an account.
  const fixedNames = new Set(season.members.map((m) => m.name));
  const onCourtNames = new Set(
    season.members
      .filter((m) => !game.absences.some((a) => a.player_name === m.name))
      .map((m) => m.name)
      .concat(game.confirmed_drop_ins.map((d) => d.player_name))
  );
  const queuedNames = new Set(game.waitlist_entries.map((w) => w.player_name));
  const wholeClub = opts.pickFromWholeClub === true;

  function peopleToPick(skipName, queuedAreChoosable) {
    const out = [];
    const seen = new Set(skipName ? [skipName] : []);
    const add = (person, group, note) => {
      const name = person.player_name || person.name;
      if (seen.has(name)) return;
      seen.add(name);
      let disabled = null;
      if (fixedNames.has(name)) disabled = "本季固定成員";
      else if (onCourtNames.has(name)) disabled = "已在出席名單";
      else if (!queuedAreChoosable && queuedNames.has(name)) disabled = "候補中";
      else if (person.linked && !wholeClub) disabled = "需本人報名";
      out.push({
        id: person.id != null ? person.id : null,
        name,
        gender: person.gender,
        avatar_url: person.avatar_url || null,
        note,
        group,
        disabled,
      });
    };
    if (queuedAreChoosable) for (const w of game.waitlist_entries) add(w, "候補", "候補中");
    if (wholeClub) for (const m of clubMembers) if (m.linked) add(m, "球隊成員", "");
    for (const g of opts.myGuests || []) {
      add(g, g.linked ? "球隊成員" : "訪客", g.times ? `報名過 ${g.times} 次` : "");
    }
    for (const m of clubMembers) if (!m.linked) add(m, "訪客", "");
    // Choosable first within each group, greyed after.
    const order = ["候補", "球隊成員", "訪客"];
    return out.sort(
      (a, b) =>
        order.indexOf(a.group) - order.indexOf(b.group) ||
        Number(!!a.disabled) - Number(!!b.disabled)
    );
  }

  /** Who this member may name as their 代打: people queued for this game and
   * people they have brought before. Anyone already on court for this
   * game is shown greyed.
   */
  function substituteCandidates(absence) {
    // Somebody queued can be named: their signup moves onto the court.
    return peopleToPick(absence.player_name, true);
  }

  /** `combined` is 請假並指定代打 for somebody still attending: no absence exists
   * yet, so the form is keyed "new".
   */
  function substituteForm(absence, combined = false, cancelFor = null) {
    const candidates = substituteCandidates(absence);
    const covering = game.confirmed_drop_ins.find((d) => d.covering === absence.player_name);
    const pickRows = pickGroupsHtml(
      candidates,
      (m) =>
        `data-pick="${absence.id}" data-pick-name="${escapeHtml(m.name)}" data-pick-gender="${m.gender || ""}"`
    );
    const maleSelected = covering && covering.gender === "male" ? " selected" : "";
    const femaleSelected = covering && covering.gender === "female" ? " selected" : "";
    // A layer of its own over the sheet, with a title naming whose slot
    // is being filled.
    return `
      <div class="picker-backdrop" data-sub-form="${absence.id}" hidden>
        <div class="picker">
          <div class="picker-head">
            <span>${
              combined
                ? `${escapeHtml(absence.player_name)} 請假並指定代打`
                : `指定 ${escapeHtml(absence.player_name)} 的代打`
            }</span>
            <button type="button" class="picker-close" data-close-sub="${absence.id}" aria-label="關閉">✕</button>
          </div>
          ${
            candidates.length
              ? `${pickRows}
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
          <button type="button" class="btn btn-primary" data-confirm-sub="${absence.id}"${
            combined ? ` data-confirm-absent="${escapeHtml(absence.player_name)}"` : ""
          }>${combined ? "確認請假與代打" : "確認指定"}</button>
          ${
            // Taking the substitute off without naming another.
            cancelFor
              ? `<button type="button" class="btn btn-danger picker-cancel-sub" data-cancel-sub="${cancelFor.id}">取消代打</button>`
              : ""
          }
        </div>
      </div>
    `;
  }

  // Who is actually playing, worked out by the app: one numbered list of
  // the people on court, and a separate list of who is away and whether
  // anyone fills their slot.

  /** Every person on the sheet, in every list, is this row, with fixed
   * slots: position, avatar, name, and a right-hand side for any note and
   * controls. An empty side takes no space, so all rows have the same
   * shape.
   */
  function rosterRow({ num, person, name, tone, note, actions, changed = false }) {
    // A row with actions opens in place onto at most two buttons, which
    // carry the same data attributes the click routing below expects.
    const acts = actions && actions.length ? actions : null;
    // The note (代打 / 缺額 / X 報名) goes inside the name as an inline
    // element, so its padding does not change the row's height. Only the
    // person's own name (att-who) may be truncated, so a long name cannot
    // hide the note.
    return `
      <div class="att-row${tone ? " " + tone : ""}${acts ? " has-acts" : ""}${
        changed ? ` just-changed roster-change-${changed}${changed === "pending" ? " roster-row-entering" : ""}` : ""
      }"${acts ? " data-row-acts" : ""}>
        <span class="att-num">${num}</span>
        ${avatarHtml(name, person && person.avatar_url)}
        <span class="att-name"><span class="att-who">${escapeHtml(name)}</span>${genderTag(
          person ? person.gender : null
        )}${note || ""}</span>
        <span class="roster-note">${acts ? ROW_TOGGLE : ""}</span>${
          acts
            ? `<span class="row-acts">${acts
                .map(
                  (a) =>
                    `<button type="button" class="row-act ${a.tone || "primary"}" data-act ${a.attrs}>${a.label}</button>`
                )
                .join("")}</span>`
            : ""
        }
      </div>`;
  }

  const absentByName = {};
  for (const a of game.absences) absentByName[a.player_name] = a;
  const canEdit = !!opts.onRecordAbsence && !frozen;
  const canRecordAbsence = opts.canRecordAbsence || (() => true);
  // Taking leave and naming who covers, in one step: the slot goes
  // straight to the substitute, so nobody is promoted from the queue and
  // bumped back.
  const offerAbsentWithSub =
    canEdit && !!opts.onRecordAbsenceWithSubstitute && !!onAssignSubstitute;

  const attendingRows = [];
  for (const m of season.members) {
    if (absentByName[m.name]) continue;
    const mayLeave = canEdit && canRecordAbsence(m);
    attendingRows.push({
      person: m,
      name: m.name,
      tone: "",
      note: "",
      changed:
        change && change.tab === "attending" && change.names.has(m.name)
          ? change.phase
          : false,
      // 指定代打 on somebody still playing is taking leave with a substitute
      // named.
      actions: mayLeave
        ? [{ label: "請假", tone: "danger", attrs: `data-mark-absent="${escapeHtml(m.name)}"` }].concat(
            offerAbsentWithSub
              ? [{ label: "指定代打", attrs: `data-absent-with-sub="${escapeHtml(m.name)}"` }]
              : []
          )
        : [],
    });
  }
  const memberCount = attendingRows.length;
  for (const d of game.confirmed_drop_ins) {
    // 代打 is somebody a member personally arranged; 臨打 signed up
    // themselves and stands in for nobody, even when their fee refunds an
    // absence.
    attendingRows.push({
      person: d,
      name: d.player_name,
      tone: "dropin",
      changed:
        change && change.tab === "attending" && change.names.has(d.player_name)
          ? change.phase
          : false,
      // One note at most: 代 X wins over who brought them.
      note: d.covering
        ? `<span class="att-note sub">代 ${escapeHtml(d.covering)}</span>`
        : d.brought_by_name
          ? `<span class="att-note by">${escapeHtml(d.brought_by_name)} 報名</span>`
          : "",
      // Only for signups this caller may undo (their own, or a guest they
      // brought), unless it is the organizer's screen. The server checks
      // again.
      actions:
        opts.onRemoveDropIn && !frozen && (opts.canRemoveDropIn || (() => true))(d)
          ? [{ label: "移除", tone: "danger", attrs: `data-remove-drop-in="${d.id}"` }]
          : [],
    });
  }
  // Two sections when there are both, numbered straight through, because
  // the count against capacity is what matters.
  const dropInCount = attendingRows.length - memberCount;
  const sectioned = memberCount > 0 && dropInCount > 0;
  const section = (label, n) => `<div class="att-sec"><span>${label}</span><b>${n}</b></div>`;
  const attendingHtml = attendingRows
    .map(
      (row, i) =>
        (sectioned && i === 0 ? section("固定成員", memberCount) : "") +
        (sectioned && i === memberCount ? section("臨打", dropInCount) : "") +
        rosterRow({ ...row, num: i + 1 })
    )
    .join("");

  const absentRows = [];
  for (const absence of game.absences) {
    const allowed = canAssignSubstitute(absence);
    const covering = game.confirmed_drop_ins.find((d) => d.covering === absence.player_name);
    const offerAssign = !!onAssignSubstitute && allowed && !frozen;
    const offerCancel =
      !!absence.covered_by && !frozen && !!onCancelSubstitute && allowed && covering;

    // Two buttons at most, so 取消代打 is inside the substitute picker. 取消請假
    // is offered whoever fills the slot; the server sends them back to
    // the queue.
    const actions = [];
    if (offerAssign) {
      actions.push({
        label: absence.covered_by ? "更換代打" : "指定代打",
        attrs: `data-toggle-sub="${absence.id}"`,
      });
    } else if (offerCancel) {
      actions.push({ label: "取消代打", tone: "danger", attrs: `data-cancel-sub="${covering.id}"` });
    }
    if (opts.onCancelAbsence && allowed && !frozen) {
      actions.push({ label: "取消請假", tone: "quiet", attrs: `data-undo-absence="${absence.id}"` });
    }

    absentRows.push(
      rosterRow({
        num: "—",
        person: season.members.find((m) => m.name === absence.player_name),
        name: absence.player_name,
        tone: "absent",
        changed:
          change && change.tab === "absent" && change.names.has(absence.player_name)
            ? change.phase
            : false,
        // Three states: somebody you arranged is your 代打; somebody who
        // signed up and filled the slot is named as 已補上 (they were never
        // asked); an empty slot is a gap.
        note: absence.covered_by
          ? `<span class="att-note sub">${escapeHtml(absence.covered_by)} 代打</span>`
          : absence.filled_by
            ? `<span class="att-note">${escapeHtml(absence.filled_by)} 已補上</span>`
            : '<span class="att-note gap">缺額</span>',
        actions,
      }) + (offerAssign ? substituteForm(absence, false, offerCancel ? covering : null) : "")
    );
  }

  /** Who comes off so a queued person can come on, shown only when the
   * game is full: tap a name.
   */
  function swapOutPicker(entry) {
    if (!isGameFull(season, game)) return "";
    const swappable = game.confirmed_drop_ins;
    if (!swappable.length) return "";
    return `
      <div class="picker-backdrop" data-swap-form="${entry.id}" hidden>
        <div class="picker">
          <div class="picker-head">
            <span>${escapeHtml(entry.player_name)} 遞補</span>
            <button type="button" class="picker-close" data-close-overlay aria-label="關閉">✕</button>
          </div>
          <p class="picker-note">本場已滿，請選擇一位臨打換下；換下者回到候補。</p>
          <div class="sub-pick">
          ${swappable
            .map(
              (d) => `
            <div class="pick-row" data-swap-in="${entry.id}" data-swap-out="${d.id}">
              <i class="radio"></i>${avatarHtml(d.player_name, d.avatar_url)}
              <span class="pk-name">${escapeHtml(d.player_name)}</span>${genderTag(d.gender)}
              ${d.covering ? `<span class="att-note sub">代 ${escapeHtml(d.covering)}</span>` : ""}
            </div>`
            )
            .join("")}
          </div>
        </div>
      </div>`;
  }

  // Same row as everything else; see rosterRow.
  const waitlistRows = game.waitlist_entries
    .map((w, i) =>
      rosterRow({
        num: i + 1,
        person: w,
        name: w.player_name + (viewerName && w.player_name === viewerName ? "（你）" : ""),
        tone: "queued" + (viewerName && w.player_name === viewerName ? " me" : ""),
        changed:
          change && change.tab === "queued" && change.names.has(w.player_name)
            ? change.phase
            : false,
        // The tab already says they are queued; only who brought them is
        // added.
        note: w.brought_by_name
          ? `<span class="att-note by">${escapeHtml(w.brought_by_name)} 報名</span>`
          : "",
        // 遞補 first: putting somebody on is why the organizer opens this.
        actions: [
          opts.onPromoteFromWaitlist && !frozen
            ? { label: "遞補", attrs: `data-promote-waitlist="${w.id}"` }
            : null,
          opts.onLeaveWaitlist && !frozen && (!opts.canLeaveWaitlist || opts.canLeaveWaitlist(w))
            ? { label: "移除", tone: "danger", attrs: `data-remove-waitlist="${w.id}"` }
            : null,
        ].filter(Boolean),
      }) + (opts.onPromoteFromWaitlist && !frozen ? swapOutPicker(w) : "")
    )
    .join("");

  // The card says which game this is, the controls come next, and the
  // lists last, so scrolling only ever reveals more names. The three
  // groups are tabs whose labels carry the counts (even 0), so the
  // summary stays on screen and each group is one tap away.
  const counts = {
    attending: attendingRows.length,
    absent: absentRows.length,
    queued: game.waitlist_entries.length,
  };
  // Kept on the container, which outlives the innerHTML, so a repaint
  // keeps the tab.
  const asked = container.dataset ? container.dataset.gdActiveTab : null;
  const activeTab = TAB_ORDER.some((t) => t.key === asked) ? asked : "attending";
  if (container.dataset) container.dataset.gdActiveTab = activeTab;

  container.innerHTML = `
    ${
      opts.compactHead
        ? renderGameHeadLine(season, game, {
            metaPills: opts.heroMetaPills,
            statusHtml: opts.statusHtml,
          })
        : renderGameHero(season, game, {
            metaPills: opts.heroMetaPills,
            // Inside the card: .hero-actions draws its own top rule.
            statusHtml: opts.statusHtml,
            actionsHtml: opts.actionsHtml,
          })
    }
    ${
      closed
        ? '<div class="gdetail-locked">本季已結算，名單已確定</div>'
        : locked
          ? '<div class="gdetail-locked">已過更動期限，本場無法再變更</div>'
          : ""
    }
    ${extraHtml}
    ${
      opts.onAddDropIn && !frozen
        ? // The trigger comes before its own popup in the markup, not
          // After the trigger: the popup stays in the DOM once opened,
          // and placing it first would shift every later button in
          // document and tab order.
          `<button type="button" class="su-add" data-open-add-dropin>＋ 新增臨打</button>
           <div class="add-dropin-picker"></div>`
        : ""
    }
    <div class="gd-tabs" role="tablist">
      ${TAB_ORDER.map(
        (t) => `<button type="button" role="tab" class="gd-tab${
          t.key === activeTab ? " active" : ""
        }${
          change && t.key === change.tab
            ? ` just-changed roster-change-${change.phase}`
            : ""
        }" data-gd-tab="${t.key}" aria-selected="${t.key === activeTab}"${
          change && t.key === change.tab && change.phase === "pending"
            ? ' aria-busy="true"'
            : ""
        }>${
          t.label
        } <b>${counts[t.key]}</b></button>`
      ).join("")}
    </div>
    <div class="gd-panel" data-gd-panel="attending"${activeTab === "attending" ? "" : " hidden"}>
      <div class="att-list">${attendingHtml || emptyPanel("本場尚無出席名單")}</div>
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
    ${offerAbsentWithSub ? "<div data-absent-sub-holder></div>" : ""}
  `;

  /** 請假並指定代打: the substitute picker for somebody still on court, built on
   * demand rather than once per member.
   */
  function openAbsentWithSub(name) {
    const holder = container.querySelector("[data-absent-sub-holder]");
    if (!holder) return false;
    holder.innerHTML = substituteForm({ id: "new", player_name: name, covered_by: null }, true);
    const form = holder.querySelector("[data-sub-form]");
    if (form) form.hidden = false;
    return !!form;
  }
  _gameDetailOpeners.set(container, { absentWithSub: openAbsentWithSub });

  // Escape closes the top layer, else the open row.
  container.onkeydown = (e) => {
    if (e.key !== "Escape") return;
    const open = Array.from(container.querySelectorAll(".picker-backdrop")).filter(
      (el) => !el.hidden
    );
    if (open.length) {
      open[open.length - 1].hidden = true;
      e.preventDefault();
    } else if (container.querySelector(".att-row.open")) {
      setOpenRow(null);
      e.preventDefault();
    }
  };

  /** One row open at a time; null closes them all. */
  function setOpenRow(row) {
    container.querySelectorAll(".att-row.open").forEach((r) => {
      if (r === row) return;
      r.classList.remove("open");
      const t = r.querySelector("[data-row-toggle]");
      if (t) t.setAttribute("aria-expanded", "false");
    });
    if (!row) return;
    const opening = !row.classList.contains("open");
    row.classList.toggle("open", opening);
    const t = row.querySelector("[data-row-toggle]");
    if (t) t.setAttribute("aria-expanded", String(opening));
  }

  // Assigned rather than added, so re-rendering never stacks handlers.
  container.onclick = (e) => {
    // Switching tabs shows a panel already built, with no refetch.
    // Matched as `button[...]` because the container records the active
    // tab in a data attribute of its own.
    const tab = e.target.closest("button[data-gd-tab]");
    if (tab) {
      showGameDetailTab(container, tab.dataset.gdTab);
      return;
    }
    const closeOverlay = e.target.closest("[data-close-overlay]");
    if (closeOverlay) {
      const layer = closeOverlay.closest(".picker-backdrop");
      if (layer) layer.hidden = true;
      return;
    }
    // A row's button closes the row, then falls through to the handler
    // for its data attribute. Anywhere else on the row toggles it.
    const act = e.target.closest("[data-act]");
    const row = e.target.closest && e.target.closest("[data-row-acts]");
    if (act) {
      setOpenRow(null);
    } else if (row) {
      setOpenRow(row);
      return;
    }
    const absentWithSub = e.target.closest("[data-absent-with-sub]");
    if (absentWithSub) {
      openAbsentWithSub(absentWithSub.dataset.absentWithSub);
      return;
    }
    const toggleSub = e.target.closest("[data-toggle-sub]");
    if (toggleSub) {
      const form = container.querySelector(`[data-sub-form="${toggleSub.dataset.toggleSub}"]`);
      if (form) form.hidden = false;
      return;
    }
    const closeSub = e.target.closest("[data-close-sub]");
    if (closeSub) {
      const form = container.querySelector(`[data-sub-form="${closeSub.dataset.closeSub}"]`);
      if (form) form.hidden = true;
      return;
    }
    // Tapping outside the panel closes it, like every sheet here.
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
        toast("請選擇人選，或直接輸入姓名");
        return;
      }
      if (confirmSub.dataset.confirmAbsent && opts.onRecordAbsenceWithSubstitute) {
        opts.onRecordAbsenceWithSubstitute(
          confirmSub.dataset.confirmAbsent,
          name,
          (genderSelect && genderSelect.value) || null,
          confirmSub
        );
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
    // Choosing who steps out so this queued person can play.
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
      // With room to spare, promote directly; on a full game open the
      // picker.
      const picker = container.querySelector(`[data-swap-form="${id}"]`);
      if (picker) {
        picker.hidden = false;
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
    const openAddDropin = e.target.closest("[data-open-add-dropin]");
    if (openAddDropin && opts.onAddDropIn) {
      openAddDropinPicker(container);
      return;
    }
  };

  /** Who 新增臨打 may offer: anyone in the club not already on this game's
   * lists (members playing or away, drop-ins, queued).
   */
  function dropInCandidates() {
    // Somebody already queued for this game is signed up already.
    return peopleToPick(null, false);
  }

  /** Opens the shared person picker for 新增臨打. */
  async function openAddDropinPicker(root) {
    const holder = root.querySelector(".add-dropin-picker");
    if (!holder) return;
    renderPersonPicker(holder, {
      title: "新增臨打",
      hint: "選球隊裡的人，或直接輸入姓名。",
      candidates: dropInCandidates(),
    });
    const picked = await choosePerson(holder);
    if (!picked) return;
    opts.onAddDropIn(picked.name, picked.gender, null);
  }
}
