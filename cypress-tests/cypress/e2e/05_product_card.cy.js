/// <reference types="cypress" />

describe("05 Product card anatomy — every card has the required parts", () => {
  beforeEach(() => cy.visit("/products.html"));

  it("each card exposes a category label", () => {
    cy.get(".product-card .product-cat").should("have.length.at.least", 12);
  });

  it("each card has a non-empty product name", () => {
    cy.get(".product-card .product-name").each(($el) => {
      expect($el.text().trim().length).to.be.greaterThan(0);
    });
  });

  it("prices render in $XX.XX format", () => {
    cy.get(".product-card .product-price").each(($el) => {
      expect($el.text()).to.match(/^\$\d+\.\d{2}$/);
    });
  });

  it("each card has an Add to cart button", () => {
    cy.get(".product-card .btn-add").should("have.length.at.least", 12);
  });

  it("each card surfaces a product emoji icon", () => {
    cy.get(".product-card .product-emoji").should("have.length.at.least", 12);
  });

  it("Add to cart buttons carry the data-id attribute", () => {
    cy.get(".product-card .btn-add").first().should("have.attr", "data-id").and("match", /^\d+$/);
  });

  it("clicking Add to cart triggers a toast", () => {
    cy.clearCart();
    cy.reload();
    cy.get(".product-card .btn-add").first().click();
    cy.get(".toast").should("be.visible").and("contain.text", "Added");
  });
});
