/// <reference types="cypress" />

/**
 * 16 Visual smoke v2 — second wave of one-liner UI checks.
 *
 * Each test maps directly to a single visible regression. Three are paired
 * with auto-fix catalog entries; the fourth (products grid) is the
 * "needs human" critical bug that the AI deliberately can't fix on its own.
 */
describe("16 Visual smoke v2 — header, search, cart, products page", () => {
  it("Header brand reads SPORTHUB on home", () => {
    cy.visit("/index.html");
    cy.get(".app-title").should("have.text", "SPORTHUB");
  });

  it("Search button reads 'Search'", () => {
    cy.visit("/index.html");
    cy.get("#search-btn").should("have.text", "Search");
  });

  it("Cart navigation link contains the word 'Cart'", () => {
    cy.visit("/index.html");
    cy.get('.nav-link[href="/cart.html"]').should("contain.text", "Cart");
  });

  it("Products page renders the product grid container", () => {
    cy.visit("/products.html");
    cy.get("#grid").should("exist");
  });
});
