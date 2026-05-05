/// <reference types="cypress" />

describe("01 Smoke — every page loads and renders the brand", () => {
  const PAGES = [
    { name: "home",     path: "/index.html" },
    { name: "products", path: "/products.html" },
    { name: "cart",     path: "/cart.html" },
    { name: "login",    path: "/login.html" },
  ];

  PAGES.forEach(({ name, path }) => {
    it(`responds 200 and renders <title> on ${name}`, () => {
      cy.request(path).its("status").should("eq", 200);
      cy.visit(path);
      cy.title().should("match", /SportHub|Login/i);
    });

    it(`shows the SPORTHUB brand bar on ${name}`, () => {
      cy.visit(path);
      cy.get(".app-title").should("be.visible").and("contain.text", "SPORTHUB");
    });
  });

  it("loads the global stylesheet (network)", () => {
    cy.request("/styles.css").then((res) => {
      expect(res.status).to.eq(200);
      expect(res.headers["content-type"]).to.match(/text\/css/);
    });
  });

  it("loads the application script (network)", () => {
    cy.request("/app.js").then((res) => {
      expect(res.status).to.eq(200);
      expect(res.body).to.contain("SH_PRODUCTS");
    });
  });
});
