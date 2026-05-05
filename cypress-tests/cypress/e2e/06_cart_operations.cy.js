/// <reference types="cypress" />
import { CartPage } from "../support/pages/CartPage";

describe("06 Cart operations — add, increment, decrement, remove", () => {
  const cart = new CartPage();

  beforeEach(() => {
    cy.clearCart();
    cy.visit("/products.html");
  });

  it("empty cart shows the empty-state hero", () => {
    cart.visit();
    cart.empty().should("be.visible");
    cart.empty().should("contain.text", "empty");
  });

  it("adding a product navigates to a non-empty cart", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.empty().should("not.be.visible");
    cart.rows().should("have.length", 1);
  });

  it("cart badge increments after adding an item", () => {
    cy.get("#cart-badge").should("have.text", "0");
    cy.addNthProductToCart(0);
    cy.get("#cart-badge").should("have.text", "1");
  });

  it("adding the same product twice keeps a single row, qty=2", () => {
    cy.addNthProductToCart(0);
    cy.addNthProductToCart(0);
    cart.visit();
    cart.rows().should("have.length", 1);
    cart.rows().eq(0).find(".qty-control span").should("have.text", "2");
  });

  it("increase quantity button increments the visible qty", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.increaseQty(0);
    cart.rowByIndex(0).find(".qty-control span").should("have.text", "2");
  });

  it("decrease quantity button decrements the visible qty", () => {
    cy.addNthProductToCart(0);
    cy.addNthProductToCart(0);
    cart.visit();
    cart.decreaseQty(0);
    cart.rowByIndex(0).find(".qty-control span").should("have.text", "1");
  });

  it("remove button drops the row and the badge resets", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.removeItem(0);
    cart.empty().should("be.visible");
    cy.get("#cart-badge").should("have.text", "0");
  });

  it("removing only item shows the empty state again", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.removeItem(0);
    cart.empty().should("contain.text", "empty");
  });
});
