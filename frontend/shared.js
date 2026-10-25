// A page cached from before the scripts moved into js/ still asks for this
// file. Reload it once with a fresh query string so the browser fetches the
// current page instead. Safe to delete once no cached page can be that old.
(function () {
  try {
    if (sessionStorage.getItem("vf_moved") === "1") return;
    sessionStorage.setItem("vf_moved", "1");
  } catch (e) {
    return; // storage blocked: a stale page is better than a reload loop
  }
  var url = new URL(location.href);
  url.searchParams.set("v", String(Date.now()));
  location.replace(url.toString());
})();
