/// <reference types="cypress" />
import { LoginPage } from "../support/pages/LoginPage";

describe("08 Login validation — empty / invalid / valid submissions", () => {
  const page = new LoginPage();

  beforeEach(() => page.visit());

  it("submitting an empty form shows the missing-fields error", () => {
    page.submit();
    page.status().should("have.class", "error");
    page.status().should("contain.text", "fill in both");
  });

  it("submitting only an email keeps the form invalid", () => {
    page.fillForm("user@x.com").submit();
    page.status().should("have.class", "error");
  });

  it("submitting only a password keeps the form invalid", () => {
    page.fillForm(undefined, "p@ssword").submit();
    page.status().should("have.class", "error");
  });

  it("invalid credentials show the Invalid credentials error", () => {
    page.loginAs("nope@example.com", "wrong");
    page.status().should("contain.text", "Invalid credentials");
  });

  it("demo credentials show the success welcome", () => {
    page.loginAs("demo@sporthub.com", "demo1234");
    page.status().should("have.class", "success");
    page.status().should("contain.text", "Welcome");
  });

  it("status field is announced with role='status' and aria-live", () => {
    page.status().should("have.attr", "role", "status");
    page.status().should("have.attr", "aria-live", "polite");
  });
});
