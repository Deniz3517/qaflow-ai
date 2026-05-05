/// <reference types="cypress" />

describe("13 Responsive — layout adapts to common viewports", () => {
  const VIEWPORTS = [
    { name: "mobile",  w: 375,  h: 667  },
    { name: "tablet",  w: 768,  h: 1024 },
    { name: "desktop", w: 1280, h: 800  },
  ];

  VIEWPORTS.forEach(({ name, w, h }) => {
    it(`brand bar visible at ${name} (${w}×${h})`, () => {
      cy.viewport(w, h);
      cy.visit("/index.html");
      cy.get(".app-title").should("be.visible");
    });

    it(`hero search controls render at ${name} (${w}×${h})`, () => {
      cy.viewport(w, h);
      cy.visit("/index.html");
      cy.get("#search").should("be.visible");
      cy.get("#search-btn").should("be.visible");
    });
  });

  it("desktop shows multiple product columns", () => {
    cy.viewport(1280, 800);
    cy.visit("/products.html");
    cy.get(".product-card").then(($cards) => {
      const tops = new Set([...$cards].map((c) => Math.round(c.getBoundingClientRect().top / 5) * 5));
      expect(tops.size, "multiple rows of cards").to.be.lessThan($cards.length);
    });
  });
});
