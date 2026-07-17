/*!
 * windrose-embed.js — tiny embed SDK for Windrose AI surfaces.
 *
 * Usage (in the tenant's page):
 *   <div id="wr"></div>
 *   <script src="https://<windrose-host>/windrose-embed.js"></script>
 *   <script>
 *     const frame = Windrose.embed(document.getElementById('wr'), {
 *       // Either a full embedUrl from POST /api/embed/token (recommended),
 *       // or {baseUrl, token, surface, resourceId} to build one.
 *       embedUrl: 'https://<windrose-host>/embed/dashboard/<id>?t=<token>',
 *       theme: 'light',
 *       onReady() {},
 *     });
 *     // later: frame.setTheme('dark'); frame.destroy();
 *   </script>
 *
 * The embed token is minted server-side by the tenant's backend (POST
 * /api/embed/token with the embed secret) — never in the browser. This SDK only
 * injects the iframe and wires resize/ready messaging, validating that inbound
 * messages come from the Windrose iframe origin.
 */
(function (global) {
  "use strict";

  function buildUrl(opts) {
    if (opts.embedUrl) return opts.embedUrl;
    if (!opts.baseUrl || !opts.token || !opts.surface) {
      throw new Error("Windrose.embed: pass embedUrl, or baseUrl+token+surface");
    }
    var base = opts.baseUrl.replace(/\/$/, "");
    var path =
      opts.surface === "dashboard" && opts.resourceId
        ? "/embed/dashboard/" + encodeURIComponent(opts.resourceId)
        : "/embed/" + opts.surface;
    return base + path + "?t=" + encodeURIComponent(opts.token);
  }

  function embed(el, opts) {
    opts = opts || {};
    if (!el) throw new Error("Windrose.embed: a container element is required");

    var url = buildUrl(opts);
    if (opts.theme) url += (url.indexOf("?") >= 0 ? "&" : "?") + "theme=" + opts.theme;
    var frameOrigin = new URL(url, global.location.href).origin;

    var iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.style.width = "100%";
    iframe.style.border = "0";
    iframe.style.display = "block";
    iframe.style.height = (opts.height || 600) + "px";
    iframe.setAttribute("title", "Windrose " + (opts.surface || "embed"));
    iframe.setAttribute("loading", "lazy");
    el.appendChild(iframe);

    function onMessage(e) {
      // Only trust messages from the Windrose iframe origin.
      if (e.origin !== frameOrigin) return;
      var d = e.data;
      if (!d || d.source !== "windrose-embed") return;
      if (d.type === "windrose:resize" && typeof d.height === "number" && !opts.fixedHeight) {
        iframe.style.height = d.height + "px";
      } else if (d.type === "windrose:ready" && typeof opts.onReady === "function") {
        opts.onReady();
      }
    }
    global.addEventListener("message", onMessage);

    function post(msg) {
      if (iframe.contentWindow) {
        iframe.contentWindow.postMessage(
          Object.assign({ source: "windrose-host" }, msg),
          frameOrigin,
        );
      }
    }

    return {
      iframe: iframe,
      setTheme: function (theme) {
        post({ type: "windrose:set-theme", theme: theme });
      },
      destroy: function () {
        global.removeEventListener("message", onMessage);
        if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
      },
    };
  }

  global.Windrose = { embed: embed };
})(typeof window !== "undefined" ? window : this);
