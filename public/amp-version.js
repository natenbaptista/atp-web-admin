(function () {
  "use strict";

  var cached = "";
  var cachedAmp = "";
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
        if (!raw || (raw.indexOf("web_version") < 0 && raw.indexOf("app_version") < 0)) continue;
        try {
          var obj = JSON.parse(raw);
          var v = pickWeb(obj);
          var a = pickAmp(obj);
          if (a && !cachedAmp) cachedAmp = a;
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

  function pickAmp(obj) {
    if (!obj || typeof obj !== "object") return "";
    var raw = obj.app_version != null ? obj.app_version : obj.version;
    if (raw != null && String(raw).trim()) return String(raw).trim().replace(/^v/i, "");
    var nested = [obj.user, obj.data, obj.session, obj.auth, obj.profile];
    for (var i = 0; i < nested.length; i++) {
      var n = nested[i];
      if (!n) continue;
      raw = n.app_version != null ? n.app_version : n.version;
      if (raw != null && String(raw).trim()) return String(raw).trim().replace(/^v/i, "");
    }
    return "";
  }

  async function fromHealth() {
    try {
      var res = await fetch("/health", { credentials: "include" });
      if (!res.ok) return { web: "", amp: "" };
      var data = await res.json();
      var web = "";
      var amp = "";
      if (data && data.web_version != null && String(data.web_version).trim()) {
        web = String(data.web_version).trim();
      }
      if (data && data.version != null && String(data.version).trim()) {
        amp = String(data.version).trim().replace(/^v/i, "");
      }
      return { web: web, amp: amp };
    } catch (e) {}
    return { web: "", amp: "" };
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

  function footerStyle() {
    var src = findAdminEl() || findAmpVersionEl();
    if (!src || !window.getComputedStyle) return null;
    var cs = window.getComputedStyle(src);
    return {
      color: cs.color,
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
      letterSpacing: cs.letterSpacing,
      lineHeight: cs.lineHeight,
      fontFamily: cs.fontFamily
    };
  }

  function applyFooterStyle(el, style) {
    if (!el || !style) return;
    el.style.setProperty("color", style.color, "important");
    el.style.setProperty("font-size", style.fontSize, "important");
    el.style.setProperty("font-weight", style.fontWeight, "important");
    el.style.setProperty("letter-spacing", style.letterSpacing, "important");
    el.style.setProperty("line-height", style.lineHeight, "important");
    el.style.setProperty("font-family", style.fontFamily, "important");
    el.style.opacity = "1";
  }

  function syncFooterShades() {
    var style = footerStyle();
    if (!style) return;
    var amp = findAmpVersionEl();
    if (amp) applyFooterStyle(amp, style);
    var web = document.getElementById("amp-web-ver");
    if (web) applyFooterStyle(web, style);
  }

  function paintAmp(ver) {
    if (!ver) return;
    var label = "v" + String(ver).replace(/^v/i, "");
    var amp = findAmpVersionEl();
    if (!amp) return;
    if (amp.textContent === label) return;
    if (obs) obs.disconnect();
    amp.textContent = label;
    if (obs) obs.observe(document.documentElement, { childList: true, subtree: true });
  }

  function paint(ver) {
    if (cachedAmp) paintAmp(cachedAmp);
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
    if (!ver || !cachedAmp || force || (Date.now() - lastPoll > POLL_MS)) {
      lastPoll = Date.now();
      var hv = await fromHealth();
      if (hv.web) ver = hv.web;
      if (hv.amp) cachedAmp = hv.amp;
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
