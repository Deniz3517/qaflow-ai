/// <reference types="cypress" />

/**
 * 17 Visual smoke v3 — third wave of one-liner UI checks.
 *
 * Each test maps directly to a single visible regression on the home page.
 * All four are paired with auto-fix catalog entries so the AI can resolve
 * them automatically and route them to the developer for approval.
 */
describe("17 Visual smoke v3 — hero, featured, nav copy", () => {
  beforeEach(() => cy.visit("/index.html"));

  it("Hero title reads 'Gear up for greatness.'", () => {
    cy.get(".hero-title").should("have.text", "Gear up for greatness.");
  });

  it("Hero subtitle still mentions Premium", () => {
    cy.get(".hero-sub").should("contain.text", "Premium");
  });

  it("Featured section heading reads 'Featured'", () => {
    cy.contains(".section-title", "Featured").should("be.visible");
  });

  it("Login navigation CTA reads 'Login'", () => {
    cy.get('.nav-link[href="/login.html"]').should("have.text", "Login");
  });
});
