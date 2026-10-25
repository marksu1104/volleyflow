// The game sheet: its roster tabs, the person picker, and roster changes
// shown before the server confirms them.

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

/** Two-stage visual cue for a roster movement.
 *
 * The game sheet deliberately keeps the group the person chose instead
 * of jumping to another tab after every write. That stability had an
 * unintended cost: marking someone absent while looking at 出席 only
 * made their row vanish, and the destination was easy to miss. Store one
 * pending cue on the long-lived container; the first render moves the
 * row and keeps the destination visibly busy. The server answer paints a
 * separate completion cue in the same frame as the success message.
 * Each paint is consumed once so unrelated renders never replay an old
 * animation. */
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

/** Starts, or refines, one visible roster movement.
 *
 * The returned token ties the eventual server answer to this exact tap.
 * If somebody taps 請假 and then 取消請假 before the first response
 * arrives, completion of the older request must not overwrite the newer
 * action's pending cue. Passing the token back while reconciling lets a
 * server-discovered promotion refine the same movement instead of
 * starting another one. */
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

/** Turns the latest pending movement into the completion cue that is
 * painted in the same frame as its success message. Stale responses are
 * deliberately ignored; a newer tap owns the screen. */
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

/** The control that opens a roster row: a large tinted chevron, which
 * turns into ✕ while the row is open. Drawn, not typed: the › glyph sits
 * high in CJK fonts and read as punctuation, and the point of it is to be
 * seen (「把 › 做大做明顯」, 2026-10-17). */
const ROW_TOGGLE =
  '<button type="button" class="att-go" data-row-toggle aria-expanded="false" aria-label="操作">' +
  '<svg class="i-open" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
  '<svg class="i-close" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>' +
  "</button>";

/** Pickers a page may open in a rendered game sheet from outside it —
 * the hero card's 請假 opening 請假並指定代打. Keyed by container, set on
 * every render, so it always reaches the current one. */
const _gameDetailOpeners = new WeakMap();

function openAbsenceWithSubstitute(container, name) {
  const openers = _gameDetailOpeners.get(container);
  return !!(openers && openers.absentWithSub(name));
}

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
    // The screen now knows something the server hasn't been told yet, so
    // any read already in the air is out of date — see localRevision.
    bumpLocalRevision();
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
      bumpLocalRevision();
      repaint();
      toast(failureMessage + e.message);
      return null;
    }
  };
}

/** Wraps a "re-read everything" function so it can never undo a change.
 *
 * Two rules, both paid for:
 *
 *  - it goes through the same queue as the writes, so a refresh can never
 *    ask the server a question that a tap still waiting its turn hasn't
 *    been allowed to answer. Firing it straight away is what made the
 *    screen flip back;
 *  - only one is ever waiting. Asking again while one is already queued
 *    does nothing, because the queued one has not started yet and will
 *    see everything by the time it does. Three quick taps used to mean
 *    three full season reads of about 600ms each, all but the last of
 *    them pointless.
 */
/** Whether a finished write actually needs the season re-read.
 *
 * The refresh after a change costs a full season document — measured at
 * 650ms locally and a couple of hundred on a phone, on top of the write
 * itself. It exists to catch what the optimistic update couldn't know,
 * and for the two most-used actions in the app the server already says
 * whether there *was* anything: recording an absence answers with
 * `promoted_from_waitlist`, cancelling one with `released_player_id`.
 * Both null means the screen is already exactly right and the second
 * round trip is spent proving it — which is what made a tap that had
 * already visibly worked keep showing 儲存中 afterwards.
 *
 * A failed write still refreshes: the rollback is local, and the one
 * moment worth paying a round trip to be certain about is the one where
 * the screen and the server just disagreed.
 */
function needsRefreshAfter(result) {
  if (!result) return true; // the write failed; make sure of the truth
  return Boolean(result.promoted_from_waitlist || result.released_player_id);
}

function coalescedRefresh(load) {
  let busy = false;
  return function refresh() {
    // Queued or still reading, and either way it covers this request.
    // Queued is obvious — it hasn't started, so it will see everything.
    // Still reading takes the queue being strictly serial: a refresh only
    // begins once the queue has drained, so any change asking for one
    // while it runs had already finished before it started, and its read
    // went out afterwards. Clearing this at the *start* of the read
    // instead let a request arriving in the same microtask slip past and
    // fetch the whole season a second time for nothing.
    if (busy) return;
    busy = true;
    enqueueRequest(async () => {
      try {
        await load();
      } catch (e) {
        // The screen is already showing the right thing; a background
        // read that fails changes nothing the person can see.
        console.warn("Background refresh failed:", e);
      } finally {
        busy = false;
      }
    });
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

/** Applies the server-confirmed result of naming a substitute without
 * paying for another full season read before the list can move.
 *
 * The response contains the new signup id and, when the court was full,
 * who went back to the queue. That is enough to draw the consequential
 * parts immediately. A quiet refresh may still follow to recover an old
 * substitute's historic queue position, which is intentionally not
 * exposed by the write response. */
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

/** Draws 請假並指定代打 once the server has answered: the absence, and
 * the substitute in its slot. Nobody comes off the queue for it — the
 * request never offered them the slot — unless the substitute was
 * queued themselves, which applySubstituteLocally already handles. */
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

/** Draws an organizer's deliberate queue promotion immediately after
 * the write succeeds. On a full court the named replacement moves to
 * the queue in the same frame, so the list never briefly shows nineteen
 * people or makes one person disappear. */
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

/** Applies the player id returned by an automatic FIFO promotion.
 *
 * Cancelling a signup frees a slot. The server answers with the player
 * who took it, but not the new drop-in row id, so the local row keeps a
 * placeholder until the quiet refresh supplies that id. The important
 * part for the person watching is immediate: the vacancy and the
 * promoted row appear as one completed roster change.
 *
 * `fallback` covers the special case where a substitute originally came
 * from the queue. Cancelling the substitution gives that place back and
 * the same person may immediately be first in line again.
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
/** The people in a picker, under a heading per group (候補 / 球隊成員 /
 * 訪客), with anybody who can't be chosen still shown — greyed, saying
 * why — rather than left out. Leaving them out read as the app having
 * lost them: 「選球隊成員的列表中沒有……」, 2026-10-16, about people who
 * were simply on the season's roster already.
 *
 * `attrs(c)` is the markup that makes a row choosable; a row with a
 * `disabled` reason gets none, so no click handler can pick it. */
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

/** Every control on the game sheet is reached through one of these
 * callbacks, so withholding them is how the sheet becomes read-only —
 * in one place, rather than a `settled` check threaded through thirty
 * lines of markup that would each have to remember it. */
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

function renderGameDetail(container, season, game, options) {
  const change = _gameDetailChanges.get(container) || null;
  _gameDetailChanges.delete(container);
  const given = options || {};
  // A settled season is closed to everybody, the organizer included, and
  // the server refuses every one of these (_require_season_open). The
  // buttons come off the sheet rather than being left there to be
  // pressed and refused: a screen that offers an action it knows will
  // fail is worse than one that doesn't offer it.
  const closed = !!season.settled_at;
  const opts = closed
    ? Object.fromEntries(
        Object.entries(given).filter(([key]) => !GAME_SHEET_ACTIONS.includes(key))
      )
    : given;
  const extraHtml = opts.extraHtml || "";
  const clubMembers = opts.clubMembers || [];
  // The member screen holds everybody to the deadline, the organizer
  // included (2026-10-16): `locked` is answered per caller and is never
  // true for them, which let them take leave on a night already played
  // from a screen meant for their own attendance. The management screen
  // leaves asMember off and keeps correcting a night after the fact.
  const locked = game.locked || (opts.asMember === true && game.past_deadline === true);
  // Nothing that changes who is playing is offered once the night is
  // locked or the season settled. 代打 and 銷假 were offered on a night
  // already played while the matching 取消 was not, so a 代打 could be
  // named on the member screen and never taken back (2026-10-16).
  const frozen = locked || !!season.settled_at;
  const viewerName = opts.viewerName || "";
  const onAssignSubstitute = opts.onAssignSubstitute;
  const onCancelSubstitute = opts.onCancelSubstitute;
  const canAssignSubstitute = opts.canAssignSubstitute || (() => true);

  function initial(name) {
    return escapeHtml(firstGrapheme(name));
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
  // Who can be put on this game, grouped, with a reason for anybody who
  // can't — shared by the 代打 and 新增臨打 pickers (2026-10-16).
  //
  // 球隊成員 are people with their own LINE account; 訪客 are names typed
  // in by somebody, who never joined themselves — asked to be kept apart,
  // since a guest somebody brought isn't a member of the club. The whole
  // club is offered only on the management screen (pickFromWholeClub):
  // the organizer may put anybody on, a member only somebody without an
  // account of their own.
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
    // Whoever can be chosen first within each group, the greyed after.
    const order = ["候補", "球隊成員", "訪客"];
    return out.sort(
      (a, b) =>
        order.indexOf(a.group) - order.indexOf(b.group) ||
        Number(!!a.disabled) - Number(!!b.disabled)
    );
  }

  function substituteCandidates(absence) {
    // Somebody queued can be named: their own signup moves onto the court.
    return peopleToPick(absence.player_name, true);
  }

  /** `combined` is 請假並指定代打 for somebody still attending: there is
   * no absence yet, so the form is keyed "new" and its confirm button
   * names who is taking leave instead. */
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
    // Its own layer over the sheet, not a panel wedged between two roster
    // rows. Picking a person is a decision of its own: it wants the whole
    // screen, a title saying whose slot is being filled, and one way out.
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
            // Taking the substitute off without naming another: a row
            // has room for two buttons, and this is the rarer third.
            cancelFor
              ? `<button type="button" class="btn btn-danger picker-cancel-sub" data-cancel-sub="${cancelFor.id}">取消代打</button>`
              : ""
          }
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
  function rosterRow({ num, person, name, tone, note, actions, changed = false }) {
    // A row with something to do opens in place, downward, onto at most
    // two buttons (proposal round 2, plan B, 2026-10-20). It replaced a
    // menu that rose from the bottom of the screen: further from the row
    // that was tapped, and one more layer to read. The buttons carry the
    // same data attributes the old controls did, so the click routing
    // below is unchanged.
    const acts = actions && actions.length ? actions : null;
    // The note (代打 / 缺額 / X 報名) goes *inside* the name, as an
    // inline element, and that placement is the whole fix.
    //
    // As a flex child of the row it was blockified, so its vertical
    // padding counted toward the row's height: a row whose note ran long
    // measured 57px against its neighbours' 46px, and the note sat 9px
    // off the controls beside it. Inline, its padding doesn't touch the
    // line box. Measured in a real browser, after four theories about
    // the flex layout turned out to be wrong.
    //
    // Only the person's own name is allowed to be shortened, hence the
    // inner att-who: with the truncation on the whole thing, a long name
    // ate the 代打 note beside it and left a blank pill.
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
  // Taking leave and naming who covers, as one step: the substitute
  // takes the slot directly and the queue is never offered it, so whoever
  // queued first is not promoted and then bumped straight back out.
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
      // 指定代打 on somebody still playing is 請假 with a substitute named
      // in the same step — naming one is taking leave, so it needs no
      // longer name (2026-10-20).
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
    // 代打 and 臨打 are different things and no longer share a label.
    // 代打 is somebody a member personally arranged to stand in for
    // them; 臨打 signed themselves up and stands in for nobody, even
    // when their fee is what refunds an absence. Showing the second as
    // the first told members that a stranger was "their" substitute.
    attendingRows.push({
      person: d,
      name: d.player_name,
      tone: "dropin",
      changed:
        change && change.tab === "attending" && change.names.has(d.player_name)
          ? change.phase
          : false,
      // One note at most. The 臨打 heading above these rows says what
      // they are, so a 臨打 pill on some of them — and not on the ones a
      // bringer's name had displaced — only read as inconsistent
      // (2026-10-20). 代 X wins over the bringer because it is a billing
      // relationship; the money screen still names who brought them.
      note: d.covering
        ? `<span class="att-note sub">代 ${escapeHtml(d.covering)}</span>`
        : d.brought_by_name
          ? `<span class="att-note by">${escapeHtml(d.brought_by_name)} 報名</span>`
          : "",
      // Only offered for signups this caller is entitled to undo — their
      // own, or a guest they brought — unless this is the organizer's
      // screen, which manages everybody's. `canRemoveDropIn` is how the
      // two pages differ; the server checks the same thing again.
      actions:
        opts.onRemoveDropIn && !frozen && (opts.canRemoveDropIn || (() => true))(d)
          ? [{ label: "移除", tone: "danger", attrs: `data-remove-drop-in="${d.id}"` }]
          : [],
    });
  }
  // Two sections once there are both, numbered straight through: the
  // count against capacity is the number that matters on the night.
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

    // Two buttons at most, so 取消代打 waits inside the substitute
    // picker. 取消請假 (it was 銷假, a word nobody uses) is offered
    // whoever fills the slot; the server sends them back to the queue.
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
        actions,
      }) + (offerAssign ? substituteForm(absence, false, offerCancel ? covering : null) : "")
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
        changed:
          change && change.tab === "queued" && change.names.has(w.player_name)
            ? change.phase
            : false,
        // The tab already says these people are queued, as the 臨打
        // heading does above; only who brought them is added.
        note: w.brought_by_name
          ? `<span class="att-note by">${escapeHtml(w.brought_by_name)} 報名</span>`
          : "",
        // 遞補 first: the organizer opens this sheet to put somebody on
        // the court far more often than to strike them off, and the
        // queue's own order is exactly what they are overriding — the
        // person at the front is often the one who can't make it.
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
    ${
      opts.compactHead
        ? renderGameHeadLine(season, game, {
            metaPills: opts.heroMetaPills,
            statusHtml: opts.statusHtml,
          })
        : renderGameHero(season, game, {
            metaPills: opts.heroMetaPills,
            // Straight into the card, not stacked underneath it: .hero-actions
            // draws its own top rule to separate itself from the facts above,
            // which outside the card is a hairline floating on the page.
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
          // after — the popup is an overlay the trigger produces, and
          // putting it first pushed every button after it one or two
          // places later in the document the moment it had ever been
          // opened once, since it stays in the DOM (hidden, not gone)
          // between opens. Index-based iteration in tests/visual/smoke.js
          // caught this as buttons losing their own identity mid-run;
          // the same reordering would confuse tab order for a keyboard
          // user, which is the real reason to fix it rather than just
          // the test.
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

  /** 請假並指定代打: the substitute picker for somebody still on court.
   * Built when asked for rather than once per attending member — that
   * would be eighteen copies of the whole club list in the page. */
  function openAbsentWithSub(name) {
    const holder = container.querySelector("[data-absent-sub-holder]");
    if (!holder) return false;
    holder.innerHTML = substituteForm({ id: "new", player_name: name, covered_by: null }, true);
    const form = holder.querySelector("[data-sub-form]");
    if (form) form.hidden = false;
    return !!form;
  }
  _gameDetailOpeners.set(container, { absentWithSub: openAbsentWithSub });

  // Escape closes whichever layer is on top, else the open row. The
  // toggle is a real button, so Enter and Space already work on it.
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
    const closeOverlay = e.target.closest("[data-close-overlay]");
    if (closeOverlay) {
      const layer = closeOverlay.closest(".picker-backdrop");
      if (layer) layer.hidden = true;
      return;
    }
    // A row's button closes the row, then does exactly what the control
    // it replaced did: it carries the same data attribute, so it falls
    // through to the same handler below. Anywhere else on the row opens
    // or closes it.
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

  /** Who this game's 新增臨打 button may offer: anyone in the club who
   * isn't already occupying a slot for it — a fixed member (attending or
   * away; away is exactly when they'd want a substitute instead, see
   * canAssignSubstitute), an existing drop-in, or somebody already
   * queued. Mirrors substituteCandidates' reasoning for the same reason:
   * half the club would otherwise be offered a name that's already on
   * this game's own list.
   */
  function dropInCandidates() {
    // Somebody already queued for this game is signed up already.
    return peopleToPick(null, false);
  }

  /** Opens the shared picker in place of the old bare name field and
   * gender select — the same
   * modal the signup sheet and the substitute picker already use, rather
   * than a third, differently-shaped way to type in a name. */
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
