/* The folder root: a plain forward to member.html.
 *
 * No LIFF SDK here, and that absence is deliberate. For part of one
 * day this page was the LIFF endpoint and the whole app routed
 * through it; it read liff.state and redirected before the SDK had
 * run, which threw away the handshake and put every rich menu button
 * into an infinite login loop inside the LINE app.
 *
 * That design is gone. Each menu destination now has its own LIFF app
 * pointing straight at its own page — member.html, ledger.html,
 * report.html — so no LIFF entry passes through here at all: no
 * liff.state, no handshake, no session to establish.
 *
 * Do not add liff.init() back. It was added while this page was the
 * endpoint and removed when it stopped being one; calling it here now
 * would initialise an app this page is not the endpoint of.
 *
 * What still arrives is somebody opening the folder root directly.
 * They get the app with the query string intact — the invite token is
 * the only thing naming a club (see api/invites.py), and dropping it
 * lands the visitor on a picker that cannot show them the club they
 * were invited to.
 *
 * replace(), not assign(): this page must not become a history entry,
 * or Back from member.html bounces straight forward again.
 */
location.replace("member.html" + location.search + location.hash);

// Only if the line above somehow did not take. Shown late so that a
// normal, fast forward never flashes it.
setTimeout(function () {
  var el = document.getElementById("fallback");
  if (el) el.hidden = false;
}, 1200);
