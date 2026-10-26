(function () {
  "use strict";
  var S = window.SITE || {}, base = S.base || "", api = S.photoApi || "";

  /* ---------- search boxes (header + home hero) ---------- */
  var index = null, loading = false, waiting = [];
  function loadIndex(cb) {
    if (index) return cb();
    waiting.push(cb);
    if (loading) return;
    loading = true;
    fetch(base + "/search-index.json").then(function (r) { return r.json(); })
      .then(function (d) { index = d; waiting.splice(0).forEach(function (f) { f(); }); })
      .catch(function () { loading = false; });
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-search]"), function (w) {
    var box = w.querySelector("input"), list = w.querySelector(".results");
    function run() {
      var q = box.value.toLowerCase().trim();
      list.innerHTML = "";
      if (q.length < 2 || !index) { list.hidden = true; return; }
      var parts = q.split(/\s+/), out = [];
      for (var i = 0; i < index.length && out.length < 8; i++) {
        var row = index[i], hay = (row[0] + " " + row[1] + " " + row[2]).toLowerCase();
        if (parts.every(function (p) { return hay.indexOf(p) > -1; })) out.push(row);
      }
      out.forEach(function (row) {
        var li = document.createElement("li"), a = document.createElement("a"), sm = document.createElement("small");
        a.href = base + "/" + row[3] + "/";
        a.appendChild(document.createTextNode(row[0]));
        sm.textContent = row[1] + ", " + row[2];
        a.appendChild(sm); li.appendChild(a); list.appendChild(li);
      });
      list.hidden = out.length === 0;
    }
    function go() {
      var a = list.querySelector("a");
      if (a) { window.location.href = a.href; } else { box.focus(); }
    }
    var btn = w.querySelector("[data-search-go]");
    if (btn) btn.addEventListener("click", function () { loadIndex(function () { run(); go(); }); });
    box.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); loadIndex(function () { run(); go(); }); } });
    box.addEventListener("focus", function () { loadIndex(run); });
    box.addEventListener("input", function () { loadIndex(run); });
    box.addEventListener("keydown", function (e) { if (e.key === "Escape") list.hidden = true; });
  });
  document.addEventListener("click", function (e) {
    Array.prototype.forEach.call(document.querySelectorAll(".results"), function (l) { if (!e.target.closest(".search")) l.hidden = true; });
    var m = document.querySelector(".menu[open]");
    if (m && !e.target.closest(".menu")) m.removeAttribute("open");
  });

  /* ---------- real-time photo recovery ----------
     If a restaurant photo fails to load (expired Google URL), ask the photo service
     (apps_script/photo_proxy) for a fresh one from the restaurant's Google profile.
     Results are cached in localStorage so each photo is requested at most once per 6 hours. */
  var TTL = 6 * 3600 * 1000, inflight = {};
  function cget(k) { try { var v = JSON.parse(localStorage.getItem(k) || "null"); if (v && Date.now() - v.t < TTL) return v; } catch (e) {} return null; }
  function cset(k, v) { try { v.t = Date.now(); localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function fetchPhoto(place, i, cb) {
    var key = "ph:" + place + ":" + i, hit = cget(key);
    if (hit) return cb(hit.u ? hit : null);
    if (!api) return cb(null);
    if (inflight[key]) { inflight[key].push(cb); return; }
    inflight[key] = [cb];
    function done(res) { var l = inflight[key]; delete inflight[key]; l.forEach(function (f) { f(res); }); }
    fetch(api + (api.indexOf("?") > -1 ? "&" : "?") + "place_id=" + encodeURIComponent(place) + "&i=" + i + "&w=900")
      .then(function (r) { return r.json(); })
      .then(function (d) { var res = d && d.url ? { u: d.url, a: d.author || "" } : { u: "", a: "" }; cset(key, res); done(res.u ? res : null); })
      .catch(function () { done(null); });
  }
  function credit(author) {
    var el = document.getElementById("photo-credit");
    if (el && author) { el.textContent = "Photo: " + author + " (via Google Maps)"; el.hidden = false; }
  }
  function giveUp(img) {
    var fig = img.closest("figure");
    if (fig) fig.style.display = "none"; else img.remove();
  }
  function recover(img) {
    if (!img) return;
    var orig = img.dataset.orig;                       // 1) retry once with the link exactly as scraped
    if (orig && !img.dataset.triedOrig && img.src !== orig) {
      img.dataset.triedOrig = "1";
      img.src = orig;
      return;
    }
    if (img.dataset.tried) return;                     // 2) then ask the photo service
    img.dataset.tried = "1";
    fetchPhoto(img.dataset.place, img.dataset.i || 0, function (res) {
      if (!res) return giveUp(img);
      img.onerror = function () { giveUp(img); };
      img.removeAttribute("srcset");
      img.src = res.u;
      credit(res.a);
    });
  }
  function fill(box) {                       // container that has no photo at all
    if (box.dataset.tried) return;
    box.dataset.tried = "1";
    fetchPhoto(box.dataset.place, box.dataset.i || 0, function (res) {
      if (!res) return;
      var img = new Image();
      img.alt = box.dataset.alt || ""; img.referrerPolicy = "no-referrer";
      img.dataset.tried = "1";
      img.onload = function () {
        if (box.classList.contains("gallery")) {
          var old = box.querySelector(".ph-fig"); if (old) old.remove();
          var f = document.createElement("figure"); f.appendChild(img); box.appendChild(f);
          box.classList.remove("n0"); box.classList.add("n1"); box.style.display = "";
        } else box.appendChild(img);
        credit(res.a);
      };
      img.src = res.u;
    });
  }
  window.imgFail = recover;
  (window.__imgErr || []).forEach(recover);
  Array.prototype.forEach.call(document.querySelectorAll("img[data-place]"), function (img) {
    if (img.complete && img.naturalWidth === 0) recover(img);
  });
  var empties = document.querySelectorAll("[data-recover]");
  if (empties.length) {
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); fill(e.target); } });
      }, { rootMargin: "300px" });
      Array.prototype.forEach.call(empties, function (b) {
        if (b.classList.contains("gallery")) b.style.display = "grid";
        io.observe(b);
      });
    } else Array.prototype.forEach.call(empties, fill);
  }

  /* ---------- home: open with ?q=... (used by the site search action) ---------- */
  (function () {
    var m = location.search.match(/[?&]q=([^&]*)/), hb = document.getElementById("q-hero");
    if (m && hb) {
      try { hb.value = decodeURIComponent(m[1].replace(/\+/g, " ")); } catch (e) { return; }
      hb.dispatchEvent(new Event("input"));
    }
  })();

  /* ---------- review slider ---------- */
  document.querySelectorAll("[data-slider]").forEach(function (s) {
    var tr = s.querySelector(".rev-track"), p = s.querySelector(".rev-prev"), n = s.querySelector(".rev-next");
    function step(d) { tr.scrollBy({ left: d * Math.max(260, tr.clientWidth * 0.9), behavior: "smooth" }); }
    p.addEventListener("click", function () { step(-1); });
    n.addEventListener("click", function () { step(1); });
    tr.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1); });
  });

  /* ---------- contact form (posts to the Apps Script web app; the email address is never on the site) ---------- */
  (function () {
    var f = document.getElementById("contact-form"), st = document.getElementById("form-status");
    if (!f) return;
    function say(msg, ok) { st.hidden = false; st.textContent = msg; st.className = "form-status " + (ok ? "ok" : "bad"); }
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var d = new FormData(f);
      if (d.get("website")) return;                                         // honeypot: bots fill this in
      if (!f.checkValidity()) { f.reportValidity(); return; }
      var api = f.dataset.api || S.contactApi;
      if (!api) { say("The contact form is not switched on yet. Please try again later.", false); return; }
      var btn = f.querySelector("button[type=submit]");
      btn.disabled = true; btn.textContent = "Sending...";
      var body = new URLSearchParams();
      d.forEach(function (v, k) { body.append(k, v); });
      fetch(api, { method: "POST", mode: "no-cors", body: body })
        .then(function () { f.reset(); say("Thank you. Your message has been sent and we will reply by email.", true); })
        .catch(function () { say("Sorry, the message could not be sent. Please try again in a moment.", false); })
        .then(function () { btn.disabled = false; btn.textContent = "Send message"; });
    });
  })();

  /* ---------- photo gallery lightbox (restaurant pages) ---------- */
  (function () {
    var box = document.getElementById("lightbox");
    var btns = Array.prototype.slice.call(document.querySelectorAll(".g-btn"));
    if (!box || !btns.length || !box.showModal) return;
    var img = document.getElementById("lb-img"), cap = document.getElementById("lb-cap"), cur = 0;
    function show(i) {
      cur = (i + btns.length) % btns.length;
      img.src = btns[cur].dataset.full;
      img.alt = btns[cur].dataset.alt || "";
      cap.textContent = (cur + 1) + " / " + btns.length;
    }
    btns.forEach(function (b, i) { b.addEventListener("click", function () { show(i); box.showModal(); }); });
    box.querySelector(".lb-close").addEventListener("click", function () { box.close(); });
    box.querySelector(".lb-prev").addEventListener("click", function () { show(cur - 1); });
    box.querySelector(".lb-next").addEventListener("click", function () { show(cur + 1); });
    box.addEventListener("click", function (e) { if (e.target === box) box.close(); });
    box.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") show(cur - 1); else if (e.key === "ArrowRight") show(cur + 1);
    });
  })();

  /* ---------- listing filters (area pages) ---------- */
  if (!window.FILTER) return;
  var grid = document.getElementById("grid");
  if (!grid) return;
  var original = Array.prototype.slice.call(grid.children);
  var q = document.getElementById("flt-q"), cu = document.getElementById("flt-cuisine"),
      ra = document.getElementById("flt-rating"), so = document.getElementById("flt-sort"),
      empty = document.getElementById("flt-empty");
  function apply() {
    var text = q.value.toLowerCase().trim(), cuisine = cu.value, minr = parseFloat(ra.value) || 0, shown = 0;
    var sorted = original.slice();
    if (so.value === "reviews") sorted.sort(function (a, b) { return b.dataset.reviews - a.dataset.reviews; });
    if (so.value === "name") sorted.sort(function (a, b) { return a.dataset.name.localeCompare(b.dataset.name); });
    sorted.forEach(function (c) {
      var ok = (!text || c.dataset.name.indexOf(text) > -1) && (!cuisine || c.dataset.cuisine === cuisine) &&
               (parseFloat(c.dataset.rating) >= minr);
      c.hidden = !ok; if (ok) shown++;
      grid.appendChild(c);
    });
    empty.hidden = shown !== 0;
  }
  [q, cu, ra, so].forEach(function (el) { el.addEventListener("input", apply); el.addEventListener("change", apply); });
})();
(function () {
  document.addEventListener("click", function (e) {
    var c = e.target.closest && e.target.closest(".card");
    if (!c || e.target.closest("a")) return;
    var a = c.querySelector("h3 a"); if (a) location.href = a.href;
  });
})();
