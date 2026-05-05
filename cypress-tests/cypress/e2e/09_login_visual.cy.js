/// <reference types="cypress" />
import { LoginPage } from "../support/pages/LoginPage";

/**
 * These tests intentionally fail against the seeded buggy SportHub —
 * each maps directly to a bug in the AI auto-fix catalog.
 *
 *   #3492  login-button must be horizontally centered
 *   #3493  email input must declare type="email"
 *   #3494  status.error colour must meet WCAG AA contrast
 *   #3495  .app-title must be center-aligned in the brand bar
 */

function relLuminance(rgb) {
  const m = String(rgb).match(/(\d+(?:\.\d+)?)/g);
  if (!m || m.length < 3) return 0;
  const [r, g, b] = m.slice(0, 3).map(Number).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(fg, bg) {
  const a = relLuminance(fg);
  const b = relLuminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// Spec disabled by default — runs alone hang on macOS / Apple Silicon Cypress 13.6.6.
// The same bug detection runs in the Playwright AI loop (backend/test_runner.py)
// which feeds the auto-fix demo. Re-enable once a fix is found.
describe.skip("09 Login visual & semantics — catches the seeded UI bugs", () => {
  const page = new LoginPage();
  beforeEach(() => page.visit());

  it("[#3492] Login button is horizontally centered within the card", () => {
    page.submitBtn().then(($btn) => {
      const child = $btn[0].getBoundingClientRect();
      const parent = $btn[0].parentElement.getBoundingClientRect();
      const offset = (child.left + child.width / 2) - (parent.left + parent.width / 2);
      expect(Math.abs(offset), `button horizontal offset (px)`).to.be.lessThan(20);
    });
  });

  it("[#3493] Email input declares type='email' for native validation", () => {
    page.emailInput().invoke("attr", "type").should("eq", "email");
  });

  it("[#3494] status.error has WCAG AA (>=4.5:1) contrast over card background", () => {
    page.loginAs("x", "y");                 // triggers .status.error
    page.status().should("have.class", "error");
    cy.window().then((win) => {
      const fg = win.getComputedStyle(win.document.getElementById("status")).color;
      const bg = win.getComputedStyle(win.document.getElementById("login-card")).backgroundColor;
      expect(contrast(fg, bg), `${fg} on ${bg} contrast`).to.be.greaterThan(4.5);
    });
  });

  it("[#3495] Brand .app-title is center-aligned inside the header", () => {
    page.brandTitle().then(($el) => {
      const ta = window.getComputedStyle($el[0]).textAlign;
      expect(ta, ".app-title text-align").to.equal("center");
    });
  });

  it("Login button is clickable and not disabled", () => {
    page.submitBtn().should("be.visible").and("not.be.disabled");
  });
});
