/// <reference types="cypress" />

describe("11 Search field — basic interactions", () => {
  beforeEach(() => cy.visit("/index.html"));

  it("search input is visible in the hero", () => {
    cy.get("#search").should("be.visible");
  });

  it("placeholder hints at categories the user can search", () => {
    cy.get("#search").should("have.attr", "placeholder").and("match", /shoes|balls|apparel/i);
  });

  it("search input accepts keystrokes", () => {
    cy.get("#search").type("running").should("have.value", "running");
  });

  it("search button has type='button' or default behaviour and is enabled", () => {
    cy.get("#search-btn").should("not.be.disabled");
  });
});
