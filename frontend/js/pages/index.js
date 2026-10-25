/* The folder root: a plain forward to member.html, keeping the query
 * string (an invite token is the only thing naming a club).
 *
 * No LIFF SDK on purpose: each rich menu destination is its own LIFF app
 * pointing at its own page, so no LIFF entry passes through here, and
 * calling liff.init() would initialise an app this page is not the
 * endpoint of. replace(), so this page is not a history entry that Back
 * bounces off.
 */
location.replace("member.html" + location.search + location.hash);

// Only if the forward did not happen; shown late so a normal forward
// never flashes it.
setTimeout(function () {
  var el = document.getElementById("fallback");
  if (el) el.hidden = false;
}, 1200);
