/**
 * Regression tests for public/password-security.js user-save wrapFetch.
 *
 * The green SPA posts /users/add with a URLSearchParams body (not a string).
 * A missing reader treated that as a blank password and returned 422.
 *
 * Run: node tests/js/test_password_security_overlay.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const RULES_ERROR =
  "Password must be at least 8 characters and include a letter, a number, and a special character.";

function makeEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    id: "",
    className: "",
    innerHTML: "",
    textContent: "",
    hidden: false,
    type: tag === "input" ? "text" : "",
    value: "",
    children: [],
    parentElement: null,
    classList: {
      add: function () {},
      remove: function () {},
      toggle: function () {},
      contains: function () { return false; }
    },
    setAttribute: function () {},
    getAttribute: function () { return null; },
    addEventListener: function () {},
    appendChild: function (child) {
      this.children.push(child);
      child.parentElement = this;
      return child;
    },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    insertAdjacentElement: function () {},
    closest: function () { return null; }
  };
  return el;
}

function loadOverlay(domPassword) {
  const origCalls = [];
  const origFetch = async function (input, init) {
    origCalls.push({ input: input, init: init || {} });
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (String(url).indexOf("/session-check") !== -1) {
      return new Response(JSON.stringify({ must_change_password: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  const namedInput = makeEl("input");
  namedInput.name = "password";
  namedInput.type = "password";
  namedInput.value = domPassword || "";

  const document = {
    readyState: "complete",
    documentElement: makeEl("html"),
    body: makeEl("body"),
    getElementById: function (id) {
      if (id === "pw-force") {
        const el = makeEl("div");
        el.id = "pw-force";
        el.classList.add = function () {};
        el.classList.remove = function () {};
        el.querySelector = function (sel) {
          if (sel === "#pw-force-save") {
            const btn = makeEl("button");
            btn.addEventListener = function () {};
            return btn;
          }
          return null;
        };
        return el;
      }
      return null;
    },
    querySelector: function (sel) {
      if (String(sel).indexOf("name='password'") !== -1) return namedInput;
      return null;
    },
    querySelectorAll: function () { return []; },
    createElement: function (tag) { return makeEl(tag); },
    addEventListener: function () {}
  };

  const window = {
    fetch: origFetch,
    location: {
      pathname: "/users/new",
      origin: "http://localhost",
      href: "http://localhost/users/new",
      replace: function () {}
    },
    addEventListener: function () {},
    setInterval: noopTimer,
    setTimeout: function (fn) { if (typeof fn === "function") fn(); return 0; }
  };

  function noopTimer() { return 0; }

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
    Request: Request,
    URL: URL,
    URLSearchParams: URLSearchParams,
    FormData: FormData,
    Blob: Blob,
    TextDecoder: TextDecoder,
    setInterval: noopTimer,
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
  window.URL = URL;
  window.URLSearchParams = URLSearchParams;
  window.FormData = FormData;
  window.Blob = Blob;
  window.Request = Request;
  window.Response = Response;

  const src = fs.readFileSync(
    path.join(__dirname, "../../public/password-security.js"),
    "utf8"
  );
  vm.runInNewContext(src, context, { filename: "password-security.js" });

  return { fetch: window.fetch, origCalls: origCalls, namedInput: namedInput };
}

async function jsonOf(res) {
  return JSON.parse(await res.text());
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

  await check("URLSearchParams create with valid password reaches the server", async function () {
    const env = loadOverlay();
    const body = new URLSearchParams();
    body.set("username", "newadmin");
    body.set("password", "Monkey@123");
    body.set("role", "Admin");
    const res = await env.fetch("/users/add", { method: "POST", body: body });
    if (res.status === 422) {
      throw new Error("overlay blocked create: " + JSON.stringify(await jsonOf(res)));
    }
    if (env.origCalls.filter(function (c) {
      return String(c.input).indexOf("/users/add") !== -1;
    }).length !== 1) {
      throw new Error("expected one pass-through to /users/add, got " + env.origCalls.length);
    }
  });

  await check("JSON string create with user_password alt key reaches the server", async function () {
    const env = loadOverlay();
    const res = await env.fetch("/users/add", {
      method: "POST",
      body: JSON.stringify({ username: "u1", user_password: "Monkey@123" })
    });
    if (res.status === 422) throw new Error("blocked alt key: " + JSON.stringify(await jsonOf(res)));
  });

  await check("nested user.password JSON reaches the server", async function () {
    const env = loadOverlay();
    const res = await env.fetch("/users/add", {
      method: "POST",
      body: JSON.stringify({ user: { password: "Monkey@123" } })
    });
    if (res.status === 422) throw new Error("blocked nested: " + JSON.stringify(await jsonOf(res)));
  });

  await check("FormData create with valid password reaches the server", async function () {
    const env = loadOverlay();
    const fd = new FormData();
    fd.set("password", "Monkey@123");
    const res = await env.fetch("/users/add", { method: "POST", body: fd });
    if (res.status === 422) throw new Error("blocked FormData: " + JSON.stringify(await jsonOf(res)));
  });

  await check("fetch(Request) body is read when init.body is missing", async function () {
    const env = loadOverlay();
    const req = new Request("http://localhost/users/add", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "username=newadmin&password=Monkey%40123"
    });
    const res = await env.fetch(req);
    if (res.status === 422) throw new Error("blocked Request: " + JSON.stringify(await jsonOf(res)));
  });

  await check("POST /users/new with URLSearchParams is treated as create", async function () {
    const env = loadOverlay();
    const body = new URLSearchParams({ password: "Monkey@123" });
    const res = await env.fetch("/users/new", { method: "POST", body: body });
    if (res.status === 422) throw new Error("blocked /users/new: " + JSON.stringify(await jsonOf(res)));
  });

  await check("blank password on create still returns required", async function () {
    const env = loadOverlay();
    const body = new URLSearchParams({ username: "x", password: "" });
    const res = await env.fetch("/users/add", { method: "POST", body: body });
    if (res.status !== 422) throw new Error("expected 422, got " + res.status);
    const data = await jsonOf(res);
    if (data.errors.password !== "Password is required.") {
      throw new Error("unexpected error: " + JSON.stringify(data));
    }
    if (env.origCalls.some(function (c) { return String(c.input).indexOf("/users/add") !== -1; })) {
      throw new Error("blank create should not hit the server");
    }
  });

  await check("weak password still returns complexity message", async function () {
    const env = loadOverlay();
    const body = new URLSearchParams({ password: "monkey123" });
    const res = await env.fetch("/users/add", { method: "POST", body: body });
    if (res.status !== 422) throw new Error("expected 422, got " + res.status);
    const data = await jsonOf(res);
    if (data.errors.password !== RULES_ERROR) {
      throw new Error("unexpected error: " + JSON.stringify(data));
    }
  });

  await check("edit with blank password means keep unchanged", async function () {
    const env = loadOverlay();
    const body = new URLSearchParams({ first_name: "Alice" });
    const res = await env.fetch("/users/alice/edit", { method: "POST", body: body });
    if (res.status === 422) throw new Error("edit blank blocked: " + JSON.stringify(await jsonOf(res)));
    if (!env.origCalls.some(function (c) { return String(c.input).indexOf("/users/alice/edit") !== -1; })) {
      throw new Error("edit did not pass through");
    }
  });

  await check("unreadable body falls back to the visible User Password field", async function () {
    const env = loadOverlay("Monkey@123");
    const res = await env.fetch("/users/add", { method: "POST", body: { not: "a form body" } });
    if (res.status === 422) throw new Error("DOM fallback blocked: " + JSON.stringify(await jsonOf(res)));
  });

  await check("unreadable body with empty DOM still requires a password on create", async function () {
    const env = loadOverlay("");
    const res = await env.fetch("/users/add", { method: "POST", body: { not: "a form body" } });
    if (res.status !== 422) throw new Error("expected 422, got " + res.status);
    const data = await jsonOf(res);
    if (data.errors.password !== "Password is required.") {
      throw new Error("unexpected error: " + JSON.stringify(data));
    }
  });

  if (failed) {
    console.error("\n" + failed + " test(s) failed");
    process.exit(1);
  }
  console.log("\nall tests passed");
}

main();
