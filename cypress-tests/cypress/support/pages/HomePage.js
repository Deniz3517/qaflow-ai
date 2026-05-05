// Page object for the SportHub home page.
// NOTE: methods that invoke cy commands return undefined to satisfy Cypress'
// "no value from a queued command" rule. Chaining is achieved by sequential calls.
export class HomePage {
  visit() { cy.visit("/index.html"); }

  // Locators
  hero()           { return cy.get(".hero"); }
  heroTitle()      { return cy.get(".hero-title"); }
  heroSubtitle()   { return cy.get(".hero-sub"); }
  searchInput()    { return cy.get("#search"); }
  searchButton()   { return cy.get("#search-btn"); }
  featuredGrid()   { return cy.get("#featured-grid"); }
  featuredCards()  { return cy.get("#featured-grid .product-card"); }
  categoriesGrid() { return cy.get(".cat-grid"); }
  categoryCards()  { return cy.get(".cat-card"); }
  brandTitle()     { return cy.get(".app-title"); }

  // Actions
  search(term) {
    this.searchInput().clear().type(term);
    this.searchButton().click();
  }
  openCategory(name) {
    this.categoryCards().contains(name).click();
  }
}
