/**
 * Regression tests for public/stations.js.
 *
 * Fresh AMP + Acquire Connected Stations must not show the canned
 * "Node install has missing software(s)!" red banner. Named missing
 * software stays visible. Line Groups / password / GD URLs pass through.
 *
 * Run: node tests/js/test_stations_overlay.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    id: "",
    className: "",
    innerHTML: "",
    textContent: "",
    hidden: false,
    style: { backgroundColor: "", display: "" },
    children: [],
    parentElement: null,
    attributes: {},
    dataset: {},
    classList: {
      add: function () {},
      remove: function () {},
      toggle: function () {},
      contains: function () { return false; }
    },
    setAttribute: function (k, v) {
      this.attributes[k] = String(v);
      if (k.indexOf("data-") === 0) {
        const raw = k.slice(5);
        const camel = raw.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        this.dataset[camel] = String(v);
      }
    },
    getAttribute: function (k) {
      return this.attributes[k] != null ? this.attributes[k] : null;
    },
    appendChild: function (child) {
      this.children.push(child);
      child.parentElement = this;
      return child;
    },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; }
  };
  return el;
}

function collect(el, acc) {
  acc.push(el);
  (el.children || []).forEach((c) => collect(c, acc));
  return acc;
}

function loadOverlay(opts) {
  opts = opts || {};
  const html = makeEl("html");
  const body = makeEl("body");
  html.appendChild(body);

  const banner = makeEl("div");
  banner.style.backgroundColor = "#e53e3e";
  banner.style.color = "#fff";
  const pre = makeEl("pre");
  pre.textContent = opts.bannerText != null
    ? opts.bannerText
    : "Node install has missing software(s)!";
  banner.textContent = pre.textContent;
  banner.appendChild(pre);
  body.appendChild(banner);

  const keepBanner = makeEl("div");
  keepBanner.style.backgroundColor = "#e53e3e";
  const keepPre = makeEl("pre");
  keepPre.textContent = "You must select stations to be pinged.";
  keepBanner.textContent = keepPre.textContent;
  keepBanner.appendChild(keepPre);
  body.appendChild(keepBanner);

  const namedBanner = makeEl("div");
  namedBanner.style.backgroundColor = "#e53e3e";
  const namedPre = makeEl("pre");
  namedPre.textContent = "Node install is missing: nmap";
  namedBanner.textContent = namedPre.textContent;
  namedBanner.appendChild(namedPre);
  body.appendChild(namedBanner);

  const all = [];
  collect(html, all);

  const document = {
    readyState: "complete",
    documentElement: html,
    body: body,
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    querySelectorAll: function (sel) {
      if (sel === "pre, [style]" || sel === "pre") {
        return all.filter((n) => {
          if (n.tagName === "PRE") return true;
          if (sel === "pre") return false;
          return !!(n.style && n.style.backgroundColor);
        });
      }
      return [];
    },
    addEventListener: function () {},
    createElement: makeEl
  };

  const origCalls = [];
  const origFetch = async function (input, init) {
    origCalls.push({ input: input, init: init || {} });
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (String(url).indexOf("/stations/discover") !== -1) {
      return new Response(JSON.stringify(opts.discover || {
        success: false,
        stations: [],
        output: "Node install has missing software(s)!"
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    if (String(url).indexOf("/lines/groups") !== -1) {
      return new Response(JSON.stringify({ result: "success", groups: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    if (String(url).indexOf("/directory/global") !== -1) {
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  const window = {
    __STATIONS_TEST__: true,
    fetch: origFetch,
    location: {
      pathname: opts.path || "/stations",
      href: "http://localhost" + (opts.path || "/stations"),
      origin: "http://localhost"
    },
    addEventListener: function () {},
    setInterval: function () { return 0; },
    setTimeout: function (fn) { if (typeof fn === "function") fn(); return 0; }
  };

  const context = {
    window: window,
    document: document,
    location: window.location,
    history: {
      pushState: function () {},
      replaceState: function () {}
    },
    fetch: origFetch,
    Response: Response,
    URL: URL,
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
  window.fetch = origFetch;
  window.Response = Response;
  window.MutationObserver = context.MutationObserver;

  const src = fs.readFileSync(
    path.join(__dirname, "../../public/stations.js"),
    "utf8"
  );
  vm.runInNewContext(src, context, { filename: "stations.js" });

  return {
    window: window,
    document: document,
    banner: banner,
    keepBanner: keepBanner,
    namedBanner: namedBanner,
    origCalls: origCalls,
    test: window.__stationsTest,
    fetch: window.fetch
  };
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

  await check("isPage matches /stations only", async function () {
    const on = loadOverlay({ path: "/stations" });
    if (!on.test.isPage()) throw new Error("expected /stations");
    const off = loadOverlay({ path: "/lines/groups" });
    if (off.test.isPage()) throw new Error("line groups must not be stations page");
    const gd = loadOverlay({ path: "/global-directory" });
    if (gd.test.isPage()) throw new Error("GD must not be stations page");
  });

  await check("generic missing-software is an empty-state banner", async function () {
    const env = loadOverlay();
    if (!env.test.isGenericMissing("Node install has missing software(s)!")) {
      throw new Error("canned line should be generic");
    }
    if (!env.test.isEmptyStateBanner("No connected stations available")) {
      throw new Error("empty success text should be empty-state");
    }
    if (env.test.isEmptyStateBanner("You must select stations to be pinged.")) {
      throw new Error("ping error must stay visible");
    }
    if (env.test.isEmptyStateBanner("Node install is missing: nmap")) {
      throw new Error("named missing software must stay visible");
    }
  });

  await check("rewriteDiscover turns canned failure into empty success", async function () {
    const env = loadOverlay();
    const out = env.test.rewriteDiscover({
      success: false,
      stations: [],
      output: "Node install has missing software(s)!"
    });
    if (out.success !== true) throw new Error("expected success");
    if (out.stations.length) throw new Error("expected no stations");
    if (out.output) throw new Error("output should be cleared, got " + out.output);
  });

  await check("rewriteDiscover names packages when the binary lists them", async function () {
    const env = loadOverlay();
    const out = env.test.rewriteDiscover({
      success: false,
      stations: [],
      output: "Node install has missing software(s)! nmap sshpass"
    });
    if (out.success !== false) throw new Error("expected failure");
    if (out.output.indexOf("nmap") < 0) throw new Error("expected nmap: " + out.output);
    if (out.output.indexOf("missing software(s)") >= 0) {
      throw new Error("must not keep canned line: " + out.output);
    }
  });

  await check("rewriteDiscover leaves unrelated errors alone", async function () {
    const env = loadOverlay();
    const out = env.test.rewriteDiscover({
      success: false,
      stations: [],
      output: "Permission denied"
    });
    if (out.success !== false) throw new Error("expected failure");
    if (out.output !== "Permission denied") throw new Error("rewrote: " + out.output);
  });

  await check("rewriteDiscover keeps a populated station list", async function () {
    const env = loadOverlay();
    const stations = [{ id: "10.0.0.8", station: "10.0.0.8" }];
    const out = env.test.rewriteDiscover({
      success: false,
      stations: stations,
      output: "Node install has missing software(s)!"
    });
    if (out.success !== true) throw new Error("stations should win");
    if (out.stations[0].station !== "10.0.0.8") throw new Error("lost stations");
  });

  await check("fetch wrap rewrites generic discover JSON", async function () {
    const env = loadOverlay();
    const res = await env.fetch("/stations/discover", { method: "POST", body: "" });
    const data = await res.json();
    if (data.success !== true) throw new Error("expected rewritten success");
    if (data.output) throw new Error("expected empty output, got " + data.output);
    if (!env.origCalls.some(function (c) { return String(c.input).indexOf("/stations/discover") !== -1; })) {
      throw new Error("did not reach original fetch");
    }
  });

  await check("fetch wrap does not touch Line Groups or Global Directory", async function () {
    const env = loadOverlay();
    const lg = await env.fetch("/lines/groups/search", { method: "GET" });
    const lgData = await lg.json();
    if (lgData.result !== "success") throw new Error("line groups rewritten");
    const gd = await env.fetch("/directory/global/list", { method: "GET" });
    const gdData = await gd.json();
    if (!Array.isArray(gdData.items)) throw new Error("GD rewritten");
  });

  await check("hides canned and empty-success banners on /stations", async function () {
    const env = loadOverlay({
      bannerText: "Node install has missing software(s)!"
    });
    env.test.hideEmptyStateBanners();
    if (env.banner.style.display !== "none") {
      throw new Error("canned banner still visible");
    }
    if (env.keepBanner.style.display === "none") {
      throw new Error("ping error banner was hidden");
    }
    if (env.namedBanner.style.display === "none") {
      throw new Error("named missing-software banner was hidden");
    }
  });

  await check("hides No connected stations available banner", async function () {
    const env = loadOverlay({ bannerText: "No connected stations available" });
    env.test.hideEmptyStateBanners();
    if (env.banner.style.display !== "none") {
      throw new Error("empty-success banner still visible");
    }
  });

  await check("does not hide banners off /stations", async function () {
    const env = loadOverlay({
      path: "/lines/groups",
      bannerText: "Node install has missing software(s)!"
    });
    env.test.hideEmptyStateBanners();
    if (env.banner.style.display === "none") {
      throw new Error("hid a banner on Line Groups");
    }
  });

  if (failed) {
    console.error("\n" + failed + " test(s) failed");
    process.exit(1);
  }
  console.log("\nall tests passed");
}

main();
