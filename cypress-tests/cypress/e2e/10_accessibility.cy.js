/// <reference types="cypress" />

describe("10 Accessibility — basic semantic and ARIA checks", () => {
  it("login form: every input has a programmatically associated label", () => {
    cy.visit("/login.html");
    cy.get("form input").each(($input) => {
      const id = $input.attr("id");
      expect(id, "input has an id").to.exist;
      cy.get(`label[for="${id}"]`).should("exist");
    });
  });

  it("login form has noValidate (relies on JS validation)", () => {
    cy.visit("/login.html");
    cy.get("#login-form").should("have.attr", "novalidate");
  });

  it("login submit button declares type='submit'", () => {
    cy.visit("/login.html");
    cy.get("#login-button").should("have.attr", "type", "submit");
  });

  it("status region is announced via aria-live=polite", () => {
    cy.visit("/login.html");
    cy.get("#status").should("have.attr", "aria-live", "polite");
  });

  it("page has lang='en' attribute on <html>", () => {
    cy.visit("/index.html");
    cy.get("html").should("have.attr", "lang", "en");
  });

  it("title is descriptive (mentions SportHub)", () => {
    cy.visit("/index.html");
    cy.title().should("match", /SportHub/);
  });

  it("category cards expose accessible link text", () => {
    cy.visit("/index.html");
    cy.get(".cat-card").each(($a) => {
      expect($a.text().trim().length, "non-empty link text").to.be.greaterThan(2);
    });
  });
});
