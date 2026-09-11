// Runs the frontend's own code under `node --test`.
//
// There is no browser here and no build step, so shared.js and the
// pages' inline scripts are loaded as source into a function scope with
// the handful of browser globals they touch stubbed out. That is enough
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
  globalThis.history = { replaceState() {} };
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

/** The inline <script> of a page, minus the ones that only load a file. */
function inlineScript(page) {
  const html = sourceOf(page);
  const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)];
  return blocks.map((m) => m[1]).join("\n");
}

/** Loads shared.js (optionally with a page's script after it) and hands
 * back every function they declare, so tests can call them directly. */
function load(page) {
  const knobs = installGlobals();

  // Which functions are in scope to return depends on where they were
  // written: shared.js declares its own at no indent, while a page's
  // script sits inside a <script> block and declares them at two. A
  // single rule over the concatenation would sweep up nested helpers
  // (loadClubs inside initClubAndSeasonPickers) that aren't in scope.
  const declared = (src, indent) => [
    ...src.matchAll(
      new RegExp(`^${indent}(?:async\\s+)?function\\s+([A-Za-z_$][\\w$]*)`, "gm")
    ),
  ].map((m) => m[1]);

  const shared = sourceOf("shared.js");
  const parts = [shared];
  const names = declared(shared, "");
  if (page) {
    const script = inlineScript(page);
    parts.push(script);
    names.push(...declared(script, " {2}"));
  }
  const source = parts.join("\n");
  const factory = new Function(
    `${source}\nreturn { ${names.join(", ")} };`
  );
  return { ...factory(), ...knobs };
}

module.exports = { load, inlineScript, installGlobals, makeElement, makeSelect, makeStore };
