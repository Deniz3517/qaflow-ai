// Custom Cypress commands. Keeps spec files declarative.

/**
 * Navigates to a SportHub page by its short name.
 * @param {"home"|"products"|"cart"|"login"} name
 */
Cypress.Commands.add("visitPage", (name) => {
  const map = {
    home:     "/index.html",
    products: "/products.html",
    cart:     "/cart.html",
    login:    "/login.html",
  };
  if (!map[name]) throw new Error(`unknown page: ${name}`);
  cy.visit(map[name]);
});

/** Add the Nth product card on the current page (0-indexed) to the cart. */
Cypress.Commands.add("addNthProductToCart", (n = 0) => {
  cy.get(".product-card").eq(n).find(".btn-add").click();
});

/** Reset the in-memory localStorage cart between tests. */
Cypress.Commands.add("clearCart", () => {
  cy.window().then((win) => win.localStorage.removeItem("sh_cart_v1"));
});

/** Submit the login form with given credentials. */
Cypress.Commands.add("login", (email, password) => {
  if (email !== undefined) cy.get("#email").clear().type(email);
  if (password !== undefined) cy.get("#password").clear().type(password);
  cy.get("#login-button").click();
});

/** Read computed style of a selector, return as a Promise. */
Cypress.Commands.add("computedStyle", { prevSubject: "element" }, (subject, prop) => {
  return cy.window().then((win) => win.getComputedStyle(subject[0]).getPropertyValue(prop));
});

/** Convenience assertion: element is horizontally centered within its parent. */
Cypress.Commands.add(
  "shouldBeHorizontallyCentered",
  { prevSubject: "element" },
  (subject, tolerance = 8) => {
    const child = subject[0].getBoundingClientRect();
    const parent = subject[0].parentElement.getBoundingClientRect();
    const childCenter = child.left + child.width / 2;
    const parentCenter = parent.left + parent.width / 2;
    expect(
      Math.abs(childCenter - parentCenter),
      `centered within ${tolerance}px (got offset ${Math.round(childCenter - parentCenter)}px)`,
    ).to.be.lessThan(tolerance);
  },
);

/** Compute relative luminance (sRGB) for a CSS rgb()/rgba() color string. */
function _relLuminance(rgb) {
  const m = rgb.match(/(\d+(?:\.\d+)?)/g);
  if (!m || m.length < 3) return 0;
  const [r, g, b] = m.slice(0, 3).map(Number).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Compute WCAG contrast ratio between fg and bg colour strings. */
Cypress.Commands.add("contrastRatio", (fg, bg) => {
  const l1 = _relLuminance(fg);
  const l2 = _relLuminance(bg);
  const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  return cy.wrap(ratio);
});
