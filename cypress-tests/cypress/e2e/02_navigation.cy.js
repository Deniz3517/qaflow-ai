/// <reference types="cypress" />

describe("02 Navigation — top-bar links route to the right pages", () => {
  beforeEach(() => cy.visit("/index.html"));

  it("Home link goes to /index.html and is marked active", () => {
    cy.get(".site-nav a").contains("Home").click();
    cy.location("pathname").should("eq", "/index.html");
    cy.get(".nav-link.active").should("contain.text", "Home");
  });

  it("Products link goes to /products.html", () => {
    cy.get(".site-nav a").contains("Products").click();
    cy.location("pathname").should("eq", "/products.html");
    cy.get(".nav-link.active").should("contain.text", "Products");
  });

  it("Cart link goes to /cart.html", () => {
    cy.get(".site-nav a").contains("Cart").click();
    cy.location("pathname").should("eq", "/cart.html");
    cy.get(".nav-link.active").should("contain.text", "Cart");
  });

  it("Login link goes to /login.html", () => {
    cy.get(".site-nav a").contains("Login").click();
    cy.location("pathname").should("eq", "/login.html");
    cy.get(".nav-link.active").should("contain.text", "Login");
  });

  it("nav-cta Login button has visible background colour", () => {
    cy.get(".nav-link.nav-cta").computedStyle("background-color")
      .should("not.eq", "rgba(0, 0, 0, 0)");
  });

  it("cart badge starts at 0 with empty localStorage", () => {
    cy.clearCart();
    cy.reload();
    cy.get("#cart-badge").should("have.text", "0");
  });
});
