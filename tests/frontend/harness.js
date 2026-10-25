// Runs the frontend's own code under `node --test`.
//
// There is no browser here and no build step, so the shared scripts and a
// page's own script are loaded as source into a function scope with the
// handful of browser globals they touch stubbed out. That is enough
// to exercise the parts worth guarding: money arithmetic, who is on the
// attendance list, what the cache does when the network disagrees.
//
// The <select> stub earns its keep. A real one adopts its first option's
// value the moment you assign innerHTML, and the picker logic leans on
// exactly that; an earlier version of this harness didn't, and invented
// a failure that never existed in a browser.

const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = path.join(__dirname, "..", "..", "frontend");

function makeStore() {
  const data = {};
  return {
    _d: data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
    removeItem: (k) => {
      delete data[k];
    },
    clear: () => {
      for (const k of Object.keys(data)) delete data[k];
    },
  };
}

/** A classList that remembers, so a test can ask whether something is
 * marked busy or showing. The old stub answered `contains` with a flat
 * false, which quietly made any such question untestable. */
function makeClassList() {
  const classes = new Set();
  return {
    add: (...names) => names.forEach((n) => classes.add(n)),
    remove: (...names) => names.forEach((n) => classes.delete(n)),
    toggle: (n, on) => (on === undefined ? !classes.delete(n) && classes.add(n) : on ? classes.add(n) : classes.delete(n)),
    contains: (n) => classes.has(n),
  };
}

function makeElement() {
  return {
    innerHTML: "",
    textContent: "",
    value: "",
    hidden: false,
    disabled: false,
    style: {},
    dataset: {},
    classList: makeClassList(),
    setAttribute() {},
    removeAttribute() {},
    id: "",
    className: "",
    onclick: null,
    onchange: null,
    addEventListener() {},
    remove() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    appendChild() {},
  };
}

/** A <select> that behaves like one: assigning option markup selects the
 * first option, which is what initClubAndSeasonPickers relies on. */
function makeSelect() {
  let value = "";
  let html = "";
  return {
    hidden: false,
    onchange: null,
    get innerHTML() {
      return html;
    },
    set innerHTML(next) {
      html = next;
      const first = /value="([^"]*)"/.exec(next);
      value = first ? first[1] : "";
    },
    get value() {
      return value;
    },
    set value(next) {
      value = String(next);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}

/** Installs the browser globals the frontend expects, and returns the
 * knobs a test needs to drive them. */
function installGlobals() {
  const elements = {};
  globalThis.localStorage = makeStore();
  globalThis.sessionStorage = makeStore();

  // Object.keys(localStorage) has to see the stored keys, because
  // clearResponseCache walks them.
  const realKeys = Object.keys;
  Object.keys = (o) => (o === globalThis.localStorage ? realKeys(o._d) : realKeys(o));

  globalThis.document = {
    getElementById: (id) => (elements[id] = elements[id] || makeElement()),
    querySelector: () => null,
    querySelectorAll: () => [],
    // A <select> behaves differently enough from everything else to be
    // worth its own stub — see makeSelect. Anything else is an ordinary
    // element, which toast() needs for appendChild and classList.
    createElement: (tag) => (tag === "select" ? makeSelect() : makeElement()),
    addEventListener() {},
    body: { appendChild() {} },
    querySelectorAll: () => [],
  };
  globalThis.location = { search: "", pathname: "/member.html", hash: "", href: "" };
  // Records rather than ignores, so a test can see what a page did to
  // the address bar while it was loading.
  globalThis.history = {
    calls: [],
    replaceState(...args) {
      this.calls.push(args);
    },
  };
  globalThis.navigator = { userAgent: "node-test", clipboard: { writeText: async () => {} } };
  globalThis.alert = () => {};
  globalThis.confirm = () => true;
  globalThis.prompt = () => null;
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });
  globalThis.setTimeout = globalThis.setTimeout || (() => 0);
  return { elements, makeSelect, makeElement };
}

function sourceOf(file) {
  return fs.readFileSync(path.join(FRONTEND, file), "utf8");
}

/** The scripts a page loads from this site, in the order it loads them. */
function scriptsOf(page) {
  return [...sourceOf(page).matchAll(/<script src="(js\/[^"]+)"><\/script>/g)].map((m) => m[1]);
}

/** A page's own script, js/pages/<page>.js. */
function pageScript(page) {
  return sourceOf(`js/pages/${page.replace(/\.html$/, ".js")}`);
}

/** Loads the shared scripts (optionally with a page's script after them)
 * and hands back every function they declare at the top level, so tests
 * can call them directly. The shared scripts and their order are read
 * from the page itself, member.html when none is given, so a test loads
 * exactly what a browser would. */
function load(page, options = {}) {
  const knobs = installGlobals();
  // A page reads its query string while its script is being evaluated
  // (member.html takes ?invite= that way), so it has to be in place
  // before the script runs, not set afterwards.
  if (options.search) globalThis.location.search = options.search;

  // Top-level functions only: nested helpers are indented and out of scope.
  const declared = (src) => [
    ...src.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm),
  ].map((m) => m[1]);

  const files = scriptsOf(page || "member.html").filter((f) => f.startsWith("js/common/"));
  if (page) files.push(`js/pages/${page.replace(/\.html$/, ".js")}`);
  const source = files.map(sourceOf).join("\n");
  const factory = new Function(
    `${source}\nreturn { ${declared(source).join(", ")} };`
  );
  return { ...factory(), ...knobs };
}

module.exports = { load, pageScript, scriptsOf, sourceOf, installGlobals, makeElement, makeSelect, makeStore };
