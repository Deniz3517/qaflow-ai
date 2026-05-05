/// <reference types="cypress" />
import { ProductsPage } from "../support/pages/ProductsPage";

describe("04 Products listing — grid, filters and category routing", () => {
  const page = new ProductsPage();

  it("default view shows all 12 products", () => {
    page.visit();
    page.cards().should("have.length", 12);
  });

  it("filtering by Running shows only running products", () => {
    page.visit("Running");
    page.cards().should("have.length", 2);
    page.cards().each(($el) => cy.wrap($el).find(".product-cat").should("have.text", "Running"));
  });

  it("filtering by Football shows only football products", () => {
    page.visit("Football");
    page.cards().should("have.length", 2);
  });

  it("filtering by Basketball shows only basketball products", () => {
    page.visit("Basketball");
    page.cards().should("have.length", 2);
  });

  it("filtering by Fitness shows three products", () => {
    page.visit("Fitness");
    page.cards().should("have.length", 3);
  });

  it("filtering by Apparel shows three products", () => {
    page.visit("Apparel");
    page.cards().should("have.length", 3);
  });

  it("category title reflects the active filter", () => {
    page.visit("Basketball");
    page.catTitle().should("have.text", "Basketball");
  });

  it("active filter pill is highlighted", () => {
    page.visit("Running");
    page.activePill().should("have.attr", "data-cat", "Running");
  });

  it("pill bar exposes 6 controls (All + 5 categories)", () => {
    page.visit();
    page.pills().should("have.length", 6);
  });

  it('"All" pill is active when no filter is set', () => {
    page.visit();
    page.activePill().should("have.attr", "data-cat", "all");
  });
});
