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

  function passwordFromBody(body) {
    if (!body) return { password: "", new_password: "", current_password: "" };
    if (typeof body === "string") {
      var trimmed = body.replace(/^\s+/, "");
      if (trimmed.charAt(0) === "{") {
        try {
          var obj = JSON.parse(body);
          return {
            password: obj.password || "",
            new_password: obj.new_password || "",
            current_password: obj.current_password || ""
          };
        } catch (e) {}
      }
      try {
        var p = new URLSearchParams(body);
        return {
          password: p.get("password") || "",
          new_password: p.get("new_password") || "",
          current_password: p.get("current_password") || ""
        };
      } catch (e) {}
    }
    if (typeof FormData !== "undefined" && body instanceof FormData) {
      return {
        password: body.get("password") || "",
        new_password: body.get("new_password") || "",
        current_password: body.get("current_password") || ""
      };
    }
    return { password: "", new_password: "", current_password: "" };
  }

  function isUserSavePath(pathname) {
    return /\/users\/add$/.test(pathname) ||
      /\/users\/[^/]+\/(edit|copy)$/.test(pathname);
  }

  function jsonResponse(status, obj) {
    return new Response(JSON.stringify(obj), {
      status: status,
      headers: { "Content-Type": "application/json", "Accept": "application/json" }
    });
  }

  function wrapFetch() {
    if (fetchWrapped || typeof window.fetch !== "function") return;
    fetchWrapped = true;
    var orig = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      var url = urlString(input);
      var pathname = pathFromUrl(url);
      var method = String(init.method || (input && input.method) || "GET").toUpperCase();
      if (method === "POST" && isUserSavePath(pathname)) {
        var fields = passwordFromBody(init.body);
        var isEdit = /\/users\/[^/]+\/edit$/.test(pathname);
        if (fields.password) {
          if (!passwordValid(fields.password)) {
            showInlineError(RULES_ERROR);
            return Promise.resolve(jsonResponse(422, { errors: { password: RULES_ERROR } }));
          }
        } else if (!isEdit) {
          showInlineError("Password is required.");
          return Promise.resolve(jsonResponse(422, { errors: { password: "Password is required." } }));
        }
      }
      if (method === "POST" && (pathname === "/reset-password" || pathname === "/change-password")) {
        var rp = passwordFromBody(init.body);
        if (rp.new_password && !passwordValid(rp.new_password)) {
          return Promise.resolve(jsonResponse(400, { error: RULES_ERROR }));
        }
        if (rp.new_password && rp.current_password && rp.new_password === rp.current_password) {
          return Promise.resolve(jsonResponse(400, { error: SAME_ERROR }));
        }
      }
      return orig.apply(this, arguments).then(function (res) {
        if (method === "POST" && (pathname === "/login" || pathname === "/api/user/login")) {
          res.clone().json().then(function (data) {
            if (data && data.must_change_password) {
              state.must = true;
              state.checked = true;
              showForce();
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
        return res;
      });
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
        '<input id="pw-cur" type="password" autocomplete="current-password" />' +
        '<label for="pw-new">New Password</label>' +
        '<input id="pw-new" type="password" autocomplete="new-password" />' +
        '<ul class="pw-rules">' +
          RULES.map(function (r) { return "<li>" + escapeHtml(r) + "</li>"; }).join("") +
        "</ul>" +
        '<label for="pw-confirm">Confirm New Password</label>' +
        '<input id="pw-confirm" type="password" autocomplete="new-password" />' +
        '<div class="pw-err hidden" id="pw-force-err"></div>' +
        '<div class="pw-actions"><button type="button" id="pw-force-save">Change Password</button></div>' +
      "</div>";
    document.body.appendChild(el);
    el.querySelector("#pw-force-save").addEventListener("click", submitForce);
    el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") submitForce();
    });
  }

  function showForce() {
    if (isLogin() || !state.must) {
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
    if (isLogin()) {
      state.must = false;
      hideForce();
      return;
    }
    try {
      var res = await fetch("/session-check", { credentials: "include" });
      if (res.status === 401) {
        state.must = false;
        hideForce();
        return;
      }
      if (!res.ok) return;
      var data = await res.json();
      state.must = !!(data && data.must_change_password);
      state.checked = true;
      if (state.must) showForce();
      else hideForce();
    } catch (e) {}
  }

  function tick() {
    injectRules();
    if (state.must && !isLogin()) showForce();
    if (isLogin()) hideForce();
  }

  function start() {
    wrapFetch();
    ensureForce();
    refreshFlag();
    tick();
    setInterval(tick, 400);
    setInterval(refreshFlag, 15000);
    window.addEventListener("popstate", function () {
      tick();
      refreshFlag();
    });
    var wrapPush = history.pushState;
    var wrapReplace = history.replaceState;
    history.pushState = function () {
      wrapPush.apply(this, arguments);
      setTimeout(tick, 0);
    };
    history.replaceState = function () {
      wrapReplace.apply(this, arguments);
      setTimeout(tick, 0);
    };
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
