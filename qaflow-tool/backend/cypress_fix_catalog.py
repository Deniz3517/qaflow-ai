"""Mock-mode auto-fix catalog for known Cypress failures.

Each entry says: 'when a Cypress bug looks like X, the fix is Y at file Z'.

This stands in for a real Claude API call: the AI engine looks up matching
entries and produces a deterministic patch. When `ANTHROPIC_API_KEY` is
configured later, the lookup falls back to the live LLM.

Each fix targets one of two repos:
  - 'cypress-tests' (lives inside the qaflow-ai monorepo)
  - 'buggy-app'      (its own repo with own remote)

The runner uses `target_repo` to decide where to commit + push.
"""

from __future__ import annotations

from typing import Callable


CypressFixEntry = dict
"""
{
  matcher:     callable(title:str, spec:str) -> bool
  title:       str  — short human title for the fix
  type:        'Test' | 'Functional' | 'UI'
  severity:    'low' | 'medium' | 'high' | 'critical'
  target_repo: 'cypress-tests' | 'buggy-app'
  file:        relative path inside target_repo
  old:         literal substring to replace (must be unique in file)
  new:         replacement
  analysis:    explanation shown to the developer
  confidence:  0..100
}
"""


CATALOG: list[CypressFixEntry] = [
    # ----------------------------------------------------------------- 1
    # 5 category-card link tests fail because the test does
    #   home.categoryCards().contains(name).should("have.attr", "href", ...)
    # `.contains(name)` matches the inner <span class="cat-name">, not the <a>.
    # Fix the test by walking up to the .cat-card anchor.
    {
        "matcher": lambda title, spec: (
            "12_categories" in (spec or "")
            and "links to /products.html?cat=" in title
        ),
        "title": "Selector returns inner <span> instead of the <a> anchor",
        "type": "Test",
        "severity": "low",
        "target_repo": "cypress-tests",
        "file": "cypress/e2e/12_categories.cy.js",
        "old": (
            '      home.categoryCards().contains(name)\n'
            '        .should("have.attr", "href", `/products.html?cat=${name}`);\n'
        ),
        "new": (
            '      home.categoryCards().contains(name).parents(".cat-card")\n'
            '        .should("have.attr", "href", `/products.html?cat=${name}`);\n'
        ),
        "analysis": (
            "Cypress' `.contains(text)` returns the deepest element holding that "
            "text, which here is `<span class=\"cat-name\">`. The href attribute "
            "lives on the parent `<a class=\"cat-card\">`. Walking up with "
            "`.parents(\".cat-card\")` resolves the assertion target to the anchor."
        ),
        "confidence": 96,
    },
    # ----------------------------------------------------------------- 2
    # 2 login-validation tests crash with TypeError because page.fillForm(...)
    # is chained with .submit() but fillForm returns undefined. Fix the page
    # object to return `this`.
    {
        "matcher": lambda title, spec: (
            "08_login_validation" in (spec or "")
            and "keeps the form invalid" in title
        ),
        "title": "LoginPage.fillForm doesn't return `this` — chain breaks",
        "type": "Test",
        "severity": "low",
        "target_repo": "cypress-tests",
        "file": "cypress/support/pages/LoginPage.js",
        "old": (
            "  fillForm(email, password) {\n"
            "    if (email    !== undefined) this.emailInput().clear().type(email);\n"
            "    if (password !== undefined) this.passwordInput().clear().type(password);\n"
            "  }\n"
        ),
        "new": (
            "  fillForm(email, password) {\n"
            "    if (email    !== undefined) this.emailInput().clear().type(email);\n"
            "    if (password !== undefined) this.passwordInput().clear().type(password);\n"
            "    return this;\n"
            "  }\n"
        ),
        "analysis": (
            "The test uses `page.fillForm(...).submit()`. Because `fillForm` "
            "has no `return` statement it yields `undefined`, so `.submit()` "
            "blows up with TypeError. Returning `this` re-enables the fluent "
            "API and unblocks every test that chains after fillForm."
        ),
        "confidence": 99,
    },
    # ----------------------------------------------------------------- 3
    # The cart subtotal test fails because the app sums prices without
    # multiplying by quantity. This is an APP bug, not a test bug.
    {
        "matcher": lambda title, spec: (
            "07_cart_summary" in (spec or "")
            and "subtotal scales with quantity" in title
        ),
        "title": "Cart subtotal ignores item quantity",
        "type": "Functional",
        "severity": "high",
        "target_repo": "buggy-app",
        "file": "public/app.js",
        "old": (
            "      // BUG (manual): subtotal does not multiply by quantity — manual tester to report\n"
            "      const lineTotal = p.price;\n"
        ),
        "new": (
            "      const lineTotal = p.price * it.qty;\n"
        ),
        "analysis": (
            "The cart line-total accumulates only the unit price, not "
            "price × quantity. The Cypress test adds 3 of the same item and "
            "expects subtotal == 3 × unit price. Multiplying by `it.qty` "
            "matches the contract every shopper expects."
        ),
        "confidence": 99,
    },
]


def find_fix(title: str, spec: str | None) -> CypressFixEntry | None:
    """Return the first catalog entry whose matcher accepts (title, spec)."""
    for entry in CATALOG:
        try:
            if entry["matcher"](title, spec or ""):
                return entry
        except Exception:
            continue
    return None
