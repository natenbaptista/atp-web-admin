(function () {
  "use strict";

  var cached = "";
  var lastPoll = 0;
  var POLL_MS = 30000;
  var obs = null;

  function fromLoginPayload() {
    var stores = [];
    try { stores.push(window.localStorage); } catch (e) {}
    try { stores.push(window.sessionStorage); } catch (e) {}
    for (var s = 0; s < stores.length; s++) {
      var store = stores[s];
      for (var i = 0; i < store.length; i++) {
        var key = store.key(i);
        var raw;
        try { raw = store.getItem(key); } catch (e) { continue; }
        if (!raw || raw.indexOf("web_version") < 0) continue;
        try {
          var obj = JSON.parse(raw);
          var v = pickWeb(obj);
          if (v) return v;
        } catch (e) {}
      }
    }
    return "";
  }

  function pickWeb(obj) {
    if (!obj || typeof obj !== "object") return "";
    if (obj.web_version != null && String(obj.web_version).trim()) {
      return String(obj.web_version).trim();
    }
    var nested = [obj.user, obj.data, obj.session, obj.auth, obj.profile];
    for (var i = 0; i < nested.length; i++) {
      if (nested[i] && nested[i].web_version != null && String(nested[i].web_version).trim()) {
        return String(nested[i].web_version).trim();
      }
    }
    return "";
  }

  async function fromHealth() {
    try {
      var res = await fetch("/health", { credentials: "include" });
      if (!res.ok) return "";
      var data = await res.json();
      if (data && data.web_version != null && String(data.web_version).trim()) {
        return String(data.web_version).trim();
      }
    } catch (e) {}
    return "";
  }

  function sidebarLeaves() {
    var nodes = document.querySelectorAll("aside *, nav *, [class*='sidebar'] *, [class*='Sidebar'] *");
    return nodes.length ? nodes : document.querySelectorAll("body *");
  }

  function findAmpVersionEl() {
    var list = sidebarLeaves();
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (el.id === "amp-web-ver" || (el.classList && el.classList.contains("amp-web-ver"))) continue;
      if (el.children && el.children.length) continue;
      var t = (el.textContent || "").trim();
      if (/^v\d+\.\d+(\.\d+)*$/.test(t)) return el;
    }
    return null;
  }

  function findAdminEl() {
    var list = sidebarLeaves();
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (el.children && el.children.length) continue;
      if ((el.textContent || "").trim().toLowerCase() === "admin") return el;
    }
    return null;
  }

  function fromOverlayNode(node) {
    if (!node) return false;
    if (node.id === "amp-web-ver") return true;
    if (node.nodeType === 3 && node.parentNode && node.parentNode.id === "amp-web-ver") return true;
    if (node.closest && node.closest("#amp-web-ver")) return true;
    return false;
  }

  function footerColor() {
    var admin = findAdminEl();
    if (admin && window.getComputedStyle) {
      return window.getComputedStyle(admin).color;
    }
    var amp = findAmpVersionEl();
    if (amp && window.getComputedStyle) {
      return window.getComputedStyle(amp).color;
    }
    return "";
  }

  function applyFooterColor(el, color) {
    if (!el || !color) return;
    el.style.setProperty("color", color, "important");
    el.style.opacity = "1";
  }

  function syncFooterShades() {
    var color = footerColor();
    if (!color) return;
    var amp = findAmpVersionEl();
    if (amp) applyFooterColor(amp, color);
    var web = document.getElementById("amp-web-ver");
    if (web) applyFooterColor(web, color);
  }

  function paint(ver) {
    if (!ver) return;
    var label = "Web v " + ver;
    var existing = document.getElementById("amp-web-ver");
    if (existing) {
      if (existing.textContent !== label) {
        if (obs) obs.disconnect();
        existing.textContent = label;
        if (obs) obs.observe(document.documentElement, { childList: true, subtree: true });
      }
      syncFooterShades();
      return;
    }
    var amp = findAmpVersionEl();
    if (!amp || !amp.parentNode) return;
    var line = document.createElement("div");
    line.id = "amp-web-ver";
    line.className = "amp-web-ver";
    line.textContent = label;
    if (obs) obs.disconnect();
    amp.parentNode.insertBefore(line, amp.nextSibling);
    if (obs) obs.observe(document.documentElement, { childList: true, subtree: true });
    syncFooterShades();
  }

  async function refresh(force) {
    var ver = fromLoginPayload() || cached;
    if (!ver || force || (Date.now() - lastPoll > POLL_MS)) {
      lastPoll = Date.now();
      var hv = await fromHealth();
      if (hv) ver = hv;
    }
    if (ver) cached = ver;
    paint(cached);
  }

  var lastPath = location.pathname;
  function tick() {
    if (location.pathname !== lastPath) lastPath = location.pathname;
    refresh(false);
  }

  function start() {
    refresh(true);
    setInterval(tick, 400);
    window.addEventListener("popstate", function () { refresh(true); });
    var wrapPush = history.pushState;
    var wrapReplace = history.replaceState;
    history.pushState = function () {
      wrapPush.apply(this, arguments);
      setTimeout(function () { refresh(true); }, 0);
    };
    history.replaceState = function () {
      wrapReplace.apply(this, arguments);
      setTimeout(function () { refresh(true); }, 0);
    };
    obs = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        if (fromOverlayNode(m.target)) continue;
        paint(cached);
        return;
      }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
