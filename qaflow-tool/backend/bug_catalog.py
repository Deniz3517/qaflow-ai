"""Known bug definitions for the demo buggy-app.

Each entry describes a single intentional bug seeded into the demo site
along with a precise fix that the AI engine (mock mode) can apply.
"""

BUG_CATALOG = {
    3492: {
        "title": "Login button misaligned",
        "type": "UI",
        "severity": "medium",
        "file": "public/styles.css",
        "analysis": (
            "The `.login-button` rule applies `margin-left: 12px;` which "
            "offsets the block-level button from the centered form layout. "
            "On narrow viewports the button visually drifts left and breaks "
            "the column rhythm. Replacing with `margin: 0 auto;` centers the "
            "fixed-width button inside its flex parent."
        ),
        "old": "  margin-left: 12px;\n  width: 160px;",
        "new": "  margin: 0 auto;\n  width: 160px;",
        "confidence": 94,
    },
    3493: {
        "title": "Email input missing type validation",
        "type": "Functional",
        "severity": "high",
        "file": "public/login.html",
        "analysis": (
            "The email field has no `type=\"email\"` attribute, so the "
            "browser performs no native format validation. Users can submit "
            "obviously malformed values (e.g. `abc`) and reach the server "
            "before any check fires. Adding the correct input type enables "
            "HTML5 validation and the right mobile keyboard."
        ),
        "old": '<input id="email" name="email" placeholder="you@example.com" required />',
        "new": '<input id="email" name="email" type="email" placeholder="you@example.com" required />',
        "confidence": 98,
    },
    3494: {
        "title": "Error message has poor contrast",
        "type": "Accessibility",
        "severity": "medium",
        "file": "public/styles.css",
        "analysis": (
            "`.status.error` uses `color: #475569` (slate-600) over a dark "
            "navy background. WCAG 2.1 AA requires a 4.5:1 contrast ratio "
            "for body text — measured ratio here is ~2.1:1. Switching the "
            "color to `#ef4444` (red-500) raises the ratio to ~5.3:1 and "
            "communicates error semantics."
        ),
        "old": ".status.error {\n  color: #475569;\n}",
        "new": ".status.error {\n  color: #ef4444;\n}",
        "confidence": 96,
    },
    3495: {
        "title": "App title not centered in header",
        "type": "UI",
        "severity": "low",
        "file": "public/styles.css",
        "analysis": (
            "The `.app-title` rule has no `text-align` declaration and "
            "defaults to left alignment. The brand bar is intended to "
            "display the title centered. Adding `text-align: center;` to "
            "the rule corrects the layout without affecting any other "
            "header elements."
        ),
        "old": ".app-title {\n  font-size: 22px;\n  font-weight: 700;\n  margin: 0;\n  color: #38bdf8;\n  letter-spacing: 1px;\n}",
        "new": ".app-title {\n  font-size: 22px;\n  font-weight: 700;\n  margin: 0;\n  color: #38bdf8;\n  letter-spacing: 1px;\n  text-align: center;\n}",
        "confidence": 99,
    },
}
