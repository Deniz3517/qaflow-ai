/// <reference types="cypress" />
import { HomePage } from "../support/pages/HomePage";

describe("03 Home page — hero, search, featured & categories", () => {
  const home = new HomePage();
  beforeEach(() => home.visit());

  it("eyebrow tag reads NEW SEASON · 2026", () => {
    cy.get(".eyebrow").should("contain.text", "NEW SEASON");
    cy.get(".eyebrow").should("contain.text", "2026");
  });

  it('hero title says "Gear up for greatness."', () => {
    home.heroTitle().should("contain.text", "Gear up for greatness");
  });

  it("hero search input has a helpful placeholder", () => {
    home.searchInput().should("have.attr", "placeholder").and("match", /search/i);
  });

  it("typing into search retains the value", () => {
    home.searchInput().type("running shoes").should("have.value", "running shoes");
  });

  it("search button is rendered as a primary CTA", () => {
    home.searchButton().should("be.visible").and("have.class", "btn-primary");
  });

  it("Featured section renders exactly 4 product cards", () => {
    home.featuredCards().should("have.length", 4);
  });

  it('"View all →" link points to /products.html', () => {
    cy.get(".section-link").should("have.attr", "href", "/products.html");
  });

  it("Categories section title is Shop by category", () => {
    cy.contains(".section-title", "Shop by category").should("be.visible");
  });

  it("renders 5 category cards", () => {
    home.categoryCards().should("have.length", 5);
  });
});
