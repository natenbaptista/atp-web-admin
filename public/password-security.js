(function () {
  "use strict";

  var RULES = [
    "At least 8 characters",
    "At least one letter, one number, and one special character",
    "New users, and users whose password is reset by an admin, must change this password at next login"
  ];
  var RULES_ERROR =
    "Password must be at least 8 characters and include a letter, a number, and a special character.";
  var SAME_ERROR = "New password must be different from the current password.";

  var state = {
    must: false,
    checked: false,
    submitting: false
  };
  var fetchWrapped = false;
  // Invalidates in-flight /session-check so a stale 401 from /login cannot
  // clear a lock that login just set.
  var checkGen = 0;

  var EYE_OFF =
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/></svg>';
  var EYE_ON =
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/></svg>';

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pathOf() {
    return (location.pathname || "").replace(/\/+$/, "") || "/";
  }

  function isLogin() {
    return pathOf() === "/login";
  }

  function lockUrl(url) {
    if (!state.must) return url;
    var next = "/";
    if (url == null || url === "") {
      next = pathOf();
    } else {
      try {
        next = new URL(String(url), location.origin).pathname.replace(/\/+$/, "") || "/";
      } catch (e) {
        next = String(url).split("?")[0].replace(/\/+$/, "") || "/";
      }
    }
    if (next === "/login" || next === "/change-password" || next === "/reset-password" || next === "/logout") {
      return url;
    }
    // Stay on /login if that is the current page so first login never enters the app.
    return isLogin() ? "/login" : "/change-password";
  }

  function isUserForm() {
    var p = pathOf();
    return /\/users\/(add|new)$/.test(p) || /\/users\/[^/]+\/(edit|copy)$/.test(p);
  }

  function isResetPage() {
    var p = pathOf();
    return p === "/reset-password" || p === "/change-password";
  }

  function passwordValid(pw) {
    if (!pw || pw.length < 8) return false;
    var letter = false, digit = false, special = false;
    for (var i = 0; i < pw.length; i++) {
      var c = pw.charAt(i);
      if (/\d/.test(c)) digit = true;
      else if (c.toLowerCase() !== c.toUpperCase()) letter = true;
      else special = true;
    }
    return letter && digit && special;
  }

  function urlString(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    try { return String(input); } catch (e) { return ""; }
  }

  function pathFromUrl(url) {
    try {
      return new URL(url, location.origin).pathname;
    } catch (e) {
      return String(url || "").split("?")[0];
    }
  }

  var PW_KEYS = ["password", "user_password", "userPassword", "user[password]"];
  var NEW_PW_KEYS = ["new_password", "newPassword"];
  var CUR_PW_KEYS = ["current_password", "currentPassword"];

  function emptyPwFields() {
    return { password: "", new_password: "", current_password: "" };
  }

  function firstKey(get, keys) {
    for (var i = 0; i < keys.length; i++) {
      var v = get(keys[i]);
      if (v != null && String(v) !== "") return String(v);
    }
    return "";
  }

  function fieldsFromGet(get, nestedGet) {
    var fields = {
      password: firstKey(get, PW_KEYS),
      new_password: firstKey(get, NEW_PW_KEYS),
      current_password: firstKey(get, CUR_PW_KEYS)
    };
    if (!fields.password && nestedGet) {
      fields.password = firstKey(nestedGet, PW_KEYS);
    }
    return fields;
  }

  function fieldsFromObject(obj) {
    if (!obj || typeof obj !== "object") return emptyPwFields();
    return fieldsFromGet(
      function (k) { return obj[k]; },
      obj.user && typeof obj.user === "object"
        ? function (k) { return obj.user[k]; }
        : null
    );
  }

  function fieldsFromString(body) {
    var trimmed = String(body).replace(/^\s+/, "");
    if (trimmed.charAt(0) === "{") {
      try { return fieldsFromObject(JSON.parse(body)); } catch (e) {}
    }
    try {
      var p = new URLSearchParams(body);
      return fieldsFromGet(function (k) { return p.get(k); }, null);
    } catch (e) {}
    return emptyPwFields();
  }

  // readable: false means we could not parse a password payload (not "blank password").
  function passwordFromBody(body) {
    if (body == null) return { fields: emptyPwFields(), readable: false };
    if (body === "") return { fields: emptyPwFields(), readable: true };
    if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) {
      return { fields: fieldsFromGet(function (k) { return body.get(k); }, null), readable: true };
    }
    if (typeof FormData !== "undefined" && body instanceof FormData) {
      return { fields: fieldsFromGet(function (k) { return body.get(k); }, null), readable: true };
    }
    if (typeof body === "string") {
      return { fields: fieldsFromString(body), readable: true };
    }
    return { fields: emptyPwFields(), readable: false };
  }

  function passwordFromDom() {
    if (typeof document === "undefined") return "";
    var label = findLabel("User Password");
    if (label) {
      var box = fieldContainer(label);
      var input = box && box.querySelector &&
        box.querySelector("input[type='password'], input[type='text']");
      if (input && input.value) return String(input.value);
    }
    var named = document.querySelector(
      "input[name='password'], input[name='user_password'], input[name='userPassword']"
    );
    return named && named.value ? String(named.value) : "";
  }

  function bytesToText(body) {
    try {
      var bytes = null;
      if (typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer) {
        bytes = new Uint8Array(body);
      } else if (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView && ArrayBuffer.isView(body)) {
        bytes = new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
      }
      if (!bytes) return null;
      return typeof TextDecoder !== "undefined"
        ? new TextDecoder().decode(bytes)
        : String.fromCharCode.apply(null, bytes);
    } catch (e) {
      return null;
    }
  }

  function resolvePasswordFields(input, init) {
    var body = init && init.body;
    var parsed = passwordFromBody(body);
    if (parsed.readable) return Promise.resolve(parsed.fields);

    function useDom() {
      var fields = emptyPwFields();
      fields.password = passwordFromDom();
      return fields;
    }

    if (typeof Request !== "undefined" && input instanceof Request && !input.bodyUsed) {
      try {
        return input.clone().text().then(function (text) {
          var fromReq = passwordFromBody(text);
          return fromReq.readable ? fromReq.fields : useDom();
        }).catch(useDom);
      } catch (e) {
        return Promise.resolve(useDom());
      }
    }

    if (body && typeof Blob !== "undefined" && body instanceof Blob && typeof body.text === "function") {
      return body.text().then(function (text) {
        var fromBlob = passwordFromBody(text);
        return fromBlob.readable ? fromBlob.fields : useDom();
      }).catch(useDom);
    }

    var decoded = bytesToText(body);
    if (decoded != null) {
      var fromBytes = passwordFromBody(decoded);
      if (fromBytes.readable) return Promise.resolve(fromBytes.fields);
    }

    return Promise.resolve(useDom());
  }

  function isUserSavePath(pathname) {
    return /\/users\/(add|new)$/.test(pathname) ||
      /\/users\/[^/]+\/(edit|copy)$/.test(pathname);
  }

  function jsonResponse(status, obj) {
    return new Response(JSON.stringify(obj), {
      status: status,
      headers: { "Content-Type": "application/json", "Accept": "application/json" }
    });
  }

  function looksLikeMustChange(data) {
    return !!(data && typeof data === "object" && !Array.isArray(data) && data.must_change_password);
  }

  function applyMustChangeGate(res) {
    if (res.status === 403 || ((res.headers && res.headers.get && (res.headers.get("content-type") || "")).indexOf("json") !== -1)) {
      return res.clone().json().then(function (data) {
        if (!looksLikeMustChange(data)) return res;
        lockSession();
        return res;
      }).catch(function () { return res; });
    }
    return Promise.resolve(res);
  }

  function wrapFetch() {
    if (fetchWrapped || typeof window.fetch !== "function") return;
    fetchWrapped = true;
    var orig = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      var args = arguments;
      var self = this;
      var url = urlString(input);
      var pathname = pathFromUrl(url);
      var method = String(init.method || (input && input.method) || "GET").toUpperCase();

      function passThrough() {
        return orig.apply(self, args).then(function (res) {
          if (method === "POST" && (pathname === "/login" || pathname === "/api/user/login")) {
            res.clone().json().then(function (data) {
              if (data && data.must_change_password) {
                lockSession();
              } else if (data && data.username) {
                state.must = false;
                state.checked = true;
                hideForce();
              }
            }).catch(function () {});
          }
          if (method === "POST" && (pathname === "/reset-password" || pathname === "/change-password") && res.ok) {
            state.must = false;
            hideForce();
          }
          return applyMustChangeGate(res);
        });
      }

      if (method === "POST" && isUserSavePath(pathname)) {
        var isEdit = /\/users\/[^/]+\/edit$/.test(pathname);
        return resolvePasswordFields(input, init).then(function (fields) {
          if (fields.password) {
            if (!passwordValid(fields.password)) {
              showInlineError(RULES_ERROR);
              return jsonResponse(422, { errors: { password: RULES_ERROR } });
            }
          } else if (!isEdit) {
            showInlineError("Password is required.");
            return jsonResponse(422, { errors: { password: "Password is required." } });
          }
          return passThrough();
        });
      }
      if (method === "POST" && (pathname === "/reset-password" || pathname === "/change-password")) {
        return resolvePasswordFields(input, init).then(function (rp) {
          if (rp.new_password && !passwordValid(rp.new_password)) {
            return jsonResponse(400, { error: RULES_ERROR });
          }
          if (rp.new_password && rp.current_password && rp.new_password === rp.current_password) {
            return jsonResponse(400, { error: SAME_ERROR });
          }
          return passThrough();
        });
      }
      return passThrough();
    };
  }

  function findLabel(exact) {
    var nodes = document.querySelectorAll("label, p, span, div, h2, h3, legend");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.id === "pw-rules" || el.classList && el.classList.contains("pw-rules")) continue;
      if (el.closest && (el.closest("#pw-rules") || el.closest("#pw-force") || el.closest(".pw-rules"))) continue;
      if (el.children && el.children.length > 4) continue;
      var t = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (t === exact || t === exact + " *" || t === exact + "*") return el;
    }
    return null;
  }

  function fieldContainer(labelEl) {
    var box = labelEl;
    for (var up = 0; up < 8 && box; up++) {
      if (box.querySelector && box.querySelector("input")) return box;
      box = box.parentElement;
    }
    return labelEl.parentElement;
  }

  function injectRules() {
    if (!isUserForm() && !isResetPage()) {
      var old = document.getElementById("pw-rules");
      if (old) old.remove();
      return;
    }
    if (document.getElementById("pw-rules")) return;
    var label = isUserForm() ? findLabel("User Password") : findLabel("New Password");
    if (!label) return;
    var box = fieldContainer(label);
    if (!box) return;
    var hint = document.createElement("ul");
    hint.id = "pw-rules";
    hint.className = "pw-rules";
    hint.innerHTML = RULES.map(function (r) {
      return "<li>" + escapeHtml(r) + "</li>";
    }).join("");
    var err = document.createElement("div");
    err.id = "pw-field-error";
    err.className = "pw-field-error";
    err.hidden = true;
    var input = box.querySelector("input[type='password'], input[type='text']");
    if (input && input.parentElement) {
      input.parentElement.insertAdjacentElement("afterend", hint);
      hint.insertAdjacentElement("afterend", err);
    } else {
      box.appendChild(hint);
      box.appendChild(err);
    }
  }

  function showInlineError(msg) {
    injectRules();
    var err = document.getElementById("pw-field-error");
    if (!err) return;
    err.hidden = !msg;
    err.textContent = msg || "";
  }

  function ensureForce() {
    if (document.getElementById("pw-force")) return;
    var el = document.createElement("div");
    el.id = "pw-force";
    el.className = "hidden";
    el.innerHTML =
      '<div class="pw-card" role="dialog" aria-modal="true" aria-labelledby="pw-force-title">' +
        '<h2 id="pw-force-title">Change your password</h2>' +
        '<p class="pw-lead">You must set a new password before using the app.</p>' +
        '<label for="pw-cur">Current Password</label>' +
        passwordFieldHtml("pw-cur", "current-password") +
        '<label for="pw-new">New Password</label>' +
        passwordFieldHtml("pw-new", "new-password") +
        '<ul class="pw-rules">' +
          RULES.map(function (r) { return "<li>" + escapeHtml(r) + "</li>"; }).join("") +
        "</ul>" +
        '<label for="pw-confirm">Confirm New Password</label>' +
        passwordFieldHtml("pw-confirm", "new-password") +
        '<div class="pw-err hidden" id="pw-force-err"></div>' +
        '<div class="pw-actions"><button type="button" id="pw-force-save">Change Password</button></div>' +
      "</div>";
    document.body.appendChild(el);
    bindEyeToggles(el);
    el.querySelector("#pw-force-save").addEventListener("click", submitForce);
    el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") submitForce();
    });
  }

  function passwordFieldHtml(id, autocomplete) {
    return (
      '<div class="pw-field">' +
        '<input id="' + id + '" type="password" autocomplete="' + autocomplete + '" />' +
        '<button type="button" class="pw-eye" aria-label="Show password" aria-pressed="false">' +
          EYE_OFF +
        "</button>" +
      "</div>"
    );
  }

  function bindEyeToggles(root) {
    var buttons = root.querySelectorAll(".pw-eye");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function (ev) {
        ev.preventDefault();
        var btn = ev.currentTarget;
        var field = btn.parentElement && btn.parentElement.querySelector("input");
        if (!field) return;
        var hide = field.type === "text";
        field.type = hide ? "password" : "text";
        btn.setAttribute("aria-pressed", hide ? "false" : "true");
        btn.setAttribute("aria-label", hide ? "Show password" : "Hide password");
        btn.innerHTML = hide ? EYE_OFF : EYE_ON;
      });
    }
  }

  function lockSession() {
    state.must = true;
    state.checked = true;
    checkGen += 1;
    showForce();
    // Full navigation so the SPA cannot mount dashboard/list pages first.
    if (pathOf() !== "/change-password" && pathOf() !== "/reset-password") {
      window.location.replace("/change-password");
    }
  }

  function stayOnLockPage() {
    if (!state.must) return;
    if (pathOf() === "/change-password" || pathOf() === "/reset-password") return;
    window.location.replace("/change-password");
  }

  function showForce() {
    if (!state.must) {
      hideForce();
      return;
    }
    ensureForce();
    var el = document.getElementById("pw-force");
    if (!el) return;
    el.classList.remove("hidden");
    document.body.classList.add("pw-force-on");
  }

  function hideForce() {
    var el = document.getElementById("pw-force");
    if (el) el.classList.add("hidden");
    document.body.classList.remove("pw-force-on");
  }

  function setForceError(msg) {
    var err = document.getElementById("pw-force-err");
    if (!err) return;
    err.textContent = msg || "";
    err.classList.toggle("hidden", !msg);
  }

  async function submitForce() {
    if (state.submitting) return;
    var cur = (document.getElementById("pw-cur") || {}).value || "";
    var neu = (document.getElementById("pw-new") || {}).value || "";
    var conf = (document.getElementById("pw-confirm") || {}).value || "";
    if (!cur || !neu || !conf) {
      setForceError("All fields are required.");
      return;
    }
    if (neu !== conf) {
      setForceError("Passwords do not match.");
      return;
    }
    if (!passwordValid(neu)) {
      setForceError(RULES_ERROR);
      return;
    }
    if (neu === cur) {
      setForceError(SAME_ERROR);
      return;
    }
    state.submitting = true;
    var btn = document.getElementById("pw-force-save");
    if (btn) btn.disabled = true;
    setForceError("");
    try {
      var res = await window.fetch("/reset-password", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ current_password: cur, new_password: neu })
      });
      var data = {};
      try { data = await res.json(); } catch (e) { data = {}; }
      if (!res.ok) {
        setForceError((data && (data.error || data.detail)) || "Failed to change password.");
        return;
      }
      state.must = false;
      hideForce();
      window.location.href = "/dashboard";
    } catch (e) {
      setForceError("Network error. Please try again.");
    } finally {
      state.submitting = false;
      if (btn) btn.disabled = false;
    }
  }

  async function refreshFlag() {
    var gen = ++checkGen;
    try {
      var res = await fetch("/session-check", { credentials: "include" });
      if (gen !== checkGen) return;
      if (res.status === 401) {
        state.must = false;
        hideForce();
        return;
      }
      if (!res.ok) return;
      var data = await res.json();
      if (gen !== checkGen) return;
      state.must = !!(data && data.must_change_password);
      state.checked = true;
      if (state.must) {
        showForce();
        stayOnLockPage();
      } else {
        hideForce();
      }
    } catch (e) {}
  }

  function tick() {
    injectRules();
    if (state.must) showForce();
  }

  function onNav() {
    tick();
    refreshFlag();
  }

  function start() {
    wrapFetch();
    ensureForce();
    var wrapPush = history.pushState;
    var wrapReplace = history.replaceState;
    history.pushState = function (data, unused, url) {
      wrapPush.call(this, data, unused, lockUrl(url));
      setTimeout(onNav, 0);
    };
    history.replaceState = function (data, unused, url) {
      wrapReplace.call(this, data, unused, lockUrl(url));
      setTimeout(onNav, 0);
    };
    if (pathOf() === "/change-password") {
      state.must = true;
      showForce();
    }
    refreshFlag();
    tick();
    setInterval(tick, 400);
    setInterval(refreshFlag, 15000);
    window.addEventListener("popstate", onNav);
    var obs = new MutationObserver(function () {
      injectRules();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
