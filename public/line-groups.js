(function () {
  "use strict";

  var ROOT_ID = "lg-overlay";
  var state = {
    groups: [],
    lines: [],
    loaded: false,
    mode: "",
    main: "",
    subs: [],
    qMain: "",
    qSub: "",
    error: ""
  };

  function isPage() {
    var p = (location.pathname || "").replace(/\/+$/, "") || "/";
    return p === "/lines/groups" || p === "/lines/line-groups";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function groupMain(g) {
    if (!g || typeof g !== "object") return "";
    return String(g.main_line || g.main || g.name || "").trim();
  }

  function groupSubs(g) {
    if (!g || typeof g !== "object") return [];
    var raw = g.sub_lines != null ? g.sub_lines : (g.subs != null ? g.subs : g.lines);
    if (typeof raw === "string") {
      return raw.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    }
    if (!Array.isArray(raw)) return [];
    return raw.map(function (item) {
      if (item && typeof item === "object") {
        return String(item.name || item.dn || item.line || item.linename || "").trim();
      }
      return String(item || "").trim();
    }).filter(Boolean);
  }

  function otherMains(except) {
    var set = {};
    state.groups.forEach(function (g) {
      var m = groupMain(g);
      if (m && m !== except) set[m] = true;
    });
    return set;
  }

  function existingSubs() {
    var set = {};
    state.groups.forEach(function (g) {
      groupSubs(g).forEach(function (s) { set[s] = true; });
    });
    return set;
  }

  function substringMatch(name, q) {
    return String(name || "").toLowerCase().indexOf(String(q || "").toLowerCase()) >= 0;
  }

  async function fetchJson(url, opts) {
    var res = await fetch(url, Object.assign({ credentials: "include" }, opts || {}));
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("unauthorized");
    }
    var data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    return { res: res, data: data };
  }

  async function loadGroups() {
    var urls = ["/lines/line-groups/search", "/lines/groups/search"];
    for (var i = 0; i < urls.length; i++) {
      try {
        var out = await fetchJson(urls[i]);
        if (!out.res.ok) continue;
        var data = out.data;
        var list = Array.isArray(data) ? data : (data && Array.isArray(data.data) ? data.data : null);
        if (list) {
          state.groups = list;
          return;
        }
      } catch (e) {}
    }
    state.groups = [];
  }

  async function loadLines() {
    var names = [];
    try {
      var out = await fetchJson("/lines/names");
      if (out.res.ok && Array.isArray(out.data)) {
        names = out.data.map(function (x) {
          return typeof x === "string" ? x : (x && (x.name || x.dn) || "");
        }).filter(Boolean);
      }
    } catch (e) {}
    if (!names.length) {
      try {
        var out2 = await fetchJson("/lines/search?q=&per_page=200");
        if (out2.res.ok) {
          var items = Array.isArray(out2.data) ? out2.data : (out2.data.items || []);
          names = items.map(function (x) {
            return (x && (x.dn || x.name || x.linename)) || "";
          }).filter(Boolean);
        }
      } catch (e) {}
    }
    var seen = {};
    state.lines = names.filter(function (n) {
      if (seen[n]) return false;
      seen[n] = true;
      return true;
    });
  }

  function hideSpaEmpty() {
    var walk = document.querySelectorAll("h1, h2, h3, p, div, span, button");
    for (var i = 0; i < walk.length; i++) {
      var el = walk[i];
      if (el.id === "lg-modal-bg" || (el.closest && (el.closest("#" + ROOT_ID) || el.closest("#lg-modal-bg")))) continue;
      var t = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (t === "No line groups configured" || t === "+ Add Line Group" || t === "Add Line Group") {
        var box = el.closest("div");
        if (box && box.id !== ROOT_ID && box.id !== "lg-modal-bg" && !(box.closest && box.closest("#lg-modal-bg"))) {
          box.classList.add("lg-hide-spa");
        }
      }
    }
  }

  function suggestMain() {
    var q = state.qMain;
    var banned = existingSubs();
    var out = [];
    state.lines.forEach(function (n) {
      if (banned[n]) return;
      if (q && !substringMatch(n, q)) return;
      out.push(n);
    });
    return out.slice(0, 12);
  }

  function suggestSub() {
    var q = state.qSub;
    var banned = otherMains(state.main);
    banned[state.main] = true;
    state.subs.forEach(function (s) { banned[s] = true; });
    var out = [];
    state.lines.forEach(function (n) {
      if (banned[n]) return;
      if (q && !substringMatch(n, q)) return;
      out.push(n);
    });
    return out.slice(0, 12);
  }

  function renderTable(root) {
    var wrap = root.querySelector("#lg-table-wrap");
    var empty = root.querySelector("#lg-empty");
    var tbody = root.querySelector("#lg-tbody");
    if (!state.groups.length) {
      wrap.classList.add("hidden");
      empty.classList.remove("hidden");
      tbody.innerHTML = "";
      return;
    }
    empty.classList.add("hidden");
    wrap.classList.remove("hidden");
    tbody.innerHTML = state.groups.map(function (g) {
      var main = groupMain(g);
      var subs = groupSubs(g).join(", ");
      return '<tr data-main="' + escapeHtml(main) + '">' +
        "<td>" + escapeHtml(main) + "</td>" +
        "<td>" + escapeHtml(subs || "—") + "</td>" +
        '<td class="lg-actions">' +
          '<button type="button" class="lg-btn lg-edit">Edit</button>' +
          '<button type="button" class="lg-btn danger lg-del">Delete</button>' +
        "</td></tr>";
    }).join("");
    tbody.querySelectorAll(".lg-edit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openModal("edit", btn.closest("tr").dataset.main);
      });
    });
    tbody.querySelectorAll(".lg-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        deleteGroup(btn.closest("tr").dataset.main);
      });
    });
  }

  function renderModal() {
    var bg = document.getElementById("lg-modal-bg");
    if (!bg) return;
    if (!state.mode) {
      bg.classList.add("hidden");
      return;
    }
    bg.classList.remove("hidden");
    bg.querySelector("#lg-modal-title").textContent =
      state.mode === "edit" ? "Edit Line Group" : "Add Line Group";
    var mainInp = bg.querySelector("#lg-main");
    mainInp.value = state.mode === "edit" ? state.main : state.qMain;
    mainInp.readOnly = state.mode === "edit";
    bg.querySelector("#lg-sub").value = state.qSub;
    var chips = bg.querySelector("#lg-chips");
    chips.innerHTML = state.subs.map(function (s) {
      return '<span class="lg-chip">' + escapeHtml(s) +
        '<button type="button" data-sub="' + escapeHtml(s) + '" aria-label="Remove">&times;</button></span>';
    }).join("");
    chips.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        state.subs = state.subs.filter(function (x) { return x !== b.dataset.sub; });
        renderModal();
      });
    });
    var sm = bg.querySelector("#lg-suggest-main");
    var ss = bg.querySelector("#lg-suggest-sub");
    if (state.mode === "add" && (state.qMain || document.activeElement === mainInp)) {
      var mains = suggestMain();
      sm.innerHTML = mains.map(function (n) {
        return '<button type="button" data-val="' + escapeHtml(n) + '">' + escapeHtml(n) + "</button>";
      }).join("");
      sm.classList.toggle("hidden", !mains.length);
      sm.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("click", function () {
          state.main = b.dataset.val;
          state.qMain = b.dataset.val;
          state.subs = state.subs.filter(function (s) { return s !== state.main; });
          renderModal();
        });
      });
    } else {
      sm.innerHTML = "";
      sm.classList.add("hidden");
    }
    var subs = suggestSub();
    if (state.qSub) {
      ss.innerHTML = subs.map(function (n) {
        return '<button type="button" data-val="' + escapeHtml(n) + '">' + escapeHtml(n) + "</button>";
      }).join("");
      ss.classList.toggle("hidden", !subs.length);
      ss.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("click", function () {
          if (state.subs.indexOf(b.dataset.val) < 0) state.subs.push(b.dataset.val);
          state.qSub = "";
          renderModal();
          bg.querySelector("#lg-sub").focus();
        });
      });
    } else {
      ss.innerHTML = "";
      ss.classList.add("hidden");
    }
    var err = bg.querySelector("#lg-err");
    err.textContent = state.error || "";
    err.classList.toggle("hidden", !state.error);
  }

  function openModal(mode, main) {
    var g = state.groups.filter(function (x) { return groupMain(x) === main; })[0];
    state.mode = mode;
    state.main = mode === "edit" ? main : "";
    state.subs = mode === "edit" && g ? groupSubs(g).slice() : [];
    state.qMain = mode === "edit" ? main : "";
    state.qSub = "";
    state.error = "";
    renderModal();
  }

  function closeModal() {
    state.mode = "";
    state.error = "";
    renderModal();
  }

  async function saveGroup() {
    if (state.mode === "add") {
      if (!state.main) {
        state.error = "Main line is required.";
        renderModal();
        return;
      }
    }
    var url = state.mode === "edit"
      ? "/lines/groups/" + encodeURIComponent(state.main) + "/edit"
      : "/lines/groups";
    var fallback = state.mode === "edit"
      ? "/lines/line-groups/" + encodeURIComponent(state.main) + "/edit"
      : "/lines/line-groups/add";
    var body = JSON.stringify({ main_line: state.main, sub_lines: state.subs });
    var opts = {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: body
    };
    var out;
    try { out = await fetchJson(url, opts); } catch (e) { return; }
    if (!out.res.ok && out.res.status === 404) {
      try { out = await fetchJson(fallback, opts); } catch (e) { return; }
    }
    if (!out.res.ok) {
      var detail = (out.data && (out.data.detail || out.data.error || out.data.message)) || "Save failed";
      state.error = typeof detail === "string" ? detail : "Save failed";
      renderModal();
      return;
    }
    closeModal();
    await loadGroups();
    var root = document.getElementById(ROOT_ID);
    if (root) renderTable(root);
  }

  async function deleteGroup(main) {
    if (!main) return;
    if (!window.confirm("Delete line group " + main + "?")) return;
    var opts = {
      method: "POST",
      credentials: "include",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: "{}"
    };
    var out;
    try {
      out = await fetchJson("/lines/groups/" + encodeURIComponent(main) + "/delete", opts);
    } catch (e) { return; }
    if (!out.res.ok && out.res.status === 404) {
      try {
        out = await fetchJson("/lines/line-groups/" + encodeURIComponent(main) + "/delete", opts);
      } catch (e) { return; }
    }
    if (!out.res.ok) {
      var detail = (out.data && (out.data.detail || out.data.error)) || "Delete failed";
      window.alert(typeof detail === "string" ? detail : "Delete failed");
      return;
    }
    await loadGroups();
    var root = document.getElementById(ROOT_ID);
    if (root) renderTable(root);
  }

  function buildRoot() {
    var el = document.createElement("div");
    el.id = ROOT_ID;
    el.innerHTML =
      '<div class="lg-head"><div class="lg-title">Line Groups</div>' +
        '<button type="button" class="lg-btn primary" id="lg-add">+ Add Line Group</button></div>' +
      '<div class="lg-table-wrap hidden" id="lg-table-wrap">' +
        '<table id="lg-table"><thead><tr>' +
          "<th>Main Line</th><th>Sub Lines</th><th>Actions</th>" +
        '</tr></thead><tbody id="lg-tbody"></tbody></table></div>' +
      '<div class="lg-empty" id="lg-empty">No line groups configured.</div>';
    return el;
  }

  function ensureModal() {
    if (document.getElementById("lg-modal-bg")) return;
    var bg = document.createElement("div");
    bg.id = "lg-modal-bg";
    bg.className = "lg-modal-bg hidden";
    bg.innerHTML =
      '<div class="lg-modal" role="dialog" aria-modal="true">' +
        '<h3 id="lg-modal-title">Add Line Group</h3>' +
        '<div class="lg-field"><label for="lg-main">Main Line</label>' +
          '<input id="lg-main" type="text" autocomplete="off" placeholder="Search lines..." />' +
          '<div class="lg-suggest hidden" id="lg-suggest-main"></div></div>' +
        '<div class="lg-field"><label for="lg-sub">Sub Lines</label>' +
          '<input id="lg-sub" type="text" autocomplete="off" placeholder="Search lines..." />' +
          '<div class="lg-suggest hidden" id="lg-suggest-sub"></div>' +
          '<div class="lg-chips" id="lg-chips"></div></div>' +
        '<div class="lg-err hidden" id="lg-err"></div>' +
        '<div class="lg-modal-actions">' +
          '<button type="button" class="lg-btn" id="lg-cancel">Cancel</button>' +
          '<button type="button" class="lg-btn primary" id="lg-save">Save</button>' +
        "</div></div>";
    document.body.appendChild(bg);
    bg.addEventListener("click", function (ev) {
      if (ev.target === bg) closeModal();
    });
    bg.querySelector("#lg-cancel").addEventListener("click", closeModal);
    bg.querySelector("#lg-save").addEventListener("click", saveGroup);
    bg.querySelector("#lg-main").addEventListener("input", function (ev) {
      if (state.mode === "edit") return;
      state.qMain = ev.target.value;
      state.main = "";
      state.error = "";
      renderModal();
      bg.querySelector("#lg-main").focus();
    });
    bg.querySelector("#lg-sub").addEventListener("input", function (ev) {
      state.qSub = ev.target.value;
      state.error = "";
      renderModal();
      bg.querySelector("#lg-sub").focus();
    });
  }

  function findAnchor() {
    var h = null;
    var nodes = document.querySelectorAll("h1, h2, h3");
    for (var i = 0; i < nodes.length; i++) {
      var t = (nodes[i].textContent || "").replace(/\s+/g, " ").trim();
      if (t === "Line Groups" || t === "Lines") { h = nodes[i]; break; }
    }
    if (h) return h.parentElement || h;
    return document.querySelector("main") || document.getElementById("root") || document.body;
  }

  async function mount() {
    if (!isPage()) {
      teardown();
      return;
    }
    document.body.classList.add("lg-overlay-on");
    hideSpaEmpty();
    ensureModal();
    if (document.getElementById(ROOT_ID)) return;
    var anchor = findAnchor();
    if (!anchor) return;
    var root = buildRoot();
    if (anchor.parentElement) {
      anchor.parentElement.insertBefore(root, anchor.nextSibling);
    } else {
      document.body.appendChild(root);
    }
    root.querySelector("#lg-add").addEventListener("click", function () { openModal("add"); });
    if (!state.loaded) {
      await Promise.all([loadGroups(), loadLines()]);
      state.loaded = true;
    }
    renderTable(root);
    hideSpaEmpty();
  }

  function teardown() {
    document.body.classList.remove("lg-overlay-on");
    var el = document.getElementById(ROOT_ID);
    if (el) el.remove();
    var bg = document.getElementById("lg-modal-bg");
    if (bg) bg.remove();
    state.loaded = false;
    state.mode = "";
  }

  var lastPath = location.pathname;
  function tick() {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      state.loaded = false;
      if (!isPage()) teardown();
    }
    mount();
  }

  function start() {
    tick();
    setInterval(tick, 400);
    window.addEventListener("popstate", tick);
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
      if (isPage()) hideSpaEmpty();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
