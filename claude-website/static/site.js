(function () {
  var base = (window.SITE && window.SITE.base) || "";

  /* ---- header search (lazy-loads the index on first focus) ---- */
  var box = document.getElementById("site-search");
  var list = document.getElementById("search-results");
  var index = null, loading = false;
  function load(cb) {
    if (index) return cb();
    if (loading) return;
    loading = true;
    fetch(base + "/search-index.json").then(function (r) { return r.json(); })
      .then(function (d) { index = d; cb(); }).catch(function () { loading = false; });
  }
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
      a.textContent = row[0] + " ";
      sm.textContent = row[1] + ", " + row[2];
      a.appendChild(sm); li.appendChild(a); list.appendChild(li);
    });
    list.hidden = out.length === 0;
  }
  if (box) {
    box.addEventListener("focus", function () { load(run); });
    box.addEventListener("input", function () { load(run); });
    document.addEventListener("click", function (e) { if (!e.target.closest(".search")) list.hidden = true; });
  }

  /* ---- listing filters (area pages) ---- */
  if (!window.FILTER) return;
  var grid = document.getElementById("grid");
  var cards = Array.prototype.slice.call(grid.children);
  var q = document.getElementById("flt-q"), cu = document.getElementById("flt-cuisine"),
      ra = document.getElementById("flt-rating"), so = document.getElementById("flt-sort"),
      cnt = document.getElementById("flt-count"), empty = document.getElementById("flt-empty");
  var original = cards.slice();
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
    cnt.textContent = shown + (shown === 1 ? " restaurant" : " restaurants");
    empty.hidden = shown !== 0;
  }
  [q, cu, ra, so].forEach(function (el) { el.addEventListener("input", apply); el.addEventListener("change", apply); });
})();
