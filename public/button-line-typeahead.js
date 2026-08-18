(function () {
  "use strict";

  function isButtons() {
    return /^\/users\/[^/]+\/buttons\/?$/.test(location.pathname || "");
  }

  function isNone(text) {
    return /^-?\s*none\s*-?$/i.test(String(text || "").trim());
  }

  function prefixMatch(text, q) {
    return String(text || "").toLowerCase().indexOf(String(q || "").toLowerCase()) === 0;
  }

  function looksLikeLineSelect(sel) {
    if (!sel || !sel.options || sel.dataset.bltHooked) return false;
    var none = false;
    var lined = 0;
    for (var i = 0; i < sel.options.length; i++) {
      var t = (sel.options[i].text || sel.options[i].value || "").trim();
      if (isNone(t)) none = true;
      if (/\d/.test(t)) lined += 1;
    }
    if (sel.options.length < 3) return false;
    return none || lined >= 5 || nearLineLabel(sel);
  }

  function nearLineLabel(el) {
    var node = el;
    for (var up = 0; up < 5 && node; up++) {
      var lab = node.querySelector && node.querySelector("label, [class*='label']");
      var txt = ((lab && lab.textContent) || (node.previousElementSibling && node.previousElementSibling.textContent) || "").trim();
      if (/^line\b/i.test(txt) || txt === "Line") return true;
      node = node.parentElement;
    }
    return false;
  }

  function enhanceSelect(sel) {
    if (!looksLikeLineSelect(sel)) return;
    sel.dataset.bltHooked = "1";
    var originals = [];
    var selected = sel.value;
    for (var i = 0; i < sel.options.length; i++) {
      var o = sel.options[i];
      originals.push({ value: o.value, text: o.text, disabled: o.disabled });
    }
    var wrap = document.createElement("div");
    wrap.className = "blt-wrap";
    var input = document.createElement("input");
    input.type = "search";
    input.className = "blt-search";
    input.placeholder = "Search lines...";
    input.setAttribute("autocomplete", "off");
    if (sel.parentNode) {
      sel.parentNode.insertBefore(wrap, sel);
      wrap.appendChild(input);
      wrap.appendChild(sel);
    }
    function apply() {
      var q = (input.value || "").trim();
      var keepVal = sel.value;
      sel.innerHTML = "";
      originals.forEach(function (o) {
        var keep = isNone(o.text) || !q || prefixMatch(o.text, q) || prefixMatch(o.value, q);
        if (!keep) return;
        var opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = o.text;
        if (o.disabled) opt.disabled = true;
        sel.appendChild(opt);
      });
      if (keepVal) {
        for (var j = 0; j < sel.options.length; j++) {
          if (sel.options[j].value === keepVal) { sel.value = keepVal; break; }
        }
      } else if (selected) {
        sel.value = selected;
      }
    }
    input.addEventListener("input", apply);
  }

  function looksLikeLineListbox(box) {
    if (!box || box.dataset.bltHooked) return false;
    var opts = box.querySelectorAll('[role="option"]');
    if (opts.length < 3) return false;
    var none = false;
    var lined = 0;
    for (var i = 0; i < opts.length; i++) {
      var t = (opts[i].textContent || "").replace(/\s+/g, " ").trim();
      if (isNone(t)) none = true;
      if (/\d/.test(t)) lined += 1;
    }
    return none || lined >= 5 || nearLineLabel(box);
  }

  function enhanceListbox(box) {
    if (!looksLikeLineListbox(box)) return;
    box.dataset.bltHooked = "1";
    var opts = Array.prototype.slice.call(box.querySelectorAll('[role="option"]'));
    var input = document.createElement("input");
    input.type = "search";
    input.className = "blt-search";
    input.placeholder = "Search lines...";
    input.setAttribute("autocomplete", "off");
    box.insertBefore(input, box.firstChild);
    input.addEventListener("input", function () {
      var q = (input.value || "").trim();
      opts.forEach(function (o) {
        var t = (o.textContent || "").replace(/\s+/g, " ").trim();
        var keep = isNone(t) || !q || prefixMatch(t, q);
        o.style.display = keep ? "" : "none";
        if (!keep) o.setAttribute("aria-hidden", "true");
        else o.removeAttribute("aria-hidden");
      });
    });
    input.addEventListener("click", function (ev) { ev.stopPropagation(); });
    input.addEventListener("keydown", function (ev) { ev.stopPropagation(); });
    try { input.focus(); } catch (e) {}
  }

  function scan() {
    if (!isButtons()) return;
    document.querySelectorAll("select").forEach(enhanceSelect);
    document.querySelectorAll('[role="listbox"]').forEach(enhanceListbox);
  }

  var lastPath = location.pathname;
  function tick() {
    if (location.pathname !== lastPath) lastPath = location.pathname;
    if (isButtons()) scan();
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
    var obs = new MutationObserver(function () { if (isButtons()) scan(); });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
