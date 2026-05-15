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
  matcher:        callable(title:str, spec:str) -> bool
  title:          str  — short human title for the fix
  type:           'Test' | 'Functional' | 'UI'
  severity:       'low' | 'medium' | 'high' | 'critical'
  target_repo:    'cypress-tests' | 'buggy-app'
  file:           relative path inside target_repo
  old:            literal substring to replace (must be unique in file)
  new:            replacement
  analysis:       explanation shown to the developer
  confidence:     0..100
  screenshot_url: page in the running buggy-app whose render captures the bug
                  visually — used for before/after screenshots in the UI.
                  May be omitted; only test-side fixes typically lack one.
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
    # ----------------------------------------------------------------- 4
    # Eyebrow text was reverted to last season's copy. App-side fix.
    {
        "matcher": lambda title, spec: (
            "03_home_page" in (spec or "")
            and "eyebrow tag" in title
        ),
        "title": "Hero eyebrow shows last season's copy",
        "type": "UI",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<span class="eyebrow">OLD STOCK · 2024</span>',
        "new": '<span class="eyebrow">NEW SEASON · 2026</span>',
        "analysis": (
            "Marketing rolled the home-page eyebrow back to the prior season's "
            "copy by accident. The smoke test pins the current campaign string "
            "so it caught the regression immediately. Restoring the 2026 copy "
            "matches the launch-page contract."
        ),
        "confidence": 98,
    },
    # ----------------------------------------------------------------- 5
    # Cart badge always renders "0" regardless of cart contents. App-side fix.
    {
        "matcher": lambda title, spec: (
            "06_cart_operations" in (spec or "")
            and "cart badge increments" in title
        ),
        "title": "Cart badge stuck at 0 — ignores item count",
        "type": "Functional",
        "severity": "high",
        "target_repo": "buggy-app",
        "file": "public/app.js",
        "old": '    el.textContent = "0";',
        "new": "    el.textContent = total;",
        "analysis": (
            "`renderCartBadge` writes a hard-coded \"0\" instead of the running "
            "total. The visibility toggle still uses `total > 0`, so the badge "
            "appears but never updates its number. Restoring `el.textContent = "
            "total` re-binds the badge to the live cart count."
        ),
        "confidence": 99,
    },
    # ----------------------------------------------------------------- 6
    # Featured-section "View all" link points to "#" instead of /products.html.
    {
        "matcher": lambda title, spec: (
            "03_home_page" in (spec or "")
            and "View all" in title
        ),
        "title": '"View all" link no longer points to /products.html',
        "type": "Functional",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<a href="#" class="section-link">View all →</a>',
        "new": '<a href="/products.html" class="section-link">View all →</a>',
        "analysis": (
            'The "View all →" CTA was changed to `href="#"` during a refactor, '
            "breaking the home → products navigation entry point. The Cypress "
            "spec checks the exact destination, so the change surfaces as a "
            "failed assertion instead of a silently dead link."
        ),
        "confidence": 99,
    },
    # ----------------------------------------------------------------- 7
    # 15 Visual smoke — login button text typo.
    {
        "matcher": lambda title, spec: (
            "15_visual_smoke" in (spec or "")
            and "Login button text" in title
        ),
        "title": "Login button text typo — 'Sign Inn' instead of 'Login'",
        "type": "UI",
        "severity": "high",
        "target_repo": "buggy-app",
        "file": "public/login.html",
        "old": '<button type="submit" id="login-button" class="login-button">Sign Inn</button>',
        "new": '<button type="submit" id="login-button" class="login-button">Login</button>',
        "analysis": (
            "The submit button label was changed to 'Sign Inn' (extra n). "
            "Marketing copy review pinned the button to read exactly 'Login'; "
            "restoring the original label resolves the visual regression."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/login.html",
    },
    # ----------------------------------------------------------------- 8
    # 15 Visual smoke — login button hidden by display:none.
    {
        "matcher": lambda title, spec: (
            "15_visual_smoke" in (spec or "")
            and "Login button is visible" in title
        ),
        "title": "Login button hidden by display:none",
        "type": "UI",
        "severity": "critical",
        "target_repo": "buggy-app",
        "file": "public/styles.css",
        "old": ".login-button {\n  display: none;\n",
        "new": ".login-button {\n  display: block;\n",
        "analysis": (
            "The .login-button rule was changed to `display: none`, which makes "
            "the submit button vanish from the login form entirely. Users have "
            "no way to sign in. Restoring `display: block` re-enables the CTA."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/login.html",
    },
    # ----------------------------------------------------------------- 9
    # 15 Visual smoke — footer year wrong.
    {
        "matcher": lambda title, spec: (
            "15_visual_smoke" in (spec or "")
            and "footer shows" in title
        ),
        "title": "Home footer year reads 2023 instead of 2026",
        "type": "UI",
        "severity": "low",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": "© 2023 SportHub · Demo store for QAFLOW AI",
        "new": "© 2026 SportHub · Demo store for QAFLOW AI",
        "analysis": (
            "The footer copyright string still references 2023 after the new "
            "year roll-over. It's a small text regression but visible across "
            "every page that renders the home footer template."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 10
    # 16 Visual smoke v2 — app title typo SPROTHUB.
    {
        "matcher": lambda title, spec: (
            "16_visual_smoke_v2" in (spec or "")
            and "Header brand reads SPORTHUB" in title
        ),
        "title": "App title typo — 'SPROTHUB' instead of 'SPORTHUB'",
        "type": "UI",
        "severity": "high",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<h1 class="app-title">SPROTHUB</h1>',
        "new": '<h1 class="app-title">SPORTHUB</h1>',
        "analysis": (
            "The brand wordmark in the home page header was typed as 'SPROTHUB'. "
            "It's a visible typo at the top of every screenshot. Restoring the "
            "canonical 'SPORTHUB' fixes the smoke test and the marketing copy."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 11
    # 16 Visual smoke v2 — search button labelled 'Find'.
    {
        "matcher": lambda title, spec: (
            "16_visual_smoke_v2" in (spec or "")
            and "Search button reads" in title
        ),
        "title": "Search button label changed to 'Find'",
        "type": "UI",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<button id="search-btn" class="btn btn-primary">Find</button>',
        "new": '<button id="search-btn" class="btn btn-primary">Search</button>',
        "analysis": (
            "Someone renamed the hero CTA from 'Search' to 'Find'. The product "
            "spec still pins the exact string, so the smoke test failed. "
            "Restoring 'Search' keeps the original copy contract."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 12
    # 16 Visual smoke v2 — cart nav link mislabeled 'Bag'.
    {
        "matcher": lambda title, spec: (
            "16_visual_smoke_v2" in (spec or "")
            and "Cart navigation link" in title
        ),
        "title": "Cart navigation link reads 'Bag' instead of 'Cart'",
        "type": "UI",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<a href="/cart.html" class="nav-link">Bag <span class="badge" id="cart-badge">0</span></a>',
        "new": '<a href="/cart.html" class="nav-link">Cart <span class="badge" id="cart-badge">0</span></a>',
        "analysis": (
            "The cart navigation link was renamed to 'Bag'. Tests reference the "
            "official 'Cart' label, and so does the help center. Reverting the "
            "label re-aligns the UI with the product vocabulary."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 13
    # 17 Visual smoke v3 — hero title typo 'greatnes.' missing one 's'.
    {
        "matcher": lambda title, spec: (
            "17_visual_smoke_v3" in (spec or "")
            and "Hero title reads" in title
        ),
        "title": "Hero title typo — 'greatnes.' missing one 's'",
        "type": "UI",
        "severity": "high",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<h2 class="hero-title">Gear up for greatnes.</h2>',
        "new": '<h2 class="hero-title">Gear up for greatness.</h2>',
        "analysis": (
            "The hero headline lost an 's' and now reads 'greatnes.' instead of "
            "'greatness.'. It's a single-character typo at the largest visual "
            "anchor of the home page. Restoring the canonical headline fixes it."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 14
    # 17 Visual smoke v3 — hero subtitle replaced with 'Shop now.'
    {
        "matcher": lambda title, spec: (
            "17_visual_smoke_v3" in (spec or "")
            and "Hero subtitle" in title
        ),
        "title": "Hero subtitle truncated to generic 'Shop now.' copy",
        "type": "UI",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<p class="hero-sub">Shop now.</p>',
        "new": '<p class="hero-sub">Premium running shoes, training gear and team apparel — engineered for athletes.</p>',
        "analysis": (
            "The hero subtitle was overwritten with the placeholder 'Shop now.', "
            "stripping the marketing positioning copy. The smoke test pins the "
            "word 'Premium', which catches the regression. Restoring the full "
            "tagline brings back the brand voice."
        ),
        "confidence": 98,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 15
    # 17 Visual smoke v3 — Featured section heading renamed to 'Hot Picks'.
    {
        "matcher": lambda title, spec: (
            "17_visual_smoke_v3" in (spec or "")
            and "Featured section heading" in title
        ),
        "title": "Featured section heading renamed to 'Hot Picks'",
        "type": "UI",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<h3 class="section-title">Hot Picks</h3>',
        "new": '<h3 class="section-title">Featured</h3>',
        "analysis": (
            "The Featured section heading was renamed to 'Hot Picks' without "
            "updating the content spec or the smoke tests. Reverting the label "
            "to 'Featured' restores parity with the rest of the product copy."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
    },
    # ----------------------------------------------------------------- 16
    # 17 Visual smoke v3 — Login nav CTA renamed 'Sign In'.
    {
        "matcher": lambda title, spec: (
            "17_visual_smoke_v3" in (spec or "")
            and "Login navigation CTA" in title
        ),
        "title": "Login navigation CTA renamed to 'Sign In'",
        "type": "UI",
        "severity": "medium",
        "target_repo": "buggy-app",
        "file": "public/index.html",
        "old": '<a href="/login.html" class="nav-link nav-cta">Sign In</a>',
        "new": '<a href="/login.html" class="nav-link nav-cta">Login</a>',
        "analysis": (
            "The login navigation CTA was changed to 'Sign In'. Existing analytics, "
            "documentation and the smoke spec all expect the label 'Login'. "
            "Restoring the original label keeps the funnel labels consistent."
        ),
        "confidence": 99,
        "screenshot_url": "http://localhost:3001/index.html",
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
