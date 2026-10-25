// Which club and season a page is about, and the switcher at the top.

/** Says so when the club on screen is one the developer sees only as the
 * developer — never their own — so another club's books are not mistaken
 * for theirs. Sits just above the club and season pickers. */
function markDeveloperView(clubs, clubEl) {
  const club = (clubs || []).find((c) => String(c.id) === String(clubEl.value));
  let bar = document.getElementById("dev-view");
  if (!club || !club.developer_view) {
    if (bar) bar.hidden = true;
    return;
  }
  if (!bar) {
    bar = document.createElement("p");
    bar.id = "dev-view";
    bar.className = "dev-view";
    // Above the season picker. The management pages' club <select> sits
    // in the top bar now (mountClubChip), so its own parent is the wrong
    // place to put a banner.
    const row = document.querySelector(".picker-row") || clubEl.parentNode;
    row.parentNode.insertBefore(bar, row);
  }
  bar.textContent = `開發者檢視：${club.name}`;
  bar.hidden = false;
}

/** The management pages' club button: the chip the member page has in
 * the same corner, so moving between 會員 and 管理 leaves the top of the
 * screen where it was. It replaced a 「‹ 會員頁」 that did what the
 * 會員／管理 row under it already does, and a 球隊 select that took half
 * a row of its own (proposal 5, 2026-10-17).
 *
 * The page's hidden club <select> stays the source of truth —
 * initClubAndSeasonPickers fills and drives it — and the chip follows
 * it: options arriving or renamed, or a change. */
function mountClubChip(chip, select) {
  if (!chip || !select || typeof MutationObserver === "undefined") return;
  const nameEl = chip.querySelector(".club-chip-name");
  const dotEl = chip.querySelector(".club-dot");
  if (!nameEl || !dotEl) return;
  function sync() {
    const option = select.options[select.selectedIndex];
    const name = option ? option.textContent : "";
    nameEl.textContent = name || "選擇球隊";
    dotEl.textContent = option && option.value ? firstGrapheme(name) : "-";
  }
  new MutationObserver(sync).observe(select, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  select.addEventListener("change", sync);
  chip.addEventListener("click", () => openClubChooser(select));
  sync();
}

/** The list behind the club chip: one row per club this page manages. */
function openClubChooser(select) {
  // No options at all means the list has not arrived yet; once it has,
  // a club-less list still carries one empty-valued 尚無球隊 option (see
  // initClubAndSeasonPickers). Saying 尚未管理任何球隊 to somebody who
  // tapped before the list loaded was wrong (found 2026-10-21), so the
  // sheet says it is loading and fills itself in when the list lands.
  const rowsHtml = () => {
    if (!select.options.length) {
      return '<div class="page-state"><div class="spinner" role="status" aria-label="載入中"></div><p>載入中…</p></div>';
    }
    const clubs = Array.from(select.options).filter((o) => o.value);
    return clubs.length
      ? clubs
          .map(
            (o) => `
        <button type="button" class="club-row${o.value === select.value ? " on" : ""}" data-club="${escapeHtml(o.value)}">
          <i class="club-dot">${escapeHtml(firstGrapheme(o.textContent))}</i>
          <span class="cr-name"><span class="cr-title">${escapeHtml(o.textContent)}</span></span>
        </button>`
          )
          .join("")
      : '<div class="empty">尚未管理任何球隊</div>';
  };
  const backdrop = document.createElement("div");
  backdrop.className = "gsheet-backdrop";
  backdrop.innerHTML = `
    <div class="gsheet">
      <button type="button" class="gsheet-close" aria-label="關閉">✕</button>
      <h3>球隊</h3>
      <div data-club-rows>${rowsHtml()}</div>
    </div>`;
  const observer =
    typeof MutationObserver === "undefined"
      ? null
      : new MutationObserver(() => {
          const rows = backdrop.querySelector("[data-club-rows]");
          if (rows) rows.innerHTML = rowsHtml();
        });
  if (observer) observer.observe(select, { childList: true, subtree: true, characterData: true });
  const close = () => {
    if (observer) observer.disconnect();
    backdrop.remove();
  };
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop || e.target.closest(".gsheet-close")) {
      close();
      return;
    }
    const row = e.target.closest("[data-club]");
    if (!row) return;
    close();
    if (row.dataset.club === select.value) return;
    select.value = row.dataset.club;
    select.dispatchEvent(new Event("change"));
  });
  document.body.appendChild(backdrop);
}

/** The circle in the top bar: the signed-in person's own picture. */
function paintSelfAvatar(el, person) {
  if (!el || !person) return;
  el.classList.toggle("has-photo", !!person.avatar_url);
  el.innerHTML = person.avatar_url
    ? `${escapeHtml(firstGrapheme(person.name))}<img src="${escapeHtml(person.avatar_url)}" alt="" decoding="async" referrerpolicy="no-referrer" onerror="this.parentNode.classList.remove('has-photo'); this.remove()">`
    : escapeHtml(firstGrapheme(person.name));
}

/** "2026年7月" / "2026年7~9月" / "2025年12月~2026年2月" — a season's
 * game-date span read as a season, not an ISO date range. */
/** The season a page should open on: the one being played now.
 *
 * "Now" first — today inside the season's own dates. Failing that the
 * next one due to start, so a club between seasons looks forward rather
 * than back. Failing that the most recently finished, which is where the
 * money still is when a season has ended but isn't settled.
 *
 * Seasons arrive newest-created first, and that used to be the default.
 * It meant booking next season in advance moved every page onto a season
 * with no games played yet — reported 2026-09-29.
 */
function defaultSeasonId(seasons) {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  const current = seasons.filter(
    (s) => s.first_game_date <= iso && iso <= s.last_game_date
  );
  // More than one only when seasons overlap, which the app allows: the
  // one ending soonest is the one being finished off.
  if (current.length) {
    return current.reduce((a, b) => (a.last_game_date <= b.last_game_date ? a : b)).id;
  }

  const upcoming = seasons.filter((s) => s.first_game_date > iso);
  if (upcoming.length) {
    return upcoming.reduce((a, b) => (a.first_game_date <= b.first_game_date ? a : b)).id;
  }

  const past = seasons.filter((s) => s.last_game_date < iso);
  if (past.length) {
    return past.reduce((a, b) => (a.last_game_date >= b.last_game_date ? a : b)).id;
  }
  return seasons[0].id;
}

const CLUB_STORAGE_KEY = "vf_club";

/** Adding the club's Official Account as a friend.
 *
 * Not decoration: LINE refuses to push to anyone who has not added the
 * account, so an organizer who skipped the prompt never receives the
 * short-roster alert and has no way of knowing why
 * (docs/backlog.md §1). The LIFF consent screen offers to add it, but
 * every LIFF app here is set to bot_prompt=normal, which makes that
 * offer skippable.
 *
 * The basic id comes from GET /v2/bot/info on the Messaging channel;
 * it is public — it is what anyone adding the account types in — so it
 * belongs in the frontend rather than in an environment variable.
 */
const OFFICIAL_ACCOUNT_URL = "https://line.me/R/ti/p/@363bopii";

/** Which club and season a page is looking at: remembered per *tab*,
 * defaulted from the last one this browser used.
 *
 * It used to live in localStorage alone, which every tab of a browser
 * shares. Opening a second club in a second tab rewrote the first tab's
 * club under it, and the first tab then fetched the *other* club's
 * roster while still showing this club's season - reported on
 * 2026-09-17 as "just created a club and it already has 21 members",
 * and as being shown as not the organizer, because in that other club
 * they are not.
 *
 * sessionStorage is per tab, so the two no longer collide; localStorage
 * still carries the default, so a new tab opens where you left off.
 */
function rememberedId(key) {
  const thisTab = sessionStorage.getItem(key);
  if (thisTab !== null) return thisTab;
  const lastUsed = localStorage.getItem(key);
  if (lastUsed !== null) sessionStorage.setItem(key, lastUsed);
  return lastUsed;
}

function rememberId(key, value) {
  sessionStorage.setItem(key, value);
  localStorage.setItem(key, value);
}

function forgetId(key) {
  sessionStorage.removeItem(key);
  localStorage.removeItem(key);
}

function currentClubId() {
  return rememberedId(CLUB_STORAGE_KEY);
}

/** A switched season, made visible.
 *
 * The picker changes and the numbers under it are replaced, but with
 * nothing moving in between the screen reads as though nothing
 * happened - reported on 2026-09-17 as 「切換季的時候要有變換季的動畫
 * 不然會以為沒切到」. Every page marks the parts that belong to a
 * season with data-season-content; this replays a short fade over them.
 *
 * Only on a real switch. The first paint of a page calls onSeasonChange
 * too, and animating that would mean every page load flickers.
 */
function flashSeasonContent() {
  for (const el of document.querySelectorAll("[data-season-content]")) {
    el.classList.remove("season-swap");
    // Reading layout restarts the animation; without it a second switch
    // re-adds a class the element already has and nothing plays.
    void el.offsetWidth;
    el.classList.add("season-swap");
  }
}

/** Shows that a club change is still finding that club's seasons. The
 * wrapper is optional because member.html has only a season picker; the
 * management pages opt in with .picker-control and a loading root.
 */
function setSeasonPickerLoading(seasonEl, loading) {
  if (!seasonEl) return;
  seasonEl.disabled = loading;
  if (seasonEl.classList) seasonEl.classList.toggle("season-picker-loading", loading);
  const control = seasonEl.closest && seasonEl.closest(".picker-control");
  if (control) control.classList.toggle("is-loading", loading);
  const root = seasonEl.closest && seasonEl.closest("[data-picker-loading-root]");
  if (root) root.classList.toggle("is-picker-loading", loading);
}

/** Which clubs the management pages offer: the ones this person
 * organizes, and — for the developer only — every other club, which
 * GET /clubs marks `developer_view`. One rule for all four pages, so a
 * club the developer merely belongs to can't drop out of one of them. */
function managesClub(club) {
  return club.role === "organizer" || club.developer_view === true;
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
 *
 * `knownClubs`, when given, is `{ list, fresh }` from a caller that has
 * already read the same clubs for its own reasons — member.html needs
 * `wants_fixed_membership`, which only GET /players/{id}/clubs carries,
 * so it was asking for the caller's clubs twice on every single load,
 * from two endpoints that answer with the same clubs. The two reads were
 * serial, not parallel, because nothing here can start until a club is
 * chosen: measured at 485ms of a 586ms warm load spent re-reading a list
 * the page already had in a variable.
 */
async function initClubAndSeasonPickers(
  apiBase,
  clubEl,
  seasonEl,
  seasonStorageKey,
  onSeasonChange,
  onError,
  clubFilter,
  knownClubs
) {
  let seasonLoad = 0;
  let shownClubs = [];
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
    if (knownClubs) {
      const mine = clubFilter ? knownClubs.list.filter(clubFilter) : knownClubs.list;
      await applyClubs(mine, { fresh: knownClubs.fresh });
      return;
    }
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
      forgetId(CLUB_STORAGE_KEY);
      forgetId(seasonStorageKey);
    }
    onSeasonChange(null);
    return;
  }

  clubEl.innerHTML = clubs
    .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    .join("");

  const rememberedClub = rememberedId(CLUB_STORAGE_KEY);
  if (rememberedClub && clubs.some((c) => String(c.id) === rememberedClub)) {
    clubEl.value = rememberedClub;
  }
  // Only the network's list may replace the remembered club. A cached
  // list can predate the club just chosen elsewhere — the developer
  // page's 管理, a freshly accepted invite — and overwriting on its word
  // sent the page to the first club before the full list arrived.
  if (!meta || meta.fresh || clubEl.value === rememberedClub) {
    rememberId(CLUB_STORAGE_KEY, clubEl.value);
  }
  shownClubs = clubs;
  markDeveloperView(clubs, clubEl);

  async function loadSeasons(showLoading) {
    const clubId = String(clubEl.value);
    const ticket = ++seasonLoad;
    if (showLoading) setSeasonPickerLoading(seasonEl, true);
    try {
      await getJsonSWR(
        `${apiBase}/clubs/${clubId}/seasons`,
        (seasons) => {
          if (ticket !== seasonLoad || String(clubEl.value) !== clubId) return;
          applySeasons(seasons);
        },
        { headers: authHeader() }
      );
    } finally {
      if (ticket === seasonLoad) setSeasonPickerLoading(seasonEl, false);
    }
  }

  function applySeasons(seasons) {
    if (seasons.length === 0) {
      seasonEl.innerHTML = '<option value="">尚無任何季別</option>';
      onSeasonChange(null);
      return;
    }

    seasonEl.innerHTML = seasons
      .map((s) => {
        // Just enough to tell one season from another. The picker shares a
        // row with the club now, so half a phone's width — the count of
        // games and people used to ride along here and got cut off
        // mid-character, and the page says both a few lines further down.
        const settledTag = s.settled ? " · 已結算" : "";
        return `<option value="${s.id}">${formatSeasonLabel(s)}${settledTag}</option>`;
      })
      .join("");

    // Which season to open on, when nothing was picked in this tab yet.
    //
    // The list arrives newest-created first, so the old rule — "whatever
    // is first" — meant that booking next season early moved every
    // page's default onto a season nobody is playing yet. What an
    // organizer wants is the one being played now.
    //
    // Only sessionStorage remembers a choice (see pickSeasonId): opening
    // the app tomorrow lands on the current season again, while moving
    // between pages inside one visit keeps the season being worked on —
    // without that, opening a past season's ledger and tapping through
    // to 明細 would snap back to the current one and make an old season
    // impossible to work with at all.
    const remembered = sessionStorage.getItem(seasonStorageKey);
    if (remembered && seasons.some((s) => String(s.id) === remembered)) {
      seasonEl.value = remembered;
    } else {
      seasonEl.value = String(defaultSeasonId(seasons));
    }

    // Assignment rather than addEventListener: loadSeasons runs again on
    // every club change, and addEventListener would stack one more
    // handler each time.
    seasonEl.onchange = () => {
      // sessionStorage only — a choice lasts this visit, not forever.
      sessionStorage.setItem(seasonStorageKey, seasonEl.value);
      onSeasonChange(seasonEl.value);
      flashSeasonContent();
    };

    onSeasonChange(seasonEl.value);
  }

  clubEl.onchange = () => {
    rememberId(CLUB_STORAGE_KEY, clubEl.value);
    markDeveloperView(shownClubs, clubEl);
    loadSeasons(true).catch((e) => {
      console.error("Could not load seasons:", e);
      if (onError) onError(e);
    });
  };

  await loadSeasons(false);
  }
}
