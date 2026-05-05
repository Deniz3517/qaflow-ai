// Page object for the SportHub login page.
export class LoginPage {
  visit() { cy.visit("/login.html"); }

  card()         { return cy.get("#login-card"); }
  cardTitle()    { return cy.get(".card-title"); }
  emailInput()   { return cy.get("#email"); }
  passwordInput(){ return cy.get("#password"); }
  submitBtn()    { return cy.get("#login-button"); }
  status()       { return cy.get("#status"); }
  brandTitle()   { return cy.get(".app-title"); }

  fillForm(email, password) {
    if (email    !== undefined) this.emailInput().clear().type(email);
    if (password !== undefined) this.passwordInput().clear().type(password);
    return this;
  }
  submit() { this.submitBtn().click(); }
  loginAs(email, password) { this.fillForm(email, password); this.submit(); }
}
