// Page object for the SportHub cart page.
export class CartPage {
  visit() { cy.visit("/cart.html"); }

  empty()       { return cy.get("#cart-empty"); }
  content()     { return cy.get("#cart-content"); }
  rows()        { return cy.get("#cart-body tr"); }
  rowByIndex(i) { return cy.get("#cart-body tr").eq(i); }

  subtotal()    { return cy.get("#sub"); }
  shipping()    { return cy.get("#ship"); }
  total()       { return cy.get("#total"); }
  status()      { return cy.get("#status"); }
  checkoutBtn() { return cy.get("#checkout-btn"); }
  cartBadge()   { return cy.get("#cart-badge"); }

  increaseQty(rowIdx = 0) { this.rowByIndex(rowIdx).find('.qty-btn[data-act="inc"]').click(); }
  decreaseQty(rowIdx = 0) { this.rowByIndex(rowIdx).find('.qty-btn[data-act="dec"]').click(); }
  removeItem(rowIdx = 0)  { this.rowByIndex(rowIdx).find(".qty-remove").click(); }
  checkout() { this.checkoutBtn().click(); }
}
