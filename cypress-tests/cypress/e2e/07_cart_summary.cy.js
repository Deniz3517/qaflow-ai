/// <reference types="cypress" />
import { CartPage } from "../support/pages/CartPage";

describe("07 Cart summary — totals, shipping, formatting", () => {
  const cart = new CartPage();

  beforeEach(() => {
    cy.clearCart();
    cy.visit("/products.html");
  });

  function readMoney(text) {
    return Number(text.replace(/[^\d.]/g, ""));
  }

  it("shipping fee is shown as $9.99", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.shipping().should("have.text", "$9.99");
  });

  it("totals follow the format $X.XX", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.subtotal().invoke("text").should("match", /^\$\d+\.\d{2}$/);
    cart.total().invoke("text").should("match", /^\$\d+\.\d{2}$/);
  });

  it("total = subtotal + shipping when there is a single item", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.subtotal().invoke("text").then((subText) => {
      cart.total().invoke("text").then((totText) => {
        const sub = readMoney(subText);
        const tot = readMoney(totText);
        expect(tot).to.equal(Number((sub + 9.99).toFixed(2)));
      });
    });
  });

  it("subtotal scales with quantity (3× should be 3× single price)", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.subtotal().invoke("text").then((singleText) => {
      const single = readMoney(singleText);
      cart.increaseQty(0);
      cart.increaseQty(0);
      cart.subtotal().invoke("text").should((tripleText) => {
        const triple = readMoney(tripleText);
        expect(triple, "subtotal should be 3× single price").to.be.closeTo(single * 3, 0.01);
      });
    });
  });

  it("checkout shows the demo-only status banner", () => {
    cy.addNthProductToCart(0);
    cart.visit();
    cart.checkout();
    cart.status().should("contain.text", "Demo only");
  });
});
