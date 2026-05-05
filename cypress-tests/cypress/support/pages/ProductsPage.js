// Page object for the SportHub products listing page.
export class ProductsPage {
  visit(category) {
    cy.visit(category ? `/products.html?cat=${encodeURIComponent(category)}` : "/products.html");
  }

  grid()       { return cy.get("#grid"); }
  cards()      { return cy.get("#grid .product-card"); }
  catTitle()   { return cy.get("#cat-title"); }
  pills()      { return cy.get(".filter-pills .pill"); }
  pill(name)   { return cy.get(`.pill[data-cat="${name}"]`); }
  activePill() { return cy.get(".pill.active"); }

  filterBy(name) { this.pill(name).click(); }
  cardByName(name) {
    return cy.get(".product-card").contains(".product-name", name)
      .parents(".product-card");
  }
}
