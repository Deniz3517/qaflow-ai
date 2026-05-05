/// <reference types="cypress" />
import { HomePage } from "../support/pages/HomePage";

describe("12 Category cards — names, links and click navigation", () => {
  const home = new HomePage();
  const NAMES = ["Running", "Football", "Basketball", "Fitness", "Apparel"];

  beforeEach(() => home.visit());

  it("renders exactly 5 category cards in a defined order", () => {
    home.categoryCards().should("have.length", 5);
    home.categoryCards().each(($card, i) => {
      expect($card.find(".cat-name").text().trim()).to.eq(NAMES[i]);
    });
  });

  NAMES.forEach((name) => {
    it(`category card "${name}" links to /products.html?cat=${name}`, () => {
      home.categoryCards().contains(name)
        .should("have.attr", "href", `/products.html?cat=${name}`);
    });
  });
});
