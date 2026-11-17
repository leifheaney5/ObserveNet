/* Leifslab home — renders the service directory from /services.json.
 * No dependencies, no inline styles, no innerHTML: safe under the site's strict CSP. */
(function () {
  "use strict";

  var STORE_PINS = "leifslab.pins";
  var STORE_RECENT = "leifslab.recent";
  var STORE_THEME = "leifslab.theme";
  var MAX_RECENT = 6;
  var MAX_QUICK = 10;
  var CATEGORY_CLASS = {
    "Research & discovery": "c0",
    "Media & entertainment": "c1",
    "Media management": "c2",
    "Personal & planning": "c3",
    "Files & communication": "c4",
    "Systems & monitoring": "c5",
  };
  var STATUS = {
    responding: { cls: "is-ok", label: "Online", ok: true },
    sign_in_required: { cls: "is-signin", label: "Online, sign-in required", ok: true },
  };
  var NS = "http://www.w3.org/2000/svg";
  var STAR = "M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z";

  var $ = function (id) {
    return document.getElementById(id);
  };
  var els = {
    input: $("search-input"),
    hint: $("search-hint"),
    clear: $("search-clear"),
    chips: $("chips"),
    status: $("result-status"),
    quick: $("quick"),
    quickList: $("quick-list"),
    results: $("results"),
    footer: $("footer-summary"),
    greeting: $("greeting"),
    theme: $("theme-toggle"),
    topbar: document.querySelector(".topbar"),
  };

  var state = { services: [], categories: [], query: "", category: "", issuesOnly: false };
  var pins = readList(STORE_PINS);
  var recent = readList(STORE_RECENT);

  // ---------- small helpers ----------

  function readList(key) {
    try {
      var value = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(value) ? value.filter(function (v) { return typeof v === "string"; }) : [];
    } catch (e) {
      return [];
    }
  }

  function writeList(key, list) {
    try {
      localStorage.setItem(key, JSON.stringify(list));
    } catch (e) {
      /* private mode: pins simply do not persist */
    }
  }

  function h(tag, props) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var value = props[key];
        if (value === null || value === undefined || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else node.setAttribute(key, value === true ? "" : value);
      });
    }
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function icon(path, filled) {
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(NS, "path");
    p.setAttribute("d", path);
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", "1.7");
    p.setAttribute("stroke-linejoin", "round");
    if (filled) p.setAttribute("fill", "currentColor");
    svg.appendChild(p);
    return svg;
  }

  function hostOf(url) {
    try {
      return new URL(url).host;
    } catch (e) {
      return url;
    }
  }

  function monogram(name) {
    var words = String(name).trim().split(/\s+/);
    if (words[0].length <= 3 && words[0] === words[0].toUpperCase()) return words[0].slice(0, 3);
    if (words.length > 1) return words[0][0] + words[1][0];
    return words[0].slice(0, 2);
  }

  function categoryClass(category) {
    if (CATEGORY_CLASS[category]) return CATEGORY_CLASS[category];
    var index = state.categories.indexOf(category);
    return "c" + ((index < 0 ? 0 : index) % 6);
  }

  function statusOf(service) {
    var known = STATUS[service.status];
    if (known) return known;
    var label = String(service.status || "unknown").replace(/_/g, " ");
    return { cls: "is-issue", label: label.charAt(0).toUpperCase() + label.slice(1), ok: false };
  }

  function timeAgo(iso) {
    var then = Date.parse(iso);
    if (isNaN(then)) return "";
    var seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (seconds < 90) return "just now";
    var minutes = Math.round(seconds / 60);
    if (minutes < 90) return minutes + " min ago";
    var hours = Math.round(minutes / 60);
    if (hours < 36) return hours + " h ago";
    return Math.round(hours / 24) + " d ago";
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many || one + "s");
  }

  // ---------- search ----------

  function scoreService(service, terms) {
    var name = service._name;
    var total = 0;
    for (var i = 0; i < terms.length; i++) {
      var term = terms[i];
      var score = 0;
      if (name.indexOf(term) === 0) score = 100;
      else if (name.split(/[\s\-_.]+/).some(function (w) { return w.indexOf(term) === 0; })) score = 80;
      else if (name.indexOf(term) !== -1) score = 60;
      else if (service._host.indexOf(term) !== -1) score = 45;
      else if (service._category.indexOf(term) !== -1) score = 25;
      else if (service._description.indexOf(term) !== -1) score = 15;
      if (!score) return 0; // every term must match somewhere
      total += score;
    }
    return total;
  }

  function visibleServices() {
    var terms = state.query.toLowerCase().split(/\s+/).filter(Boolean);
    var list = state.services.filter(function (s) {
      if (state.category && s.category !== state.category) return false;
      if (state.issuesOnly && statusOf(s).ok) return false;
      return true;
    });
    if (!terms.length) return list;
    return list
      .map(function (s) { return { s: s, score: scoreService(s, terms) }; })
      .filter(function (x) { return x.score > 0; })
      .sort(function (a, b) { return b.score - a.score || a.s.name.localeCompare(b.s.name); })
      .map(function (x) { return x.s; });
  }

  /** Text with the first match of the first search term wrapped in <mark>. */
  function highlighted(text) {
    var term = state.query.trim().toLowerCase().split(/\s+/)[0];
    var span = h("span");
    var at = term ? text.toLowerCase().indexOf(term) : -1;
    if (at === -1) {
      span.textContent = text;
      return span;
    }
    span.appendChild(document.createTextNode(text.slice(0, at)));
    span.appendChild(h("mark", { text: text.slice(at, at + term.length) }));
    span.appendChild(document.createTextNode(text.slice(at + term.length)));
    return span;
  }

  // ---------- rendering ----------

  function card(service) {
    var st = statusOf(service);
    var pinned = pins.indexOf(service.id) !== -1;
    var checked = service.checked_at ? " Checked " + timeAgo(service.checked_at) + "." : "";
    var link = h(
      "a",
      { class: "card-link", href: service.url, "data-id": service.id, rel: "noopener" },
      h(
        "span",
        { class: "tile-wrap" },
        h("span", { class: "tile", "aria-hidden": "true", text: monogram(service.name) }),
        h("span", { class: "status " + st.cls, role: "img", "aria-label": st.label, title: st.label + ". " + (service.status_note || "") + checked })
      ),
      h(
        "div",
        { class: "card-body" },
        h("p", { class: "card-name" }, highlighted(service.name)),
        h("p", { class: "card-host" }, highlighted(hostOf(service.url))),
        h("p", { class: "card-desc", text: service.description || "" })
      )
    );
    var pin = h(
      "button",
      {
        class: "pin",
        type: "button",
        "data-pin": service.id,
        "aria-pressed": pinned ? "true" : "false",
        "aria-label": (pinned ? "Unpin " : "Pin ") + service.name,
        title: pinned ? "Unpin from quick access" : "Pin to quick access",
      },
      icon(STAR)
    );
    return h("li", { class: "card " + categoryClass(service.category) }, link, pin);
  }

  function renderChips() {
    var counts = {};
    var issues = 0;
    state.services.forEach(function (s) {
      counts[s.category] = (counts[s.category] || 0) + 1;
      if (!statusOf(s).ok) issues++;
    });
    els.chips.textContent = "";
    var all = !state.category && !state.issuesOnly;
    els.chips.appendChild(
      h("button", { class: "chip", type: "button", "data-category": "", "aria-pressed": all ? "true" : "false" }, "All", h("span", { class: "chip-count", text: String(state.services.length) }))
    );
    state.categories.forEach(function (category) {
      els.chips.appendChild(
        h(
          "button",
          { class: "chip " + categoryClass(category), type: "button", "data-category": category, "aria-pressed": state.category === category ? "true" : "false" },
          h("span", { class: "chip-dot", "aria-hidden": "true" }),
          category,
          h("span", { class: "chip-count", text: String(counts[category]) })
        )
      );
    });
    if (issues) {
      els.chips.appendChild(
        h(
          "button",
          { class: "chip", type: "button", "data-issues": "1", "aria-pressed": state.issuesOnly ? "true" : "false" },
          h("span", { class: "status is-issue", "aria-hidden": "true" }),
          "Needs attention",
          h("span", { class: "chip-count", text: String(issues) })
        )
      );
    }
  }

  function renderQuick() {
    var byId = {};
    state.services.forEach(function (s) { byId[s.id] = s; });
    var ids = pins.concat(recent.filter(function (id) { return pins.indexOf(id) === -1; }));
    var items = ids.map(function (id) { return byId[id]; }).filter(Boolean).slice(0, MAX_QUICK);
    var filtering = state.query.trim() || state.category || state.issuesOnly;
    els.quick.hidden = !items.length || !!filtering;
    els.quickList.textContent = "";
    items.forEach(function (service) {
      var pinned = pins.indexOf(service.id) !== -1;
      els.quickList.appendChild(
        h(
          "li",
          null,
          h(
            "a",
            { class: "quick-item " + categoryClass(service.category), href: service.url, "data-id": service.id, rel: "noopener", title: hostOf(service.url) },
            h("span", { class: "tile", "aria-hidden": "true", text: monogram(service.name) }),
            service.name,
            pinned ? icon(STAR, true) : null
          )
        )
      );
    });
  }

  function renderResults() {
    var list = visibleServices();
    var searching = !!state.query.trim();
    els.results.textContent = "";
    els.results.setAttribute("aria-busy", "false");

    if (!list.length) {
      var reset = h("button", { type: "button", id: "reset-filters", text: "Show everything" });
      els.results.appendChild(
        h(
          "div",
          { class: "empty" },
          h("strong", { text: searching ? "Nothing matches “" + state.query.trim() + "”" : "Nothing here" }),
          "Try a shorter word, a different category, or part of the address.",
          h("br"),
          reset
        )
      );
    } else if (searching || state.issuesOnly) {
      var flat = h("ul", { class: "grid" });
      list.forEach(function (s) { flat.appendChild(card(s)); });
      els.results.appendChild(
        h("div", { class: "section-head" }, h("h2", { text: state.issuesOnly && !searching ? "Needs attention" : "Results" }), h("span", { class: "section-count", text: String(list.length) }), h("span", { class: "section-rule" }))
      );
      els.results.appendChild(flat);
    } else {
      state.categories.forEach(function (category) {
        var members = list.filter(function (s) { return s.category === category; });
        if (!members.length) return;
        var grid = h("ul", { class: "grid" });
        members.forEach(function (s) { grid.appendChild(card(s)); });
        var section = h(
          "section",
          { "aria-label": category },
          h("div", { class: "section-head" }, h("h2", { text: category }), h("span", { class: "section-count", text: String(members.length) }), h("span", { class: "section-rule" })),
          grid
        );
        els.results.appendChild(section);
      });
    }

    var total = state.services.length;
    if (searching) els.status.textContent = plural(list.length, "match", "matches") + " for “" + state.query.trim() + "”" + (list.length ? " — press Enter to open the first" : "");
    else if (state.issuesOnly) els.status.textContent = plural(list.length, "service") + " did not respond normally at the last check";
    else if (state.category) els.status.textContent = plural(list.length, "service") + " in " + state.category;
    else els.status.textContent = plural(total, "service") + " in your directory";

    els.clear.hidden = !searching;
    els.hint.hidden = searching;
  }

  function renderFooter() {
    var ok = state.services.filter(function (s) { return statusOf(s).ok; }).length;
    var issues = state.services.length - ok;
    var latest = state.services.reduce(function (max, s) {
      var t = Date.parse(s.checked_at);
      return !isNaN(t) && t > max ? t : max;
    }, 0);
    var parts = [plural(state.services.length, "service"), ok + " online"];
    if (issues) parts.push(issues + " need attention");
    if (latest) parts.push("status checked " + timeAgo(new Date(latest).toISOString()));
    els.footer.textContent = parts.join(" · ");
  }

  function render() {
    renderChips();
    renderQuick();
    renderResults();
  }

  // ---------- actions ----------

  function rememberVisit(id) {
    recent = [id].concat(recent.filter(function (x) { return x !== id; })).slice(0, MAX_RECENT);
    writeList(STORE_RECENT, recent);
  }

  function togglePin(id) {
    var at = pins.indexOf(id);
    if (at === -1) pins.push(id);
    else pins.splice(at, 1);
    writeList(STORE_PINS, pins);
    render();
    var again = els.results.querySelector('[data-pin="' + CSS.escape(id) + '"]');
    if (again) again.focus();
  }

  function setQuery(value) {
    state.query = value;
    render();
  }

  function cardLinks() {
    return Array.prototype.slice.call(els.results.querySelectorAll(".card-link"));
  }

  function moveFocus(step) {
    var links = cardLinks();
    if (!links.length) return;
    var at = links.indexOf(document.activeElement);
    var next = at === -1 ? (step > 0 ? 0 : links.length - 1) : at + step;
    if (next < 0) {
      els.input.focus();
      return;
    }
    links[Math.min(next, links.length - 1)].focus();
  }

  // ---------- events ----------

  els.input.addEventListener("input", function () {
    setQuery(els.input.value);
  });

  els.input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      var first = cardLinks()[0];
      if (first && state.query.trim()) {
        rememberVisit(first.getAttribute("data-id"));
        window.location.assign(first.href);
      }
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      moveFocus(1);
    } else if (event.key === "Escape" && els.input.value) {
      els.input.value = "";
      setQuery("");
    }
  });

  els.clear.addEventListener("click", function () {
    els.input.value = "";
    setQuery("");
    els.input.focus();
  });

  els.chips.addEventListener("click", function (event) {
    var chip = event.target.closest(".chip");
    if (!chip) return;
    if (chip.hasAttribute("data-issues")) {
      state.issuesOnly = !state.issuesOnly;
      state.category = "";
    } else {
      var category = chip.getAttribute("data-category");
      state.category = state.category === category ? "" : category;
      state.issuesOnly = false;
    }
    render();
  });

  document.addEventListener("click", function (event) {
    var pin = event.target.closest("[data-pin]");
    if (pin) {
      event.preventDefault();
      togglePin(pin.getAttribute("data-pin"));
      return;
    }
    if (event.target.closest("#reset-filters")) {
      state.category = "";
      state.issuesOnly = false;
      els.input.value = "";
      setQuery("");
      els.input.focus();
      return;
    }
    var link = event.target.closest("a[data-id]");
    if (link) rememberVisit(link.getAttribute("data-id"));
  });

  document.addEventListener("auxclick", function (event) {
    var link = event.target.closest("a[data-id]");
    if (link) rememberVisit(link.getAttribute("data-id"));
  });

  document.addEventListener("keydown", function (event) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if ((event.key === "/" && !typing) || (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey))) {
      event.preventDefault();
      els.input.focus();
      els.input.select();
    } else if (!typing && (event.key === "ArrowDown" || event.key === "ArrowUp") && document.activeElement.classList.contains("card-link")) {
      event.preventDefault();
      moveFocus(event.key === "ArrowDown" ? 1 : -1);
    }
  });

  els.theme.addEventListener("click", function () {
    var root = document.documentElement;
    var dark = root.getAttribute("data-theme")
      ? root.getAttribute("data-theme") === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORE_THEME, next);
    } catch (e) {
      /* not persisted */
    }
  });

  window.addEventListener(
    "scroll",
    function () {
      els.topbar.classList.toggle("is-scrolled", window.scrollY > 8);
    },
    { passive: true }
  );

  // ---------- start ----------

  function greet() {
    var hour = new Date().getHours();
    els.greeting.textContent = hour < 5 ? "Up late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  }

  function showLoadError() {
    els.results.textContent = "";
    els.results.setAttribute("aria-busy", "false");
    var retry = h("button", { type: "button", text: "Try again" });
    retry.addEventListener("click", load);
    els.results.appendChild(h("div", { class: "empty" }, h("strong", { text: "Could not load your services" }), "The directory file did not load. Check your connection and try again.", h("br"), retry));
    els.status.textContent = "";
    els.footer.textContent = "Directory unavailable";
  }

  function load() {
    fetch("/services.json", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (!Array.isArray(data)) throw new Error("unexpected format");
        state.services = data
          .filter(function (s) { return s && s.id && s.name && s.url; })
          .map(function (s) {
            s.category = s.category || "Other";
            s._name = String(s.name).toLowerCase();
            s._host = hostOf(s.url).toLowerCase();
            s._category = String(s.category).toLowerCase();
            s._description = String(s.description || "").toLowerCase();
            return s;
          });
        state.categories = [];
        state.services.forEach(function (s) {
          if (state.categories.indexOf(s.category) === -1) state.categories.push(s.category);
        });
        var initial = new URLSearchParams(window.location.search).get("q");
        if (initial) {
          els.input.value = initial;
          state.query = initial;
        }
        render();
        renderFooter();
      })
      .catch(showLoadError);
  }

  greet();
  load();
})();
