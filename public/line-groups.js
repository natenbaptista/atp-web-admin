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
    if (p === "/lines/groups" || p === "/lines/line-groups" || p === "/lines/line_group") return true;
    return /\/lines\/.*group/.test(p);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Virtual-line appearances are stored as "<line>--<n>" (e.g. 2407--1).
  // Line Groups operate on the real line name; never display or POST --N.
  function canonicalLineName(name) {
    var s = String(name == null ? "" : name).trim();
    if (!s) return "";
    var m = s.match(/^(.*)--(\d+)$/);
    return m ? m[1] : s;
  }

  function groupMain(g) {
    if (!g || typeof g !== "object") return "";
    return canonicalLineName(g.main_line || g.main || g.name || "");
  }

  function groupSubs(g) {
    if (!g || typeof g !== "object") return [];
    var raw = g.sub_lines != null ? g.sub_lines : (g.subs != null ? g.subs : g.lines);
    if (typeof raw === "string") {
      return raw.split(",").map(function (s) { return canonicalLineName(s); }).filter(Boolean);
    }
    if (!Array.isArray(raw)) return [];
    return raw.map(function (item) {
      if (item && typeof item === "object") {
        return canonicalLineName(item.name || item.dn || item.line || item.linename || "");
      }
      return canonicalLineName(item);
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

  function existingSubs(exceptMain) {
    var set = {};
    state.groups.forEach(function (g) {
      if (exceptMain && groupMain(g) === exceptMain) return;
      groupSubs(g).forEach(function (s) { set[s] = true; });
    });
    return set;
  }

  function collectLineNames(raw) {
    var list = Array.isArray(raw) ? raw : (raw && (raw.items || raw.data));
    if (!Array.isArray(list)) return [];
    var names = [];
    var seen = {};
    list.forEach(function (x) {
      var n = "";
      if (typeof x === "string") n = x;
      else if (x && typeof x === "object") n = x.dn || x.name || x.linename || "";
      n = canonicalLineName(n);
      if (!n || seen[n]) return;
      seen[n] = true;
      names.push(n);
    });
    return names;
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
    // /lines/search returns real DNs. /lines/names is virtual_lines and
    // includes appearance labels (2407--1); only use it as a fallback.
    try {
      var out2 = await fetchJson("/lines/search?q=&per_page=200");
      if (out2.res.ok) names = collectLineNames(out2.data);
    } catch (e) {}
    if (!names.length) {
      try {
        var out = await fetchJson("/lines/names");
        if (out.res.ok) names = collectLineNames(out.data);
      } catch (e) {}
    }
    state.lines = names;
  }

  function isOurs(el) {
    if (!el) return false;
    if (el.id === ROOT_ID || el.id === "lg-modal-bg" || el.id === "lg-host") return true;
    if (el.closest && (el.closest("#" + ROOT_ID) || el.closest("#lg-modal-bg") || el.closest("#lg-host"))) return true;
    return false;
  }

  function containsOurs(el) {
    if (!el) return false;
    if (isOurs(el)) return true;
    if (!el.querySelector) return false;
    return !!(el.querySelector("#" + ROOT_ID) || el.querySelector("#lg-host") || el.querySelector("#lg-modal-bg"));
  }

  function normalizeLabel(el) {
    return (el && el.textContent || "").replace(/\s+/g, " ").trim();
  }

  function isEmptyLabel(t) {
    return t === "No line groups configured" || t === "No line groups configured.";
  }

  function isAddLabel(t) {
    return (
      t === "+ Add Line Group" || t === "Add Line Group" ||
      t === "+ New line group" || t === "New line group" ||
      t === "+ New Line Group" || t === "New Line Group"
    );
  }

  function hideTarget(el) {
    if (!el || isOurs(el) || containsOurs(el)) return;
    el.classList.add("lg-hide-spa");
  }

  function hideSpaEmpty() {
    var root = document.getElementById(ROOT_ID);
    if (!root) return;
    // Never hide our own Add / Edit / Delete controls
    root.querySelectorAll(".lg-hide-spa").forEach(function (n) {
      if (isOurs(n)) n.classList.remove("lg-hide-spa");
    });
    var walk = document.querySelectorAll("h1, h2, h3, p, div, span, button, section, article");
    for (var i = 0; i < walk.length; i++) {
      var el = walk[i];
      if (isOurs(el) || containsOurs(el)) continue;
      var t = normalizeLabel(el);
      var isEmpty = isEmptyLabel(t);
      var isAdd = isAddLabel(t);
      if (!isEmpty && !isAdd) continue;
      if (isAdd && el.tagName === "BUTTON") {
        hideTarget(el);
        // Only hide a tight button-only wrap — never the header row that also
        // holds #lg-host (that was blanking the overlay after first paint).
        var wrap = el.parentElement;
        if (wrap && isAddLabel(normalizeLabel(wrap))) hideTarget(wrap);
        continue;
      }
      var box = el;
      for (var up = 0; up < 6 && box && box.parentElement; up++) {
        var parent = box.parentElement;
        if (isOurs(parent) || containsOurs(parent)) break;
        if (parent.tagName === "MAIN" || parent.id === "root" || parent === document.body) break;
        var pt = normalizeLabel(parent);
        if (pt.indexOf("No line groups configured") >= 0 && pt.length < 160) {
          box = parent;
          continue;
        }
        break;
      }
      hideTarget(box);
    }
    hideSpaForeignDialogs();
  }

  function hideSpaForeignDialogs() {
    var nodes = document.querySelectorAll("[role='dialog'], dialog");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (isOurs(el) || containsOurs(el)) continue;
      var t = normalizeLabel(el);
      if (/line group/i.test(t) || /main line/i.test(t)) hideTarget(el);
    }
  }

  function suggestMain() {
    var q = canonicalLineName(state.qMain);
    var banned = existingSubs();
    var out = [];
    state.lines.forEach(function (n) {
      n = canonicalLineName(n);
      if (!n || banned[n]) return;
      if (q && !substringMatch(n, q)) return;
      out.push(n);
    });
    return out.slice(0, 12);
  }

  function suggestSub() {
    var q = canonicalLineName(state.qSub);
    var banned = otherMains(state.main);
    var taken = existingSubs(state.mode === "edit" ? state.main : "");
    Object.keys(taken).forEach(function (s) { banned[s] = true; });
    banned[state.main] = true;
    state.subs.forEach(function (s) { banned[s] = true; });
    var out = [];
    state.lines.forEach(function (n) {
      n = canonicalLineName(n);
      if (!n || banned[n]) return;
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
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        openModal("edit", btn.closest("tr").dataset.main);
      });
    });
    tbody.querySelectorAll(".lg-del").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
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
    if (state.mode === "add" && !state.main) {
      var typedExact = canonicalLineName(state.qMain);
      if (typedExact && state.lines.indexOf(typedExact) >= 0) {
        state.main = typedExact;
        state.qMain = typedExact;
      }
    }
    var mainCommitted = !!(state.main && state.main === canonicalLineName(state.qMain));
    if (state.mode === "add" && !mainCommitted && (state.qMain || document.activeElement === mainInp)) {
      var mains = suggestMain();
      sm.innerHTML = mains.map(function (n) {
        return '<button type="button" data-val="' + escapeHtml(n) + '">' + escapeHtml(n) + "</button>";
      }).join("");
      sm.classList.toggle("hidden", !mains.length);
      sm.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("mousedown", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          var picked = canonicalLineName(b.dataset.val);
          state.main = picked;
          state.qMain = picked;
          state.subs = state.subs.filter(function (s) { return s !== state.main; });
          state.error = "";
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
        b.addEventListener("mousedown", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          var picked = canonicalLineName(b.dataset.val);
          if (existingSubs(state.mode === "edit" ? state.main : "")[picked]) {
            state.error = "Sub line " + picked + " already belongs to another group.";
            state.qSub = "";
            renderModal();
            return;
          }
          if (picked && state.subs.indexOf(picked) < 0) state.subs.push(picked);
          state.qSub = "";
          state.error = "";
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
    hideSpaForeignDialogs();
    renderModal();
  }

  function closeModal() {
    state.mode = "";
    state.error = "";
    renderModal();
  }

  function uniqueSubError(subs, exceptMain) {
    var taken = existingSubs(exceptMain);
    var clash = [];
    (subs || []).forEach(function (s) {
      s = canonicalLineName(s);
      if (s && taken[s] && clash.indexOf(s) < 0) clash.push(s);
    });
    if (!clash.length) return "";
    return "Sub line " + clash[0] + " already belongs to another group.";
  }

  async function saveGroup() {
    state.main = canonicalLineName(state.main || state.qMain);
    state.subs = state.subs.map(canonicalLineName).filter(Boolean);
    if (state.mode === "add") {
      if (!state.main) {
        state.error = "Main line is required.";
        renderModal();
        return;
      }
    }
    var clash = uniqueSubError(state.subs, state.mode === "edit" ? state.main : "");
    if (clash) {
      state.error = clash;
      renderModal();
      return;
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
    if (root) {
      renderTable(root);
      root.classList.remove("lg-hide-spa");
      var addBtn = root.querySelector("#lg-add");
      if (addBtn) {
        addBtn.classList.remove("lg-hide-spa");
        addBtn.style.display = "";
      }
    }
    hideSpaEmpty();
  }

  async function deleteGroup(main) {
    main = canonicalLineName(main);
    if (!main) return;
    if (!window.confirm("Delete line group " + main + "?")) return;
    var opts = {
      method: "POST",
      credentials: "include",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ main_line: main })
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
    if (root) {
      renderTable(root);
      root.classList.remove("lg-hide-spa");
      var addBtn = root.querySelector("#lg-add");
      if (addBtn) {
        addBtn.classList.remove("lg-hide-spa");
        addBtn.style.display = "";
      }
    }
    hideSpaEmpty();
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
      '<div class="lg-empty" id="lg-empty">' +
        "<p>No line groups configured.</p>" +
        '<button type="button" class="lg-btn primary" id="lg-add-empty">+ Add Line Group</button></div>';
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
      state.qMain = canonicalLineName(ev.target.value) || ev.target.value;
      state.main = "";
      state.error = "";
      renderModal();
      bg.querySelector("#lg-main").focus();
    });
    bg.querySelector("#lg-main").addEventListener("keydown", function (ev) {
      if (state.mode === "edit") return;
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      var mains = suggestMain();
      var typed = canonicalLineName(ev.target.value);
      var pick = null;
      for (var i = 0; i < mains.length; i++) {
        if (mains[i] === typed) { pick = mains[i]; break; }
      }
      if (!pick && mains.length === 1) pick = mains[0];
      if (!pick && typed && state.lines.indexOf(typed) >= 0) pick = typed;
      if (!pick) return;
      state.main = pick;
      state.qMain = pick;
      state.error = "";
      renderModal();
    });
    bg.querySelector("#lg-sub").addEventListener("input", function (ev) {
      state.qSub = canonicalLineName(ev.target.value) || ev.target.value;
      state.error = "";
      renderModal();
      bg.querySelector("#lg-sub").focus();
    });
  }

  function isChromeHeader(el) {
    var p = el;
    while (p && p !== document.body) {
      if (p.tagName === "HEADER") return true;
      p = p.parentElement;
    }
    return false;
  }

  function findHeading() {
    var prefer = null;
    var nodes = document.querySelectorAll("h1, h2, h3");
    for (var i = 0; i < nodes.length; i++) {
      if (isOurs(nodes[i])) continue;
      var t = normalizeLabel(nodes[i]);
      if (t === "Line Groups") return nodes[i];
      if (t === "Lines" && !prefer && !isChromeHeader(nodes[i])) prefer = nodes[i];
    }
    return prefer;
  }

  function isToolbarRow(row, heading) {
    if (!row || !heading || isOurs(row) || containsOurs(row)) return false;
    if (row.tagName === "MAIN" || row.id === "root" || row === document.body) return false;
    if (normalizeLabel(row).length > 80) return false;
    var kids = row.children || [];
    for (var i = 0; i < kids.length; i++) {
      var kid = kids[i];
      if (kid === heading) continue;
      if (kid.tagName === "BUTTON" || (kid.querySelector && kid.querySelector("button"))) return true;
    }
    return false;
  }

  function placeHost(host) {
    var heading = findHeading();
    var after = heading;
    if (heading && isToolbarRow(heading.parentElement, heading)) {
      after = heading.parentElement;
    }
    if (after && after.parentNode) {
      if (host.previousElementSibling !== after || host.parentNode !== after.parentNode) {
        after.parentNode.insertBefore(host, after.nextSibling);
      }
      return host;
    }
    var main = document.querySelector("main") || document.getElementById("root") || document.body;
    if (host.parentNode !== main) {
      main.insertBefore(host, main.firstChild);
    }
    return host;
  }

  function ensureHost() {
    var host = document.getElementById("lg-host");
    if (host) {
      placeHost(host);
      return host;
    }
    host = document.createElement("div");
    host.id = "lg-host";
    host.className = "lg-host";
    placeHost(host);
    return host;
  }

  function wireRoot(root) {
    if (root.dataset.lgWired === "1") return;
    root.dataset.lgWired = "1";
    var add = root.querySelector("#lg-add");
    if (add) add.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openModal("add");
    });
    var addEmpty = root.querySelector("#lg-add-empty");
    if (addEmpty) addEmpty.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openModal("add");
    });
  }

  async function mount() {
    if (!isPage()) {
      teardown();
      return;
    }
    document.body.classList.add("lg-overlay-on");
    ensureModal();
    var host = ensureHost();
    var existing = document.getElementById(ROOT_ID);
    if (existing) {
      if (existing.parentNode !== host) host.appendChild(existing);
      existing.classList.remove("lg-hide-spa");
      existing.style.display = "";
      existing.style.pointerEvents = "auto";
      wireRoot(existing);
      if (!state.loaded) {
        await Promise.all([loadGroups(), loadLines()]);
        state.loaded = true;
        renderTable(existing);
      }
      hideSpaEmpty();
      return;
    }
    var root = buildRoot();
    host.appendChild(root);
    wireRoot(root);
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
    var host = document.getElementById("lg-host");
    if (host) host.remove();
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
    }
    // Always teardown off Line Groups so body.lg-overlay-on cannot hide other pages' tables.
    if (!isPage()) {
      teardown();
      return;
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

  if (typeof window !== "undefined" && window.__LG_TEST__) {
    window.__lgTest = {
      hideSpaEmpty: hideSpaEmpty,
      mount: mount,
      teardown: teardown,
      isPage: isPage,
      ensureHost: ensureHost,
      containsOurs: containsOurs,
      state: state,
      canonicalLineName: canonicalLineName,
      collectLineNames: collectLineNames,
      suggestMain: suggestMain,
      suggestSub: suggestSub,
      existingSubs: existingSubs,
      uniqueSubError: uniqueSubError,
      deleteGroup: deleteGroup,
      saveGroup: saveGroup,
      openModal: openModal,
      renderModal: renderModal
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
