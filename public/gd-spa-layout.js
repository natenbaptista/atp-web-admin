(function () {
  "use strict";

  var EXTRA_FIELDS = [
    { key: "phone1", label: "Phone" },
    { key: "phone2", label: "Phone 2" },
    { key: "phone3", label: "Phone 3" },
    { key: "email", label: "Email" },
    { key: "profile_pic", label: "Profile" },
    { key: "company_logo", label: "Logo" },
    { key: "designation", label: "Designation" },
    { key: "company_name", label: "Company" },
    { key: "company_type", label: "Company Type" },
    { key: "group", label: "Group" },
    { key: "company_address", label: "Address" },
    { key: "cn", label: "Type (cn)" }
  ];
  var SORT_FIELDS = [{ key: "name", label: "Name" }].concat(EXTRA_FIELDS);
  var DEFAULT_EXTRAS = ["phone1", "profile_pic", "company_name", "email"];
  var EXTRA_KEYS = EXTRA_FIELDS.map(function (f) { return f.key; });
  var ROOT_ID = "gd-spa-layout";

  var state = {
    rows: [],
    page: 1,
    q: "",
    loaded: false,
    selected: {}
  };

  function isList() {
    var p = (location.pathname || "").replace(/\/+$/, "") || "/";
    return p === "/global-directory";
  }

  function isForm() {
    var p = location.pathname || "";
    return p.indexOf("/global-directory/") === 0 &&
      (/\/edit\/?$/.test(p) || /\/new\/?$/.test(p) || /\/add\/?$/.test(p));
  }

  function loadCols() {
    try {
      var raw = JSON.parse(localStorage.getItem("gd_columns") || "null");
      if (Array.isArray(raw)) {
        var keys = raw.filter(function (k) { return EXTRA_KEYS.indexOf(k) >= 0; }).slice(0, 4);
        if (keys.length) return keys;
      }
    } catch (e) {}
    return DEFAULT_EXTRAS.slice();
  }
  function saveCols(cols) { localStorage.setItem("gd_columns", JSON.stringify(cols)); }
  function loadSort() {
    var raw = localStorage.getItem("gd_sort") || "name:asc";
    var parts = raw.split(":");
    var field = SORT_FIELDS.some(function (f) { return f.key === parts[0]; }) ? parts[0] : "name";
    var dir = parts[1] === "desc" ? "desc" : "asc";
    return { field: field, dir: dir };
  }
  function saveSort(field, dir) { localStorage.setItem("gd_sort", field + ":" + dir); }
  function loadPageSize() {
    var n = parseInt(localStorage.getItem("gd_page_size") || "10", 10);
    return [10, 25, 50, 100].indexOf(n) >= 0 ? n : 10;
  }
  function savePageSize(n) { localStorage.setItem("gd_page_size", String(n)); }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function imgUrl(kind, filename) {
    return "/directory/global/image/" + kind + "/" + encodeURIComponent(filename);
  }
  function initials(name) {
    var parts = String(name || "").trim().split(/\s+/).slice(0, 2);
    return parts.map(function (p) { return p.charAt(0).toUpperCase(); }).join("") || "?";
  }

  function filteredRows() {
    var q = (state.q || "").trim();
    if (q.length < 3) return state.rows.slice();
    var t = q.toLowerCase();
    return state.rows.filter(function (r) {
      return [
        r.name, r.phone1, r.phone2, r.phone3, r.email, r.company_name,
        r.cn, r.group, r.designation, r.company_type, r.company_address
      ].some(function (v) { return String(v || "").toLowerCase().indexOf(t) >= 0; });
    });
  }
  function sortedRows() {
    var s = loadSort();
    var rows = filteredRows();
    rows.sort(function (a, b) {
      var av = String(a[s.field] == null ? "" : a[s.field]).toLowerCase();
      var bv = String(b[s.field] == null ? "" : b[s.field]).toLowerCase();
      if (av < bv) return s.dir === "asc" ? -1 : 1;
      if (av > bv) return s.dir === "asc" ? 1 : -1;
      return (a.id || 0) - (b.id || 0);
    });
    return rows;
  }

  function cellHtml(row, key) {
    if (key === "profile_pic") {
      if (!row.profile_pic) return '<span class="gd-noimg">No image</span>';
      return '<img class="gd-thumb" alt="" src="' + imgUrl("profile", row.profile_pic) + '" />';
    }
    if (key === "company_logo") {
      if (!row.company_logo) return '<span class="gd-noimg">No logo</span>';
      return '<img class="gd-thumb" alt="" src="' + imgUrl("logo", row.company_logo) + '" />';
    }
    var v = row[key];
    return escapeHtml(v == null || v === "" ? "—" : String(v));
  }

  function nameCell(row) {
    var av;
    if (row.profile_pic) {
      av = '<img class="gd-av" alt="" src="' + imgUrl("profile", row.profile_pic) + '" />';
    } else {
      av = '<div class="gd-av initials">' + escapeHtml(initials(row.name)) + "</div>";
    }
    return '<div class="gd-name">' + av + "<div><div class=\"gd-nm\">" +
      escapeHtml(row.name || "") + '</div><div class="gd-sub">' +
      escapeHtml(row.designation || "") + "</div></div></div>";
  }

  function renderColGrid(root) {
    var grid = root.querySelector("#gd-col-grid");
    var cols = loadCols();
    grid.innerHTML = EXTRA_FIELDS.map(function (f) {
      var checked = cols.indexOf(f.key) >= 0;
      var disable = !checked && cols.length >= 4;
      return '<label><input type="checkbox" data-col="' + f.key + '"' +
        (checked ? " checked" : "") + (disable ? " disabled" : "") + "> " +
        escapeHtml(f.label) + "</label>";
    }).join("");
    grid.querySelectorAll("input").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var next = loadCols().slice();
        if (inp.checked) {
          if (next.length >= 4) { inp.checked = false; return; }
          next.push(inp.dataset.col);
        } else {
          next = next.filter(function (k) { return k !== inp.dataset.col; });
        }
        saveCols(next);
        renderColGrid(root);
        renderTable(root);
      });
    });
  }

  function renderSortGrid(root) {
    var grid = root.querySelector("#gd-sort-grid");
    var s = loadSort();
    grid.innerHTML = SORT_FIELDS.map(function (f) {
      return '<label><input type="radio" name="gdSortField" value="' + f.key + '"' +
        (s.field === f.key ? " checked" : "") + "> " + escapeHtml(f.label) + "</label>";
    }).join("");
    grid.querySelectorAll("input").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var dir = root.querySelector("#gd-sort-dir").value;
        saveSort(inp.value, dir);
        renderTable(root);
      });
    });
    root.querySelector("#gd-sort-dir").value = s.dir;
    root.querySelector("#gd-page-size").value = String(loadPageSize());
  }

  function renderTable(root) {
    var extras = loadCols();
    var thead = root.querySelector("#gd-thead");
    thead.innerHTML = '<th style="width:36px"><input type="checkbox" id="gd-chk-all"></th><th>Name</th>' +
      extras.map(function (k) {
        var f = EXTRA_FIELDS.filter(function (x) { return x.key === k; })[0];
        return "<th>" + escapeHtml(f ? f.label : k) + "</th>";
      }).join("") + "<th>Actions</th>";

    var size = loadPageSize();
    var rows = sortedRows();
    var pages = Math.max(1, Math.ceil(rows.length / size) || 1);
    if (state.page > pages) state.page = pages;
    if (state.page < 1) state.page = 1;
    var start = (state.page - 1) * size;
    var pageRows = rows.slice(start, start + size);

    var tbody = root.querySelector("#gd-tbody");
    tbody.innerHTML = pageRows.map(function (r) {
      var checked = state.selected[r.id] ? " checked" : "";
      return '<tr data-id="' + r.id + '"><td><input type="checkbox" class="gd-rowchk" data-id="' +
        r.id + '"' + checked + "></td><td>" + nameCell(r) + "</td>" +
        extras.map(function (k) { return "<td>" + cellHtml(r, k) + "</td>"; }).join("") +
        '<td class="gd-actions">' +
          '<button type="button" class="gd-btn gd-edit" data-id="' + r.id + '">Edit</button>' +
          '<button type="button" class="gd-btn danger gd-del" data-id="' + r.id + '">Delete</button>' +
        "</td></tr>";
    }).join("");

    root.querySelector("#gd-empty").classList.toggle("hidden", pageRows.length > 0);
    root.querySelector("#gd-count").textContent =
      rows.length + (rows.length === 1 ? " contact" : " contacts");
    root.querySelector("#gd-page-info").textContent =
      "Showing " + pageRows.length + " of " + rows.length + " contacts";
    root.querySelector("#gd-prev").disabled = state.page <= 1;
    root.querySelector("#gd-next").disabled = state.page >= pages;

    var chkAll = root.querySelector("#gd-chk-all");
    chkAll.checked = pageRows.length > 0 && pageRows.every(function (r) { return state.selected[r.id]; });
    chkAll.addEventListener("change", function () {
      pageRows.forEach(function (r) {
        if (chkAll.checked) state.selected[r.id] = true;
        else delete state.selected[r.id];
      });
      renderTable(root);
    });
    tbody.querySelectorAll(".gd-rowchk").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var id = Number(inp.dataset.id);
        if (inp.checked) state.selected[id] = true;
        else delete state.selected[id];
      });
    });
    tbody.querySelectorAll(".gd-edit").forEach(function (btn) {
      btn.addEventListener("click", function () {
        window.location.href = "/global-directory/" + btn.dataset.id + "/edit";
      });
    });
    tbody.querySelectorAll(".gd-del").forEach(function (btn) {
      btn.addEventListener("click", function () { deleteOne(Number(btn.dataset.id), root); });
    });
  }

  async function deleteOne(id, root) {
    if (!window.confirm("Delete this contact?")) return;
    var res = await fetch("/directory/global/" + id + "/delete", {
      method: "POST",
      credentials: "include"
    });
    if (res.status === 401) { window.location.href = "/login"; return; }
    if (!res.ok) { window.alert("Failed to delete entry"); return; }
    delete state.selected[id];
    await loadRows(root);
  }

  async function loadRows(root) {
    var res = await fetch("/directory/global", { credentials: "include" });
    if (res.status === 401) { window.location.href = "/login"; return; }
    var data = await res.json();
    state.rows = Array.isArray(data) ? data : [];
    state.loaded = true;
    renderTable(root);
  }

  function hookSearch(root) {
    var input = document.querySelector('input[placeholder*="Search" i], input[type="search"]');
    if (!input || input.dataset.gdHooked) return;
    input.dataset.gdHooked = "1";
    var t = null;
    input.addEventListener("input", function () {
      clearTimeout(t);
      var val = input.value || "";
      t = setTimeout(function () {
        state.q = val;
        state.page = 1;
        renderTable(root);
      }, 200);
    });
    state.q = input.value || "";
  }

  function buildRoot() {
    var el = document.createElement("div");
    el.id = ROOT_ID;
    el.innerHTML =
      '<div class="gd-count" id="gd-count">0 contacts</div>' +
      '<div class="gd-filters">' +
        '<div class="gd-field">' +
          '<label for="gd-page-size">Contact view per page</label>' +
          '<div class="gd-pagesize"><span>Show contacts</span>' +
            '<select id="gd-page-size"><option>10</option><option>25</option><option>50</option><option>100</option></select>' +
            '<span>per page</span></div>' +
        "</div>" +
      "</div>" +
      '<div class="gd-section"><div class="gd-section-title">Show columns</div>' +
        '<div class="gd-hint">Max 4 extra — Name and Actions always shown</div>' +
        '<div class="gd-grid" id="gd-col-grid"></div></div>' +
      '<div class="gd-section"><div class="gd-section-title">Sort columns</div>' +
        '<div class="gd-grid" id="gd-sort-grid"></div>' +
        '<div class="gd-sort-actions">' +
          '<span class="gd-lbl">Direction</span>' +
          '<select id="gd-sort-dir"><option value="asc">asc</option><option value="desc">desc</option></select>' +
          '<button type="button" class="gd-btn" id="gd-reload">Reload</button>' +
        "</div></div>" +
      '<div class="gd-table-wrap"><table id="gd-spa-table"><thead><tr id="gd-thead"></tr></thead><tbody id="gd-tbody"></tbody></table></div>' +
      '<div class="gd-empty hidden" id="gd-empty">No contacts to display.</div>' +
      '<div class="gd-pager"><div id="gd-page-info">Showing 0 of 0 contacts</div>' +
        '<div><button type="button" class="gd-btn" id="gd-prev">Prev</button> ' +
        '<button type="button" class="gd-btn" id="gd-next">Next</button></div></div>';
    return el;
  }

  function bindControls(root) {
    root.querySelector("#gd-page-size").addEventListener("change", function () {
      savePageSize(parseInt(root.querySelector("#gd-page-size").value, 10));
      state.page = 1;
      renderTable(root);
    });
    root.querySelector("#gd-sort-dir").addEventListener("change", function () {
      var radio = root.querySelector('input[name="gdSortField"]:checked');
      saveSort(radio ? radio.value : "name", root.querySelector("#gd-sort-dir").value);
      renderTable(root);
    });
    root.querySelector("#gd-reload").addEventListener("click", function () { loadRows(root); });
    root.querySelector("#gd-prev").addEventListener("click", function () {
      state.page -= 1;
      renderTable(root);
    });
    root.querySelector("#gd-next").addEventListener("click", function () {
      state.page += 1;
      renderTable(root);
    });
  }

  function findAnchor() {
    var search = document.querySelector('input[placeholder*="Search" i], input[type="search"]');
    if (search) {
      return search.closest("div") || search.parentElement;
    }
    var table = document.querySelector("table");
    return table ? table.parentElement : null;
  }

  function mount() {
    if (!isList()) {
      teardown();
      return;
    }
    if (document.getElementById(ROOT_ID)) {
      document.body.classList.add("gd-layout-on");
      hookSearch(document.getElementById(ROOT_ID));
      return;
    }
    var anchor = findAnchor();
    if (!anchor) return;
    var root = buildRoot();
    if (anchor.parentElement) {
      anchor.parentElement.insertBefore(root, anchor.nextSibling);
    } else {
      document.body.appendChild(root);
    }
    document.body.classList.add("gd-layout-on");
    renderColGrid(root);
    renderSortGrid(root);
    bindControls(root);
    hookSearch(root);
    loadRows(root);
  }

  function teardown() {
    document.body.classList.remove("gd-layout-on");
    var el = document.getElementById(ROOT_ID);
    if (el) el.remove();
  }

  function hookFilePreview(input) {
    if (input.dataset.gdPreview) return;
    input.dataset.gdPreview = "1";
    var img = document.createElement("img");
    img.className = "gd-file-preview hidden";
    img.alt = "Preview";
    input.parentElement.appendChild(img);
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) { img.removeAttribute("src"); img.classList.add("hidden"); return; }
      var reader = new FileReader();
      reader.onload = function () {
        img.src = reader.result;
        img.classList.remove("hidden");
      };
      reader.readAsDataURL(file);
    });
  }

  function enhanceForms() {
    if (!isForm() && !isList()) return;
    document.querySelectorAll('input[type="file"]').forEach(hookFilePreview);
  }

  var lastPath = location.pathname;
  function tick() {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      state.loaded = false;
      if (!isList()) teardown();
    }
    mount();
    enhanceForms();
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
    var obs = new MutationObserver(function () { tick(); });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
