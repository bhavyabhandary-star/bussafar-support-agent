// BusSafar page: help content comes from /api/help (the same help docs Sahil answers from).
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // Any [data-ask] opens Sahil, sending its question if it has one.
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ask]");
    if (b) { e.preventDefault(); window.Sahil.open(b.getAttribute("data-ask") || ""); }
  });
  $("#askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#askInput").value.trim();
    window.Sahil.open(q);
    $("#askInput").value = "";
  });

  let data = { faqs: [], topics: [], policies: [] };
  let topic = "All";

  function renderSlabs() {
    const pol = data.policies.find((p) => /cancellation and refund/i.test(p.title));
    const list = $("#slabs");
    list.textContent = "";
    if (!pol) { list.appendChild(el("li", "slab-loading", "Ask Sahil for the refund rules.")); return; }
    // Only the refund lines, word for word from the policy.
    pol.lines.filter((l) => /refund/i.test(l) && /before departure/i.test(l)).forEach((line) => {
      const [when, what] = line.split(/:\s*/);
      const li = el("li", "slab");
      const pct = (what || "").match(/\d+%/);
      li.appendChild(el("span", "slab-val" + (pct ? "" : " none"), pct ? pct[0] : "0%"));
      const t = el("span", "slab-txt");
      t.appendChild(el("strong", null, when));
      t.appendChild(el("span", null, (what || "").replace(/\.$/, "")));
      li.appendChild(t);
      list.appendChild(li);
    });
    const extra = pol.lines.filter((l) => /reach the original|date change/i.test(l));
    $("#slabSource").textContent = extra.join(" ") + " Source: " + pol.title + ".";
  }

  function renderTopics() {
    const box = $("#topics");
    box.textContent = "";
    ["All"].concat(data.topics).forEach((t) => {
      if (t !== "All" && !data.faqs.some((f) => f.topic === t)) return;
      const b = el("button", "chip" + (t === topic ? " on" : ""), t);
      b.type = "button";
      b.setAttribute("aria-pressed", t === topic ? "true" : "false");
      b.addEventListener("click", () => { topic = t; renderTopics(); renderFaqs(); });
      box.appendChild(b);
    });
  }

  function renderFaqs() {
    const q = $("#helpSearch").value.trim().toLowerCase();
    const box = $("#faqs");
    box.textContent = "";
    const hits = data.faqs.filter((f) => (topic === "All" || f.topic === topic) &&
      (!q || (f.q + " " + f.a).toLowerCase().includes(q)));
    hits.forEach((f) => {
      const d = el("details", "faq");
      d.appendChild(el("summary", null, f.q));
      const body = el("div", "faq-body");
      body.appendChild(el("p", null, f.a));
      const ask = el("button", "link", "Ask Sahil about this");
      ask.type = "button";
      ask.setAttribute("data-ask", f.q);
      body.appendChild(ask);
      d.appendChild(body);
      box.appendChild(d);
    });
    $("#noResults").hidden = hits.length > 0;
  }

  $("#helpSearch").addEventListener("input", renderFaqs);
  $("#askInstead").addEventListener("click", () => window.Sahil.open($("#helpSearch").value.trim()));

  fetch("/api/help").then((r) => r.json()).then((d) => {
    data = d; renderSlabs(); renderTopics(); renderFaqs();
  }).catch(() => {
    $("#slabs").textContent = "";
    $("#slabs").appendChild(el("li", "slab-loading", "Help is unavailable right now. Please try again."));
  });
})();
