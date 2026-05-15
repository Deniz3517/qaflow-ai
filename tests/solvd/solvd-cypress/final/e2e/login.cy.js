/// <reference types="cypress" />
import { LoginSporthub } from '../pages/login_page.js';

describe('Login · SportHub — AI smoke', () => {
  const loginSporthub = new LoginSporthub();
  beforeEach(() => loginSporthub.visit());

  it('renders the expected document title', () => {
    loginSporthub.pageTitle().should('include', 'Login · SportHub');
  });

  it('SPORTHUB', () => {
    loginSporthub.sporthubHeading().should('contain.text', 'SPORTHUB');
  });

  it('Sign in to your account', () => {
    loginSporthub.sign_in_to_your_accountHeading().should('contain.text', 'Sign in to your account');
  });

  it('Login button is visible and labelled', () => {
    loginSporthub.loginButton().should('be.visible')
      .and('contain.text', 'Login');
  });

  it('login-form exposes the expected inputs', () => {
    loginSporthub.emailInput().should('exist');
    loginSporthub.passwordInput().should('exist');
  });

  it('major landmarks are present (a11y)', () => {
    cy.get('header').should('exist');
    cy.get('nav').should('exist');
    cy.get('main, section, footer').should('exist');
  });

});
