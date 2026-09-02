(function () {
  "use strict";

  // The green SPA posts /stations/discover, then paints `output` as a red
  // banner whenever success is false. A successful empty list is also painted
  // red ("No connected stations available"). Fresh AMP + no stations must not
  // look like a broken install.

  var GENERIC_MISSING_RE = /Node install has missing software\(s\)!?/i;
  var NO_STATIONS_RE = /^no connected stations available\.?$/i;
  var fetchWrapped = false;

  function pathOf() {
    return (location.pathname || "").replace(/\/+$/, "") || "/";
  }

  function isPage() {
    return pathOf() === "/stations";
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

  function isDiscoverPath(pathname) {
    return String(pathname || "").replace(/\/+$/, "") === "/stations/discover";
  }

  function missingSoftwareDetail(text) {
    if (!text || !GENERIC_MISSING_RE.test(text)) return "";
    return String(text).replace(GENERIC_MISSING_RE, "").replace(/^[\s.:;,-]+|[\s.:;,-]+$/g, "");
  }

  function isGenericMissing(text) {
    if (!text || !GENERIC_MISSING_RE.test(text)) return false;
    return !missingSoftwareDetail(text);
  }

  function isEmptyStateBanner(text) {
    var t = String(text || "").replace(/\s+/g, " ").trim();
    if (!t) return false;
    if (isGenericMissing(t)) return true;
    return NO_STATIONS_RE.test(t);
  }

  function rewriteDiscover(data) {
    var body = data && typeof data === "object" && !Array.isArray(data) ? data : {};
    var stations = Array.isArray(body.stations) ? body.stations : [];
    if (stations.length) {
      return { success: true, stations: stations, output: body.output || "" };
    }
    var output = String(body.output || "");
    var named = missingSoftwareDetail(output);
    if (named) {
      return {
        success: false,
        stations: [],
        output: "Node install is missing: " + named
      };
    }
    if (isGenericMissing(output)) {
      return { success: true, stations: [], output: "" };
    }
    return body;
  }

  function jsonResponse(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }

  function wrapFetch() {
    if (fetchWrapped || typeof window.fetch !== "function") return;
    fetchWrapped = true;
    var orig = window.fetch;
    window.fetch = function (input, init) {
      var args = arguments;
      var self = this;
      var pathname = pathFromUrl(urlString(input));
      return orig.apply(self, args).then(function (res) {
        if (!isDiscoverPath(pathname)) return res;
        return res.json().then(function (data) {
          return jsonResponse(rewriteDiscover(data));
        }).catch(function () {
          return res;
        });
      });
    };
  }

  function isRedBannerEl(el) {
    if (!el || !el.style) return false;
    var bg = String(el.style.backgroundColor || "").replace(/\s+/g, "").toLowerCase();
    return bg === "#e53e3e" || bg === "rgb(229,62,62)";
  }

  function bannerRoot(el) {
    var n = el;
    while (n && n !== document.body && n !== document.documentElement) {
      if (isRedBannerEl(n)) return n;
      n = n.parentElement;
    }
    return el && el.parentElement ? el.parentElement : el;
  }

  function hideEmptyStateBanners() {
    if (!isPage()) return;
    var nodes = document.querySelectorAll("pre, [style]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var text = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!isEmptyStateBanner(text)) continue;
      var root = bannerRoot(el);
      if (!root || root === document.body || root === document.documentElement) continue;
      if (root.getAttribute && root.getAttribute("data-stations-keep") === "1") continue;
      root.setAttribute("data-stations-empty-banner", "1");
      root.style.display = "none";
    }
  }

  function tick() {
    hideEmptyStateBanners();
  }

  function start() {
    wrapFetch();
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
      if (isPage()) hideEmptyStateBanners();
    });
    if (document.documentElement) {
      obs.observe(document.documentElement, { childList: true, subtree: true });
    }
  }

  if (typeof window !== "undefined" && window.__STATIONS_TEST__) {
    window.__stationsTest = {
      isPage: isPage,
      isDiscoverPath: isDiscoverPath,
      missingSoftwareDetail: missingSoftwareDetail,
      isGenericMissing: isGenericMissing,
      isEmptyStateBanner: isEmptyStateBanner,
      rewriteDiscover: rewriteDiscover,
      hideEmptyStateBanners: hideEmptyStateBanners,
      wrapFetch: wrapFetch
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
