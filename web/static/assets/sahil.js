// Sahil: BusSafar's floating support chat. Talks to /api/session and /api/chat.
// All server text is inserted with textContent, never as HTML.
(function () {
  "use strict";
  // The chat session is kept in memory only, so every page load asks for the passcode again.
  // Clear what older versions saved in the tab.
  try { sessionStorage.removeItem("bussafar.sahil.session"); } catch (_) {}
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  let session = null;
  let turns = [];          // recent turns, sent so a fresh server instance can pick up the chat
  let busy = false;
  let pending = "";       // question waiting for the passcode
  const needsPasscode = true; // the server always needs the passcode

  // Fixed icon markup (our own constants, never server text).
  const ICONS = {
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    doc: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    booking: '<path d="M4 7h16v4a2 2 0 0 0 0 4v4H4v-4a2 2 0 0 0 0-4z"/><path d="M10 7v12"/>',
    ticket: '<path d="M9 5H5v14h14v-4"/><path d="M13 5h6v6"/><path d="M19 5l-8 8"/>',
    contact: '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>',
  };
  function icon(name, size, cls) {
    const s = document.createElement("span");
    s.className = "sahil-ico" + (cls ? " " + cls : "");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
    return s;
  }
  function avatar() {
    const a = el("span", "sahil-avatar", "S");
    a.append(el("span", "sahil-online"));
    return a;
  }

  // ---- build the widget ----
  const launcher = el("button", "sahil-launch");
  launcher.type = "button";
  launcher.setAttribute("aria-label", "Chat with Sahil, BusSafar support");
  launcher.setAttribute("aria-expanded", "false");
  launcher.append(avatar(), el("span", "sahil-launch-txt", "Ask Sahil"));

  const panel = el("section", "sahil-panel");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Sahil, BusSafar support chat");
  panel.hidden = true;

  const head = el("div", "sahil-head");
  const who = el("div", "sahil-who");
  who.append(avatar());
  const names = el("div");
  names.append(el("strong", null, "Sahil"), el("span", "sahil-sub", "BusSafar support"));
  who.append(names);
  const newBtn = el("button", "sahil-icon", "New chat");
  newBtn.type = "button";
  const close = el("button", "sahil-icon sahil-x");
  close.append(icon("close", 16));
  close.type = "button";
  close.setAttribute("aria-label", "Close chat");
  const tools = el("div", "sahil-tools");
  tools.append(newBtn, close);
  head.append(who, tools);

  const sos = el("div", "sahil-sos");
  sos.hidden = true;
  sos.append("In danger? Call ");
  const tel = el("a", null, "112");
  tel.href = "tel:112";
  sos.append(tel, " now.");

  const log = el("div", "sahil-log");
  log.setAttribute("aria-live", "polite");

  const gate = el("form", "sahil-gate");
  const gateHead = el("div", "sahil-gate-head");
  gateHead.append(icon("lock", 16, "sahil-badge"), el("p", null, "Enter the demo passcode to start chatting."));
  gate.append(gateHead);
  const pass = el("input");
  pass.type = "password";
  pass.placeholder = "Passcode";
  pass.setAttribute("aria-label", "Passcode");
  const passBtn = el("button", "btn btn-primary", "Start chat");
  passBtn.type = "submit";
  const gateErr = el("p", "sahil-err");
  gateErr.id = "sahilGateErr";
  const gateRow = el("div", "sahil-row");
  gateRow.append(pass, passBtn);
  gate.append(gateRow, gateErr);

  const form = el("form", "sahil-form");
  const input = el("textarea");
  input.rows = 1;
  input.maxLength = 1000;
  input.placeholder = "Type your question…";
  input.setAttribute("aria-label", "Message Sahil");
  const send = el("button", "btn btn-primary", "Send");
  send.type = "submit";
  form.append(input, send);

  const quick = el("div", "sahil-quick");
  // Chips that open a form go straight to the fields; the others send a question.
  [["Check booking", () => showForm("booking")], ["Refund rules", () => ask("How much refund will I get if I cancel?")],
   ["Track ticket", () => showForm("ticket_status")], ["Talk to a person", () => showForm("contact")]].forEach(([label, fn]) => {
    const b = el("button", "chip chip-sm", label);
    b.type = "button";
    b.addEventListener("click", fn);
    quick.append(b);
  });

  panel.append(head, sos, log, gate, quick, form);
  document.body.append(launcher, panel);

  // ---- rendering ----
  function bubble(role, text, extra) {
    const row = el("div", "sahil-msg " + role);
    const b = el("div", "sahil-bubble");
    let body = text || "";
    let sources = (extra && extra.sources) || [];
    const m = body.match(/\s*\(Source:\s*([^)]+)\)\s*$/);
    if (m) { body = body.slice(0, m.index); if (!sources.length) sources = m[1].split(/;\s*/); }
    b.append(el("p", null, body));
    if (extra && extra.booking) b.append(bookingCard(extra.booking));
    (extra && extra.ticket_ids || []).forEach((t) => b.append(el("span", "sahil-ticket", "Ticket " + t)));
    if (sources.length) {
      const src = el("span", "sahil-src");
      src.append(icon("doc", 12), "From: " + sources.join(" · "));
      b.append(src);
    }
    row.append(b);
    log.append(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function bookingCard(bk) {
    const c = el("dl", "sahil-booking");
    const pair = (cls, k, v) => {
      const d = el("div", cls);
      d.append(el("dt", null, k), el("dd", null, v));
      return d;
    };
    if (bk.pnr) c.append(pair("bk-pnr", "PNR", bk.pnr));
    const grid = el("div", "bk-grid");
    if (bk.route) grid.append(pair("bk-wide", "Route", bk.route));
    if (bk.travel_date) grid.append(pair(null, "Date", bk.travel_date));
    if (bk.seat) grid.append(pair(null, "Seat", bk.seat));
    if (bk.status) {
      const st = pair("bk-wide bk-status", "Status", bk.status);
      const dd = st.querySelector("dd");
      dd.className = "st st-" + String(bk.status).toLowerCase().replace(/[^a-z]/g, "");
      dd.prepend(el("span", "st-dot"));
      grid.append(st);
    }
    c.append(grid);
    return c;
  }

  // ---- in-chat forms: the customer fills fields; Sahil gets them in the right shape ----
  let categories = [];
  let pendingForm = null;
  fetch("/api/help").then((r) => r.json()).then((d) => { categories = d.ticket_categories || []; }).catch(() => {});

  function field(labelText, control) {
    const l = el("label", "sahil-field");
    l.append(el("span", null, labelText), control);
    return l;
  }
  function textInput(attrs) {
    const i = el("input");
    Object.entries(attrs).forEach(([k, v]) => { i[k] = v; });
    return i;
  }

  function formTitle(name, text) {
    const t = el("strong", "sahil-card-title");
    t.append(icon(name, 15, "sahil-badge"), text);
    return t;
  }

  function showForm(type, opts) {
    opts = opts || {};
    if (!session) { pendingForm = { type, opts }; return needsPasscode ? showGate(true) : start("").then(() => showForm(type, opts)); }
    log.querySelectorAll(".sahil-card:not(.is-done)").forEach((c) => c.closest(".sahil-msg").remove());
    const row = el("div", "sahil-msg bot");
    const card = el("form", "sahil-bubble sahil-card");
    card.noValidate = true;
    const err = el("p", "sahil-err");
    const go = el("button", "btn btn-primary btn-sm");
    go.type = "submit";
    let fields = [];
    let submit;

    if (type === "booking") {
      card.append(formTitle("booking", "Check booking status"));
      const pnr = textInput({ placeholder: "BS123456", maxLength: 12, value: opts.pnr || "", autocomplete: "off" });
      pnr.style.textTransform = "uppercase";
      const last4 = textInput({ type: "password", placeholder: "••••", maxLength: 4, inputMode: "numeric", autocomplete: "off" });
      fields = [field("PNR", pnr), field("Last 4 digits of phone", last4)];
      go.textContent = "Check status";
      submit = () => {
        const p = pnr.value.replace(/\s+/g, "").toUpperCase();
        const d = last4.value.trim();
        if (!/^BS\d{6}$/.test(p)) return "PNR is BS followed by 6 digits, like BS123456.";
        if (!/^\d{4}$/.test(d)) return "Enter the last 4 digits of the phone number on the booking.";
        ask(`Check my booking. PNR ${p}, phone ends in ${d}`, `PNR ${p} · phone ending ••••`);
      };
    } else if (type === "ticket_status") {
      card.append(formTitle("ticket", "Track a support ticket"));
      const tid = textInput({ placeholder: "TKT-1001", maxLength: 12, autocomplete: "off" });
      fields = [field("Ticket ID", tid)];
      go.textContent = "Check ticket";
      submit = async () => {
        const m = tid.value.trim().toUpperCase().match(/^(?:TKT[\s-]?)?(\d{4,6})$/);
        if (!m) return "Ticket IDs look like TKT-1001.";
        try {
          const d = await post("/api/ticket/check", { session, ticket_id: `TKT-${m[1]}` });
          ask(`What is the status of ${d.ticket_id}?`);
        } catch (e2) { return e2.message; }
      };
    } else {
      card.append(formTitle("contact", "Contact a person"));
      const sel = el("select");
      const pick = el("option", null, "Choose one"); pick.value = ""; pick.disabled = true; pick.selected = true; sel.append(pick);
      (categories.length ? categories : [{ id: "other", label: "Other" }]).forEach((c) => {
        const o = el("option", null, c.label); o.value = c.id; sel.append(o);
      });
      const pnr = textInput({ placeholder: "BS123456", maxLength: 12, autocomplete: "off" });
      pnr.style.textTransform = "uppercase";
      const desc = el("textarea");
      desc.rows = 3; desc.maxLength = 1000; desc.placeholder = "What happened?";
      const last4 = textInput({ type: "password", placeholder: "••••", maxLength: 4, inputMode: "numeric", autocomplete: "off" });
      const pnrField = field("PNR", pnr);
      const digitsField = field("Last 4 digits of phone", last4);
      // A safety report must never be held up, so only there the booking details may be left empty.
      sel.addEventListener("change", () => {
        const safety = sel.value === "safety";
        pnrField.firstChild.textContent = safety ? "PNR (optional for safety)" : "PNR";
        digitsField.firstChild.textContent = safety ? "Last 4 digits of phone (optional for safety)" : "Last 4 digits of phone";
      });
      fields = [field("Issue type", sel), pnrField, digitsField, field("Describe the issue", desc)];
      go.textContent = "Create ticket";
      submit = async () => {
        if (!sel.value) return "Please choose an issue type.";
        const p = pnr.value.replace(/\s+/g, "").toUpperCase();
        const d4 = last4.value.trim();
        if (p || sel.value !== "safety") {
          if (!/^BS\d{6}$/.test(p)) return "Please enter your PNR: BS followed by 6 digits, like BS123456.";
          if (!/^\d{4}$/.test(d4)) return "Please enter the last 4 digits of the phone number on the booking.";
        }
        if (desc.value.trim().length < 5) return "Please describe the issue before creating a ticket.";
        const label = sel.options[sel.selectedIndex].text;
        let d;
        try {
          d = await post("/api/ticket", { session, category: sel.value, description: desc.value, pnr: p, last4: d4 });
        } catch (e2) { return e2.message; }   // stays in the form, e.g. a PNR with no booking
        const shown = `${label}${p ? " · " + p : ""}: ${desc.value.trim()}`;
        bubble("me", shown);
        turns.push({ role: "user", text: `[Contact form] ${shown}` }, { role: "model", text: d.text });
        bubble("bot", d.text, d);
        if (d.emergency) sos.hidden = false;
      };
    }
    fields.forEach((f) => card.append(f));
    const cancel = el("button", "btn btn-ghost btn-sm", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => { row.remove(); input.focus(); });
    const actions = el("div", "sahil-actions");
    actions.append(go, cancel);
    card.append(err, actions);
    card.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (busy || go.disabled) return;
      err.textContent = "";
      go.disabled = true;
      const problem = await submit();
      go.disabled = false;
      if (problem) { err.textContent = problem; return; }
      card.classList.add("is-done");
      card.querySelectorAll("input, select, textarea, button").forEach((n) => { n.disabled = true; });
    });
    row.append(card);
    log.append(row);
    log.scrollTop = log.scrollHeight;
    setTimeout(() => { const first = card.querySelector("input, select, textarea"); first && first.focus(); }, 50);
  }

  function typing() {
    const row = el("div", "sahil-msg bot");
    const b = el("div", "sahil-bubble sahil-typing");
    b.setAttribute("aria-label", "Sahil is typing");
    b.append(el("i"), el("i"), el("i"));
    row.append(b);
    log.append(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function showGate(on) {
    gate.hidden = !on;
    form.hidden = on;
    quick.hidden = on;
    if (on) setTimeout(() => pass.focus(), 50); else setTimeout(() => input.focus(), 50);
  }

  // ---- networking ----
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = {};
    try { data = await r.json(); } catch (_) {}
    if (!r.ok) { const e = new Error(data.error || "Something went wrong."); e.status = r.status; throw e; }
    return data;
  }

  let starting = null;     // one session request at a time
  function start(passcode) {
    if (starting) return starting;
    starting = post("/api/session", { passcode: passcode || "" }).then((d) => {
      session = d.session;
      if (!log.children.length) bubble("bot", d.greeting);
      showGate(false);
    }).finally(() => { starting = null; });
    return starting;
  }

  async function ask(text, shown) {
    text = (text || "").trim();
    if (!text || busy) return;
    if (!session) { pending = text; return showGate(true); }
    busy = true;
    send.disabled = true;
    bubble("me", shown || text);
    const t = typing();
    try {
      const d = await post("/api/chat", { session, message: text, history: turns.slice(-12) });
      turns.push({ role: "user", text: shown || text }, { role: "model", text: d.text });
      t.remove();
      bubble("bot", d.text, d);
      if (d.emergency) sos.hidden = false;
      if (d.form) showForm(d.form.type, d.form);
    } catch (err) {
      t.remove();
      if (err.status === 401) {          // chat expired: ask to start again, keep the question
        session = null; pending = text;
        log.lastChild && log.lastChild.remove();
        bubble("bot", "Your chat timed out. Please start again.");
        needsPasscode ? showGate(true) : start("").then(() => ask(pending)).catch(() => {});
      } else {
        bubble("bot", err.message || "Sorry, I couldn't reach BusSafar support. Please try again.");
      }
    } finally {
      busy = false;
      send.disabled = false;
    }
  }

  // ---- events ----
  gate.addEventListener("submit", async (e) => {
    e.preventDefault();
    gateErr.textContent = "";
    gate.classList.remove("has-err");
    passBtn.disabled = true;
    try {
      await start(pass.value);
      pass.value = "";
      const q = pending; pending = "";
      const f = pendingForm; pendingForm = null;
      if (q) ask(q); else if (f) showForm(f.type, f.opts);
    } catch (err) {
      gateErr.textContent = err.status === 401 ? "That passcode isn't right." : err.message;
      gate.classList.add("has-err");
      pass.setAttribute("aria-describedby", "sahilGateErr");
    } finally { passBtn.disabled = false; }
  });

  form.addEventListener("submit", (e) => { e.preventDefault(); const v = input.value; input.value = ""; autosize(); ask(v); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 120) + "px"; }
  input.addEventListener("input", autosize);

  function setOpen(on) {
    panel.hidden = !on;
    launcher.setAttribute("aria-expanded", on ? "true" : "false");
    launcher.classList.toggle("is-open", on);
    document.documentElement.classList.toggle("sahil-open", on);
    if (on) {
      if (!session && !needsPasscode) start("").catch((e) => bubble("bot", e.message));
      showGate(!session && needsPasscode);
      if (session && !log.children.length) bubble("bot", "Welcome back. How can I help with your trip?");
    } else { launcher.focus(); }
  }
  launcher.addEventListener("click", () => setOpen(panel.hidden));
  close.addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) setOpen(false); });
  newBtn.addEventListener("click", () => {
    session = null; log.textContent = ""; sos.hidden = true; turns = [];
    needsPasscode ? showGate(true) : start("").catch((e) => bubble("bot", e.message));
  });


  window.Sahil = {
    form(type) { setOpen(true); showForm(type); },
    open(q) {
      setOpen(true);
      if (!q) return;
      if (session) return ask(q);
      pending = q;
      if (needsPasscode) return showGate(true);
      start("").then(() => { const p = pending; pending = ""; ask(p); }).catch((e) => bubble("bot", e.message));
    },
  };
})();
