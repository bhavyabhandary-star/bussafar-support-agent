// Sahil: BusSafar's floating support chat. Talks to /api/session and /api/chat.
// All server text is inserted with textContent, never as HTML.
(function () {
  "use strict";
  const KEY = "bussafar.sahil.session";
  const store = {
    get() { try { return sessionStorage.getItem(KEY); } catch (_) { return null; } },
    set(v) { try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY); } catch (_) {} },
  };
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  let session = store.get();
  let turns = [];          // recent turns, sent so a fresh server instance can pick up the chat
  let busy = false;
  let pending = "";       // question waiting for the passcode
  let needsPasscode = true;

  // ---- build the widget ----
  const launcher = el("button", "sahil-launch");
  launcher.type = "button";
  launcher.setAttribute("aria-label", "Chat with Sahil, BusSafar support");
  launcher.setAttribute("aria-expanded", "false");
  launcher.append(el("span", "sahil-avatar", "S"), el("span", "sahil-launch-txt", "Ask Sahil"));

  const panel = el("section", "sahil-panel");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Sahil, BusSafar support chat");
  panel.hidden = true;

  const head = el("div", "sahil-head");
  const who = el("div", "sahil-who");
  who.append(el("span", "sahil-avatar", "S"));
  const names = el("div");
  names.append(el("strong", null, "Sahil"), el("span", "sahil-sub", "BusSafar support"));
  who.append(names);
  const newBtn = el("button", "sahil-icon", "New chat");
  newBtn.type = "button";
  const close = el("button", "sahil-icon sahil-x", "×");
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
  sos.append(tel, " now and use the SOS button in the app.");

  const log = el("div", "sahil-log");
  log.setAttribute("aria-live", "polite");

  const gate = el("form", "sahil-gate");
  gate.append(el("p", null, "Enter the demo passcode to start chatting."));
  const pass = el("input");
  pass.type = "password";
  pass.placeholder = "Passcode";
  pass.setAttribute("aria-label", "Passcode");
  const passBtn = el("button", "btn btn-primary", "Start chat");
  passBtn.type = "submit";
  const gateErr = el("p", "sahil-err");
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
  [["Check booking", "I want to check my booking status"], ["Refund rules", "How much refund will I get if I cancel?"],
   ["Talk to a person", "I want to speak to a person."]].forEach(([label, q]) => {
    const b = el("button", "chip chip-sm", label);
    b.type = "button";
    b.addEventListener("click", () => ask(q));
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
    if (sources.length) b.append(el("span", "sahil-src", "From: " + sources.join(" · ")));
    row.append(b);
    log.append(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function bookingCard(bk) {
    const c = el("dl", "sahil-booking");
    [["PNR", bk.pnr], ["Route", bk.route], ["Date", bk.travel_date], ["Seat", bk.seat], ["Status", bk.status]]
      .forEach(([k, v]) => { if (v) { c.append(el("dt", null, k), el("dd", k === "Status" ? "st st-" + String(v).toLowerCase().replace(/[^a-z]/g, "") : null, v)); } });
    return c;
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
      store.set(session);
      if (!log.children.length) bubble("bot", d.greeting);
      showGate(false);
    }).finally(() => { starting = null; });
    return starting;
  }

  async function ask(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    if (!session) { pending = text; return showGate(true); }
    busy = true;
    send.disabled = true;
    bubble("me", text);
    const t = typing();
    try {
      const d = await post("/api/chat", { session, message: text, history: turns.slice(-12) });
      turns.push({ role: "user", text }, { role: "model", text: d.text });
      t.remove();
      bubble("bot", d.text, d);
      if (d.emergency) sos.hidden = false;
    } catch (err) {
      t.remove();
      if (err.status === 401) {          // chat expired: ask to start again, keep the question
        session = null; store.set(null); pending = text;
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
    passBtn.disabled = true;
    try {
      await start(pass.value);
      pass.value = "";
      const q = pending; pending = "";
      if (q) ask(q);
    } catch (err) {
      gateErr.textContent = err.status === 401 ? "That passcode isn't right." : err.message;
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
    session = null; store.set(null); log.textContent = ""; sos.hidden = true; turns = [];
    needsPasscode ? showGate(true) : start("").catch((e) => bubble("bot", e.message));
  });

  fetch("/api/health").then((r) => r.json()).then((h) => { needsPasscode = !!h.passcode_required; }).catch(() => {});

  window.Sahil = {
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
