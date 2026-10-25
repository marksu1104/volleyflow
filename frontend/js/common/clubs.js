// Which club and season a page is about, and the switcher at the top.

/** Says so when the club on screen is one the developer sees only as the
 * developer, so another club's books are not mistaken for theirs.
 */
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
    // Above the season picker; the club <select> lives in the top bar.
    const row = document.querySelector(".picker-row") || clubEl.parentNode;
    row.parentNode.insertBefore(bar, row);
  }
  bar.textContent = `開發者檢視：${club.name}`;
  bar.hidden = false;
}

/** The management pages' club button, the same chip the member page has in
 * that corner, so switching between 會員 and 管理 keeps the top of the screen
 * still. The hidden club <select> stays the source of truth
 * (initClubAndSeasonPickers drives it); the chip follows it.
 */
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
  // No options yet means the list has not arrived (an empty club list
  // still carries one 尚無球隊 option), so say it is loading and fill in when
  // it lands.
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

/** The season a page should open on: the one being played now; failing
 * that the next to start, so a club between seasons looks forward;
 * failing that the most recently finished, where unsettled money still
 * is.
 */
function defaultSeasonId(seasons) {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  const current = seasons.filter(
    (s) => s.first_game_date <= iso && iso <= s.last_game_date
  );
  // Seasons may overlap; the one ending soonest is being finished off.
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

/** Adds the Official Account as a friend. LINE delivers private messages
 * only to people who have, and the LIFF consent screen's offer to add it
 * can be skipped. The id is public (it is what anyone types to add the
 * account).
 */
const OFFICIAL_ACCOUNT_URL = "https://line.me/R/ti/p/@363bopii";

/** Which club and season a page is looking at, remembered per tab in
 * sessionStorage, so two tabs on two clubs do not overwrite each other;
 * localStorage keeps the default for a new tab.
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

/** Replays a short fade over the parts of the page marked
 * data-season-content, so switching season visibly changes something.
 * Only on a real switch, not on the first paint.
 */
function flashSeasonContent() {
  for (const el of document.querySelectorAll("[data-season-content]")) {
    el.classList.remove("season-swap");
    // Reading layout restarts the animation for a class the element
    // already has.
    void el.offsetWidth;
    el.classList.add("season-swap");
  }
}

/** Shows that a club change is still loading that club's seasons. The
 * wrapper is optional: member.html has only a season picker.
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

/** Which clubs the management pages offer: the ones this person organizes
 * and, for the developer, every club GET /clubs marks `developer_view`.
 */
function managesClub(club) {
  return club.role === "organizer" || club.developer_view === true;
}

/** Wires the club and season pickers together: picking a club loads its
 * seasons, picking a season calls onSeasonChange (also once at the
 * start). onSeasonChange(null) means nothing to show: no club, or a club
 * without seasons.
 *
 * `knownClubs` is `{ list, fresh }` from a caller that already fetched
 * the caller's clubs (member.html does), saving a second request before
 * anything can start.
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
  // A failed fetch is reported to the caller (see showPageError) rather
  // than leaving a blank page.
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
    // getJsonSWR may call this twice (cache, then network), so the season
    // fetch's rejection is caught here. Callers must resolve identity
    // first, or the requests have no token.
    await getJsonSWR(`${apiBase}/clubs`, (clubs, meta) => {
      // The management pages filter out clubs the caller only belongs to;
      // the server refuses their writes anyway.
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
    // Forget a remembered club that no longer exists, but only on the
    // network's word: a cached empty list is a guess, and forgetting
    // cannot be undone.
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
  // Only the network's list may replace the remembered club; a cached
  // list can predate a club just chosen elsewhere.
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
        // Just enough to tell seasons apart; the picker has half a row.
        const settledTag = s.settled ? " · 已結算" : "";
        return `<option value="${s.id}">${formatSeasonLabel(s)}${settledTag}</option>`;
      })
      .join("");

    // Which season to open on when nothing was picked in this tab: see
    // defaultSeasonId. Only sessionStorage remembers a choice, so
    // tomorrow opens on the current season while moving between pages
    // keeps the one being worked on.
    const remembered = sessionStorage.getItem(seasonStorageKey);
    if (remembered && seasons.some((s) => String(s.id) === remembered)) {
      seasonEl.value = remembered;
    } else {
      seasonEl.value = String(defaultSeasonId(seasons));
    }

    // Assigned rather than added: loadSeasons runs on every club change.
    seasonEl.onchange = () => {
      // sessionStorage only: a choice lasts this visit.
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
