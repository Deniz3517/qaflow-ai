/// <reference types="cypress" />

describe("14 Performance — pages render under conservative budgets", () => {
  const BUDGETS = [
    { path: "/index.html",    budgetMs: 3000 },
    { path: "/products.html", budgetMs: 3000 },
    { path: "/cart.html",     budgetMs: 2000 },
    { path: "/login.html",    budgetMs: 2000 },
  ];

  BUDGETS.forEach(({ path, budgetMs }) => {
    it(`${path} reaches DOMContentLoaded under ${budgetMs}ms`, () => {
      cy.visit(path, {
        onBeforeLoad: (win) => {
          win.__qaflowStart = win.performance.now();
        },
      });
      cy.window().then((win) => {
        const elapsed = win.performance.now() - win.__qaflowStart;
        expect(elapsed, `${path} load time`).to.be.lessThan(budgetMs);
      });
    });
  });

  it("home page issues fewer than 6 same-origin requests", () => {
    let count = 0;
    cy.intercept({ url: "http://localhost:3001/**" }, () => { count += 1; });
    cy.visit("/index.html").then(() => {
      expect(count, "request count").to.be.lessThan(6);
    });
  });
});
