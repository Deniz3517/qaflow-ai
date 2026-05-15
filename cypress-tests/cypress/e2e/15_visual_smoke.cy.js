/// <reference types="cypress" />

/**
 * 15 Visual smoke — simple, focused checks for the QAFLOW AI demo.
 *
 * One assertion per test, each maps directly to a single visual or text
 * regression in the buggy-app. The auto-fix catalog has explicit entries
 * for every test in this file (except the cart-table one, which is the
 * "needs human" critical bug).
 */
describe("15 Visual smoke — high-signal one-liners", () => {
  it("Login button text reads 'Login'", () => {
    cy.visit("/login.html");
    cy.get("#login-button").should("have.text", "Login");
  });

  it("Login button is visible on the page", () => {
    cy.visit("/login.html");
    cy.get("#login-button").should("be.visible");
  });

  it("Home footer shows the current year (2026)", () => {
    cy.visit("/index.html");
    cy.get(".site-footer").should("contain.text", "2026");
  });

  it("Cart page renders the items table", () => {
    cy.visit("/cart.html");
    cy.get("#cart-body").should("exist");
  });
});
