// Website chat checks in a real browser (WEB-018 to WEB-020). No Gemini calls.
// 1. Start the site with no key and no passcode:
//      GOOGLE_API_KEY= APP_PASSCODE= TICKETS_PATH=/tmp/qa_tickets.csv uvicorn web.server:app --port 8765
// 2. node tests/qa/web_browser_check.js      (needs Playwright + Chromium)
// 3. python tests/qa/run_qa.py --only WEB   (folds the result into the QA report)
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.SITE || "http://localhost:8765";
const OUT = path.join(__dirname, "web_browser_result.json");
const LAST4 = Object.fromEntries(
  fs.readFileSync(path.join(__dirname, "..", "..", "data", "bookings.csv"), "utf8")
    .trim().split(/\r?\n/).slice(1).map((l) => l.split(",")).map((c) => [c[0], c[2]])
);

async function run(name, viewport, fn) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto(BASE);
    await page.waitForTimeout(600);
    const actual = await fn(page);
    return { ok: errors.length === 0 && actual.ok, actual: actual.note + (errors.length ? ` | JS errors: ${errors}` : "") };
  } catch (e) {
    return { ok: false, actual: `${name}: ${e.message}` };
  } finally { await browser.close(); }
}

async function bookingForm(page) {
  await page.click('button.chip[data-form="booking"]');
  const card = page.locator(".sahil-card").first();
  await card.locator("input").nth(0).fill("bs 597452");
  await card.locator("input").nth(1).fill(LAST4.BS597452);
  await card.locator('button[type="submit"]').click();
  await page.waitForSelector(".sahil-booking, .sahil-bubble dl", { timeout: 8000 });
  const log = await page.textContent(".sahil-log");
  const ok = log.includes("PNR BS597452 · phone ending ••••") && !log.includes(LAST4.BS597452);
  return { ok, note: `Bubble shows 'PNR BS597452 · phone ending ••••'; booking card shown; digits not on screen: ${ok}` };
}

const results = [];
(async () => {
  const b = await run("booking", { width: 1280, height: 860 }, bookingForm);
  results.push({ id: "WEB-018", scenario: "Booking form in the browser", data: "BS597452 + ****",
    expected: "Fields accept 'bs 597452', digits are masked, booking card appears", severity: "High",
    steps: "Tap Check my booking, fill PNR and last 4 digits, Check status", status: b.ok ? "Pass" : "Fail", actual: b.actual });

  const c = await run("contact", { width: 1280, height: 860 }, async (page) => {
    await page.click(".sahil-launch");
    await page.click('.sahil-panel button:has-text("Talk to a person")');
    const card = page.locator(".sahil-card").last();
    await card.locator('button[type="submit"]').click();
    const err1 = await card.locator(".sahil-err").textContent();
    await card.locator("select").selectOption("lost_item");
    await card.locator("textarea").fill("I left my black backpack on the bus");
    await card.locator("input").nth(0).fill("BS597452");
    await card.locator("input").nth(1).fill("0000");
    await card.locator('button[type="submit"]').click();
    await page.waitForTimeout(800);
    const err2 = await card.locator(".sahil-err").textContent();
    await card.locator("input").nth(0).fill("bs 597452");
    await card.locator("input").nth(1).fill(LAST4.BS597452);
    await card.locator('button[type="submit"]').click();
    await page.waitForSelector("text=/TKT-\\d+/", { timeout: 8000 });
    const ok = /choose an issue type/i.test(err1) && /couldn't find a booking that matches/i.test(err2);
    return { ok, note: `Empty form says '${err1}'; wrong digits say '${err2}'; with a real PNR a ticket ID is shown` };
  });
  results.push({ id: "WEB-019", scenario: "Contact form in the browser", data: "Lost item + description",
    expected: "Must pick an issue type and a PNR + phone digits that match; ticket ID shown after Create ticket", severity: "High",
    steps: "Tap Talk to a person, submit empty, then fill and submit", status: c.ok ? "Pass" : "Fail", actual: c.actual });

  const m = await run("mobile", { width: 375, height: 812 }, async (page) => {
    await page.click('button.chip[data-form="ticket_status"]');
    const card = page.locator(".sahil-card").first();
    const box = await card.boundingBox();
    const scroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    await card.locator("input").fill("TKT-9999");
    await card.locator('button[type="submit"]').click();
    await page.waitForTimeout(800);
    const unknown = await card.locator(".sahil-err").textContent();
    await card.locator('button:has-text("Cancel")').click();
    const gone = (await page.locator(".sahil-card").count()) === 0;
    const ok = box && box.x >= 0 && box.x + box.width <= 375 && scroll && gone && /no ticket TKT-9999/.test(unknown);
    return { ok, note: `Ticket form fits 375px (x=${box && Math.round(box.x)}, w=${box && Math.round(box.width)}); no sideways scroll: ${scroll}; unknown ID says '${unknown}'; Cancel removes it: ${gone}` };
  });
  results.push({ id: "WEB-020", scenario: "Forms on a phone (375x812)", data: "375x812 viewport",
    expected: "Form fits the screen; unknown ticket ID refused; Cancel closes it", severity: "Medium",
    steps: "Open the Ticket ID form at phone size, then Cancel", status: m.ok ? "Pass" : "Fail", actual: m.actual });

  fs.writeFileSync(OUT, JSON.stringify(results, null, 1));
  results.forEach((r) => console.log(r.id, r.status, "-", r.actual));
})();
