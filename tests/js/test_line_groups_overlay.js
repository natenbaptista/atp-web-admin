/**
 * Regression tests for public/line-groups.js.
 *
 * Covers hideSpaEmpty / host placement (PR #25) plus delete payload shape,
 * unique sub-line, single MAIN LINE field, and --N appearance stripping.
 *
 * Run: node tests/js/test_line_groups_overlay.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function ClassList(el) {
  this._el = el;
  this._set = Object.create(null);
}

ClassList.prototype._syncFromName = function () {
  this._set = Object.create(null);
  String(this._el.className || "").split(/\s+/).forEach((c) => {
    if (c) this._set[c] = true;
  });
};

ClassList.prototype._write = function () {
  this._el.className = Object.keys(this._set).join(" ");
};

ClassList.prototype.add = function (c) {
  this._syncFromName();
  this._set[c] = true;
  this._write();
};

ClassList.prototype.remove = function (c) {
  this._syncFromName();
  delete this._set[c];
  this._write();
};

ClassList.prototype.toggle = function (c, force) {
  this._syncFromName();
  if (force === true) this._set[c] = true;
  else if (force === false) delete this._set[c];
  else if (this._set[c]) delete this._set[c];
  else this._set[c] = true;
  this._write();
};

ClassList.prototype.contains = function (c) {
  this._syncFromName();
  return !!this._set[c];
};

function tokenize(html) {
  const tokens = [];
  const re = /<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>|[^<]+/g;
  let m;
  while ((m = re.exec(html))) {
    const raw = m[0];
    if (raw.startsWith("<!--")) continue;
    if (raw.charAt(0) === "<") {
      const close = raw.charAt(1) === "/";
      const tagM = raw.match(/^<\/?\s*([a-zA-Z][\w-]*)/);
      if (!tagM) continue;
      const attrs = {};
      const attrRe = /([:@A-Za-z_][\w:-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?/g;
      let a;
      const inner = raw.replace(/^<\/?\s*[a-zA-Z][\w-]*\s*/, "").replace(/\/?>$/, "");
      while ((a = attrRe.exec(inner))) {
        attrs[a[1]] = a[2] != null ? a[2] : a[3] != null ? a[3] : a[4] != null ? a[4] : "";
      }
      tokens.push({
        type: close ? "close" : "open",
        tag: tagM[1].toLowerCase(),
        attrs: attrs,
        self: /\/>$/.test(raw) || /^(input|br|hr|img|meta|link)$/.test(tagM[1].toLowerCase())
      });
    } else if (raw.replace(/\s+/g, "")) {
      tokens.push({ type: "text", value: raw });
    }
  }
  return tokens;
}

function applyAttrs(el, attrs) {
  Object.keys(attrs).forEach((key) => {
    const val = attrs[key];
    if (key === "id") el.id = val;
    else if (key === "class") el.className = val;
    else if (key === "type") el.type = val;
    else if (key.indexOf("data-") === 0) {
      const raw = key.slice(5);
      const camel = raw.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      el.dataset[camel] = val;
    } else {
      el.setAttribute(key, val);
    }
  });
}

function makeEl(tag, document) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    id: "",
    className: "",
    _text: "",
    type: tag === "input" ? "text" : "",
    value: "",
    readOnly: false,
    hidden: false,
    children: [],
    parentElement: null,
    parentNode: null,
    ownerDocument: document,
    style: {},
    dataset: {},
    _attrs: {},
    _listeners: {},
    nodeType: 1
  };
  el.classList = new ClassList(el);

  Object.defineProperty(el, "textContent", {
    get: function () {
      if (!this.children.length) return this._text;
      return this.children.map((c) => c.textContent).join("");
    },
    set: function (v) {
      this.children = [];
      this._text = v == null ? "" : String(v);
    }
  });

  Object.defineProperty(el, "innerHTML", {
    get: function () {
      return this.textContent;
    },
    set: function (html) {
      this.children = [];
      this._text = "";
      parseInto(this, String(html == null ? "" : html), document);
    }
  });

  Object.defineProperty(el, "previousElementSibling", {
    get: function () {
      const p = this.parentElement;
      if (!p) return null;
      const idx = p.children.indexOf(this);
      return idx > 0 ? p.children[idx - 1] : null;
    }
  });

  Object.defineProperty(el, "nextSibling", {
    get: function () {
      const p = this.parentElement;
      if (!p) return null;
      const idx = p.children.indexOf(this);
      return idx >= 0 && idx + 1 < p.children.length ? p.children[idx + 1] : null;
    }
  });

  el.setAttribute = function (k, v) {
    this._attrs[k] = String(v);
    if (k === "id") this.id = String(v);
    if (k === "class") this.className = String(v);
  };
  el.getAttribute = function (k) {
    if (k === "id") return this.id || null;
    if (k === "class") return this.className || null;
    return this._attrs[k] != null ? this._attrs[k] : null;
  };
  el.addEventListener = function (type, fn) {
    (this._listeners[type] || (this._listeners[type] = [])).push(fn);
  };
  el.click = function () {
    const ev = { preventDefault: function () {}, stopPropagation: function () {}, target: this };
    (this._listeners.click || []).forEach((fn) => fn(ev));
  };
  el.focus = function () {
    document.activeElement = this;
  };
  el.appendChild = function (child) {
    if (child.parentElement) child.parentElement.removeChild(child);
    this.children.push(child);
    child.parentElement = this;
    child.parentNode = this;
    return child;
  };
  el.removeChild = function (child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    child.parentElement = null;
    child.parentNode = null;
    return child;
  };
  el.insertBefore = function (child, ref) {
    if (child.parentElement) child.parentElement.removeChild(child);
    const idx = ref ? this.children.indexOf(ref) : -1;
    if (idx < 0) this.children.push(child);
    else this.children.splice(idx, 0, child);
    child.parentElement = this;
    child.parentNode = this;
    return child;
  };
  el.remove = function () {
    if (this.parentElement) this.parentElement.removeChild(this);
  };
  el.matches = function (sel) {
    return matchSelector(this, sel);
  };
  el.closest = function (sel) {
    let n = this;
    while (n) {
      if (n.matches && n.matches(sel)) return n;
      n = n.parentElement;
    }
    return null;
  };
  el.querySelector = function (sel) {
    const all = collect(this, false);
    for (let i = 0; i < all.length; i++) {
      if (matchSelector(all[i], sel)) return all[i];
    }
    return null;
  };
  el.querySelectorAll = function (sel) {
    return collect(this, false).filter((n) => matchSelector(n, sel));
  };
  return el;
}

function parseInto(parent, html, document) {
  const tokens = tokenize(html);
  const stack = [parent];
  tokens.forEach((tok) => {
    if (tok.type === "text") {
      const node = makeEl("#text", document);
      node.textContent = tok.value;
      stack[stack.length - 1].appendChild(node);
      return;
    }
    if (tok.type === "close") {
      if (stack.length > 1) stack.pop();
      return;
    }
    const child = makeEl(tok.tag, document);
    applyAttrs(child, tok.attrs);
    stack[stack.length - 1].appendChild(child);
    if (!tok.self) stack.push(child);
  });
}

function collect(root, includeSelf) {
  const out = [];
  function walk(n) {
    if (includeSelf) out.push(n);
    (n.children || []).forEach((c) => {
      if (c.tagName !== "#TEXT") out.push(c);
      walk(c);
    });
  }
  if (includeSelf) walk(root);
  else (root.children || []).forEach((c) => {
    if (c.tagName !== "#TEXT") out.push(c);
    walk(c);
  });
  return out;
}

function matchOne(el, part) {
  part = part.trim();
  if (!part) return false;
  if (part.charAt(0) === "#") return el.id === part.slice(1);
  const m = part.match(/^([a-zA-Z][\w-]*)?((?:\.[a-zA-Z_][\w-]*)*)$/);
  if (!m) return false;
  if (m[1] && el.tagName !== m[1].toUpperCase()) return false;
  const classes = (m[2] || "").split(".").filter(Boolean);
  for (let i = 0; i < classes.length; i++) {
    if (!el.classList.contains(classes[i])) return false;
  }
  return true;
}

function matchSelector(el, sel) {
  const groups = String(sel).split(",");
  return groups.some((group) => {
    const parts = group.trim().split(/\s+/);
    if (parts.length === 1) return matchOne(el, parts[0]);
    // descendant: last part must match el; earlier parts match ancestors
    if (!matchOne(el, parts[parts.length - 1])) return false;
    let node = el.parentElement;
    for (let i = parts.length - 2; i >= 0; i--) {
      let found = false;
      while (node) {
        if (matchOne(node, parts[i])) {
          found = true;
          node = node.parentElement;
          break;
        }
        node = node.parentElement;
      }
      if (!found) return false;
    }
    return true;
  });
}

function hasHideClass(el) {
  let n = el;
  while (n) {
    if (n.classList && n.classList.contains("lg-hide-spa")) return true;
    n = n.parentElement;
  }
  return false;
}

function buildSpaPage(document, opts) {
  opts = opts || {};
  const root = document.getElementById("root");
  const header = document.createElement("header");
  const crumb = document.createElement("div");
  crumb.textContent = "enePath Production Lines Groups";
  const h1 = document.createElement("h1");
  h1.textContent = "Lines";
  header.appendChild(crumb);
  header.appendChild(h1);
  root.appendChild(header);

  const main = document.createElement("main");
  const page = document.createElement("div");
  page.className = "space-y-6 max-w-3xl";

  const toolbar = document.createElement("div");
  toolbar.className = "flex items-center justify-between";
  const h2 = document.createElement("h2");
  h2.textContent = "Line Groups";
  const spaAdd = document.createElement("button");
  spaAdd.textContent = opts.addLabel || "Add Line Group";
  toolbar.appendChild(h2);
  toolbar.appendChild(spaAdd);

  const card = document.createElement("div");
  card.className = "rounded-lg border";
  if (opts.withTable) {
    const table = document.createElement("table");
    table.id = "spa-lg-table";
    const td = document.createElement("td");
    td.textContent = "2400";
    const tr = document.createElement("tr");
    tr.appendChild(td);
    table.appendChild(tr);
    card.appendChild(table);
  } else {
    const empty = document.createElement("div");
    const p = document.createElement("p");
    p.textContent = "No line groups configured";
    const emptyAdd = document.createElement("button");
    emptyAdd.textContent = opts.emptyAddLabel || "Add Line Group";
    empty.appendChild(p);
    empty.appendChild(emptyAdd);
    card.appendChild(empty);
  }

  page.appendChild(toolbar);
  page.appendChild(card);
  main.appendChild(page);
  root.appendChild(main);
  document.body.appendChild(root);
  return { root: root, main: main, page: page, toolbar: toolbar, h2: h2, spaAdd: spaAdd, card: card };
}

function loadOverlay(opts) {
  opts = opts || {};
  const groups = opts.groups || [];
  const document = {
    readyState: "complete",
    activeElement: null,
    addEventListener: function () {}
  };
  const html = makeEl("html", document);
  const body = makeEl("body", document);
  const root = makeEl("div", document);
  root.id = "root";
  html.appendChild(body);
  document.documentElement = html;
  document.body = body;

  document.createElement = function (tag) { return makeEl(tag, document); };
  document.getElementById = function (id) {
    if (id === "root") return root;
    const all = collect(html, true);
    for (let i = 0; i < all.length; i++) {
      if (all[i].id === id) return all[i];
    }
    return null;
  };
  document.querySelector = function (sel) {
    const all = collect(html, true);
    for (let i = 0; i < all.length; i++) {
      if (matchSelector(all[i], sel)) return all[i];
    }
    return null;
  };
  document.querySelectorAll = function (sel) {
    return collect(html, true).filter((n) => matchSelector(n, sel));
  };

  const spa = buildSpaPage(document, opts);

  const fetchCalls = [];
  const fakeFetch = async function (url, init) {
    fetchCalls.push({ url: String(url), init: init || {} });
    const u = String(url);
    if (u.indexOf("/lines/line-groups/search") >= 0 || u.indexOf("/lines/groups/search") >= 0) {
      return {
        ok: true,
        status: 200,
        json: async function () { return groups; }
      };
    }
    if (u.indexOf("/lines/names") >= 0) {
      return {
        ok: true,
        status: 200,
        json: async function () { return opts.names != null ? opts.names : ["2400", "2401", "2402"]; }
      };
    }
    if (u.indexOf("/lines/search") >= 0) {
      return {
        ok: true,
        status: 200,
        json: async function () {
          return opts.search != null ? opts.search : { items: [] };
        }
      };
    }
    if ((init && init.method === "POST") || /\/(add|edit|delete)/.test(u)) {
      return {
        ok: true,
        status: 200,
        json: async function () { return { result: "success" }; }
      };
    }
    return { ok: false, status: 404, json: async function () { return {}; } };
  };

  const window = {
    __LG_TEST__: true,
    fetch: fakeFetch,
    location: {
      pathname: opts.path || "/lines/groups",
      href: "http://localhost/lines/groups",
      origin: "http://localhost"
    },
    addEventListener: function () {},
    setInterval: function () { return 0; },
    setTimeout: function (fn) { if (typeof fn === "function") fn(); return 0; },
    confirm: function () { return true; },
    alert: function () {}
  };

  const context = {
    window: window,
    document: document,
    location: window.location,
    history: {
      pushState: function () {},
      replaceState: function () {}
    },
    fetch: fakeFetch,
    setInterval: function () { return 0; },
    setTimeout: function (fn) { if (typeof fn === "function") fn(); return 0; },
    clearInterval: function () {},
    clearTimeout: function () {},
    MutationObserver: function () {
      this.observe = function () {};
      this.disconnect = function () {};
    },
    console: console
  };
  context.globalThis = context;
  window.window = window;
  window.document = document;
  window.history = context.history;
  window.MutationObserver = context.MutationObserver;

  const src = fs.readFileSync(
    path.join(__dirname, "../../public/line-groups.js"),
    "utf8"
  );
  vm.runInNewContext(src, context, { filename: "line-groups.js" });

  return {
    window: window,
    document: document,
    spa: spa,
    fetchCalls: fetchCalls,
    test: window.__lgTest
  };
}

async function flush(env) {
  if (env.test && env.test.mount) await env.test.mount();
}

async function main() {
  let failed = 0;
  async function check(name, fn) {
    try {
      await fn();
      console.log("ok  " + name);
    } catch (err) {
      failed += 1;
      console.error("FAIL " + name);
      console.error("  " + (err && err.stack ? err.stack : err));
    }
  }

  await check("isPage matches /lines/groups", async function () {
    const env = loadOverlay();
    if (!env.test.isPage()) throw new Error("expected isPage true");
  });

  await check("overlay stays visible after hideSpaEmpty on empty SPA card", async function () {
    const env = loadOverlay();
    await flush(env);
    const overlay = env.document.getElementById("lg-overlay");
    if (!overlay) throw new Error("overlay did not mount");
    env.test.hideSpaEmpty();
    env.test.hideSpaEmpty();
    if (hasHideClass(overlay)) {
      throw new Error("overlay (or an ancestor) has lg-hide-spa after hideSpaEmpty");
    }
    const empty = overlay.querySelector("#lg-empty");
    if (!empty || empty.classList.contains("hidden")) {
      throw new Error("overlay empty-state should stay shown when no groups");
    }
    const add = overlay.querySelector("#lg-add");
    if (!add || hasHideClass(add)) throw new Error("overlay Add was hidden");
  });

  await check("SPA empty card and Add button are hidden; header row is not", async function () {
    const env = loadOverlay();
    await flush(env);
    env.test.hideSpaEmpty();
    if (!env.spa.spaAdd.classList.contains("lg-hide-spa")) {
      throw new Error("SPA Add button should be hidden");
    }
    if (env.spa.toolbar.classList.contains("lg-hide-spa")) {
      throw new Error("toolbar row must not be hidden (it sits next to the overlay)");
    }
    if (env.spa.page.classList.contains("lg-hide-spa")) {
      throw new Error("page wrapper must not be hidden — it contains #lg-host");
    }
    if (!env.spa.card.classList.contains("lg-hide-spa") &&
        !env.spa.card.querySelector("p").classList.contains("lg-hide-spa") &&
        !hasHideClass(env.spa.card.querySelector("p"))) {
      throw new Error("SPA empty copy should be hidden");
    }
  });

  await check("host sits after the toolbar, not inside it", async function () {
    const env = loadOverlay();
    await flush(env);
    const host = env.document.getElementById("lg-host");
    if (!host) throw new Error("missing #lg-host");
    if (host.parentElement === env.spa.toolbar) {
      throw new Error("host was inserted inside the SPA Add toolbar");
    }
    if (host.previousElementSibling !== env.spa.toolbar) {
      throw new Error("host should be the sibling after the toolbar row");
    }
    if (env.spa.toolbar.children.indexOf(host) >= 0) {
      throw new Error("toolbar still contains host");
    }
  });

  await check("repeated hideSpaEmpty after table flash does not blank overlay", async function () {
    const env = loadOverlay({
      withTable: true,
      addLabel: "+ New line group",
      groups: [{ main_line: "2400", sub_lines: ["2401"] }]
    });
    await flush(env);
    env.test.hideSpaEmpty();
    env.test.hideSpaEmpty();
    const overlay = env.document.getElementById("lg-overlay");
    if (!overlay) throw new Error("overlay missing");
    if (hasHideClass(overlay) || hasHideClass(env.document.getElementById("lg-host"))) {
      throw new Error("table-page overlay was blanked");
    }
    const wrap = overlay.querySelector("#lg-table-wrap");
    if (!wrap || wrap.classList.contains("hidden")) {
      throw new Error("overlay table should be visible when groups exist");
    }
  });

  await check("Add still opens the modal after hideSpaEmpty", async function () {
    const env = loadOverlay();
    await flush(env);
    env.test.hideSpaEmpty();
    const add = env.document.getElementById("lg-add");
    add.click();
    if (env.test.state.mode !== "add") throw new Error("expected add mode, got " + env.test.state.mode);
    const bg = env.document.getElementById("lg-modal-bg");
    if (!bg || bg.classList.contains("hidden")) throw new Error("modal stayed hidden");
    if (hasHideClass(bg)) throw new Error("modal was marked lg-hide-spa");
  });

  await check("teardown on a non-groups page does not leave lg-overlay-on", async function () {
    const env = loadOverlay();
    await flush(env);
    env.window.location.pathname = "/lines";
    env.test.teardown();
    if (env.document.body.classList.contains("lg-overlay-on")) {
      throw new Error("lg-overlay-on leaked off Line Groups");
    }
    if (env.document.getElementById("lg-overlay")) {
      throw new Error("overlay should be removed off-page");
    }
  });

  await check("delete POST body is {main_line} not empty object", async function () {
    const env = loadOverlay({
      withTable: true,
      groups: [
        { main_line: "2402", sub_lines: ["2406", "2403"] },
        { main_line: "2404", sub_lines: ["2405", "2406"] }
      ]
    });
    await flush(env);
    const del = env.document.querySelector(".lg-del");
    if (!del) throw new Error("missing Delete button");
    env.fetchCalls.length = 0;
    del.click();
    await Promise.resolve();
    const delCall = env.fetchCalls.find((c) => /\/delete/.test(c.url) && c.init.method === "POST");
    if (!delCall) throw new Error("delete was not POSTed; calls=" + JSON.stringify(env.fetchCalls.map((c) => c.url)));
    let body;
    try { body = JSON.parse(delCall.init.body); } catch (e) {
      throw new Error("delete body is not JSON: " + delCall.init.body);
    }
    if (body.main_line !== "2402") {
      throw new Error("expected {main_line:'2402'}, got " + JSON.stringify(body));
    }
    if (Object.keys(body).join(",") !== "main_line") {
      throw new Error("delete body should be only main_line, got " + JSON.stringify(body));
    }
  });

  await check("picker strips --N appearance suffixes", async function () {
    const env = loadOverlay({
      names: [
        { name: "2407--1", type: "Line" },
        { name: "2407--2", type: "Line" },
        { name: "2407--3", type: "Line" },
        { name: "2401", type: "Line" }
      ]
    });
    await flush(env);
    if (env.test.state.lines.indexOf("2407--1") >= 0) {
      throw new Error("state.lines still has appearance label 2407--1: " + env.test.state.lines.join(","));
    }
    if (env.test.state.lines.indexOf("2407") < 0) {
      throw new Error("expected canonical 2407 in lines, got " + env.test.state.lines.join(","));
    }
    if (env.test.canonicalLineName("2407--2") !== "2407") {
      throw new Error("canonicalLineName(2407--2) => " + env.test.canonicalLineName("2407--2"));
    }
    env.test.state.qSub = "2407";
    env.test.state.main = "2401";
    const sugg = env.test.suggestSub();
    if (sugg.some((n) => /--\d+$/.test(n))) {
      throw new Error("suggestSub offered appearance labels: " + sugg.join(","));
    }
    if (sugg.indexOf("2407") < 0) {
      throw new Error("suggestSub should offer 2407, got " + sugg.join(","));
    }
    const collected = env.test.collectLineNames([
      { name: "2407--1" }, { dn: "2407--2" }, "2407--3", "2407"
    ]);
    if (collected.join(",") !== "2407") {
      throw new Error("collectLineNames should collapse appearances to 2407, got " + collected.join(","));
    }
  });

  await check("unique sub-line is blocked in picker and save", async function () {
    const env = loadOverlay({
      withTable: true,
      groups: [
        { main_line: "2402", sub_lines: ["2406", "2403"] },
        { main_line: "2404", sub_lines: ["2405", "2406"] }
      ],
      search: { items: [
        { dn: "2401" }, { dn: "2402" }, { dn: "2403" },
        { dn: "2404" }, { dn: "2405" }, { dn: "2406" }, { dn: "2407" }
      ] }
    });
    await flush(env);
    env.test.openModal("add");
    env.test.state.main = "2401";
    env.test.state.qMain = "2401";
    env.test.state.qSub = "240";
    const sugg = env.test.suggestSub();
    if (sugg.indexOf("2406") >= 0) {
      throw new Error("suggestSub offered already-used 2406: " + sugg.join(","));
    }
    if (sugg.indexOf("2403") >= 0 || sugg.indexOf("2405") >= 0) {
      throw new Error("suggestSub offered other groups' subs: " + sugg.join(","));
    }
    const clash = env.test.uniqueSubError(["2406"], "");
    if (!clash || clash.indexOf("2406") < 0) {
      throw new Error("uniqueSubError should name 2406, got " + clash);
    }
    env.fetchCalls.length = 0;
    env.test.state.subs = ["2406"];
    await env.test.saveGroup();
    const addCall = env.fetchCalls.find((c) => {
      const u = c.url;
      return c.init.method === "POST" && (u === "/lines/groups" || /\/add$/.test(u) || /\/edit$/.test(u));
    });
    if (addCall) {
      throw new Error("save POSTed a used sub-line: " + addCall.init.body);
    }
    if (!env.test.state.error || env.test.state.error.indexOf("2406") < 0) {
      throw new Error("expected unique-sub error, got " + env.test.state.error);
    }
  });

  await check("Add modal has one MAIN LINE field, not two", async function () {
    const env = loadOverlay({
      search: { items: [{ dn: "2401" }, { dn: "2407" }] }
    });
    await flush(env);
    env.test.openModal("add");
    const bg = env.document.getElementById("lg-modal-bg");
    if (!bg || bg.classList.contains("hidden")) throw new Error("modal hidden");
    const mains = bg.querySelectorAll("#lg-main");
    if (mains.length !== 1) {
      throw new Error("expected one #lg-main, got " + mains.length);
    }
    const labels = bg.querySelectorAll("label");
    const mainLabels = labels.filter((el) => /main line/i.test(el.textContent || ""));
    if (mainLabels.length !== 1) {
      throw new Error("expected one Main Line label, got " + mainLabels.length);
    }
    env.test.state.main = "";
    env.test.state.qMain = "2401";
    env.test.renderModal();
    if (env.test.state.main !== "2401") {
      throw new Error("exact Main Line type-in should auto-commit, got " + env.test.state.main);
    }
    const sm = bg.querySelector("#lg-suggest-main");
    if (sm && !sm.classList.contains("hidden") && sm.children.length) {
      throw new Error("committed Main Line still shows a second suggest editor");
    }
  });

  if (failed) {
    console.error("\n" + failed + " test(s) failed");
    process.exit(1);
  }
  console.log("\nall tests passed");
}

main();
