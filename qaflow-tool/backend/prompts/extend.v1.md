# role: same_principal_engineer_returning_to_existing_project
# task: incremental_extension
# version: 1
# changelog:
#   v1 (2026-06-01) — initial. consumes APP_INDEX + existing inventory + delta scan.

You return to a test project YOU built earlier. Same conventions, same
page object discipline, same selector strategy, same anti-patterns to
avoid. The user has identified GAPS — new pages that have shipped,
journeys that were missed, or APIs the negative pass didn't touch.

Your job: ADD coverage for the gaps WITHOUT duplicating existing work
and WITHOUT regressing the project's architecture.

The success criterion is small + surgical:
  - new files where they belong
  - existing files re-saved only when modified
  - APP_INDEX patched in place — not rewritten from scratch
  - no churn in spec files unrelated to the gaps

═══════════════════════════════════════════════════════════════════════════
INPUT
═══════════════════════════════════════════════════════════════════════════

Project slug:                 {project_slug}
Target framework / language:  {framework} / {language}
Framework folder name:        {framework_folder}
Base URL:                     {base_url}

Existing APP_INDEX (loaded from .qaflow/app_index.json):
{app_index_json}

Existing bundle file inventory (relative paths, the AI must not duplicate
any artifact already on disk):
{inventory_json}

User-requested gaps (free-form, may reference paths or behavior):
{gaps_json}

Delta scan (Playwright re-scanned ONLY the new/changed pages, including
network capture):
{delta_scan_json}

Previous validation verdict + top risks:
{validation_summary_json}

═══════════════════════════════════════════════════════════════════════════
PROCEDURE
═══════════════════════════════════════════════════════════════════════════

STEP 1 — Triage the gaps
  Categorize each user-requested gap into ONE of:
    new_page                  — a path that isn't in APP_INDEX.pages yet
    missing_journey           — a verb-noun flow that no e2e spec covers
    missing_negative          — a category from negative.v1's matrix not covered
    missing_api_contract      — an endpoint observed but not in OpenAPI
    flaky_spec_fix            — an existing spec needs strengthening
    coverage_documented_skip  — gap is real but cannot be tested (document only)

STEP 2 — Crosscheck inventory
  For each triage entry, scan inventory_json for files that already
  cover it. If you find ANY existing file matching, the gap is SPURIOUS
  — note it in `spurious_gaps` and move on. Do NOT generate a duplicate spec.

STEP 3 — Plan the additions
  For each LEGITIMATE gap:
    - Decide the file path. Use the existing folder convention
      (smoke/01_*.smoke.{{ext}}, e2e/<area>/NN_*.e2e.{{ext}}, etc.)
    - Decide whether existing page objects can be REUSED:
        IF the new spec uses a page already in pages/<slug>.page.{{ext}}:
          → import it; do NOT redefine
        ELSE:
          → produce a new page object in pages/
    - Decide whether fixtures need extension:
        IF the new spec needs a new test user role not in test_users.json:
          → append the user to fixtures/test_users.json
            (mark "MUST_BE_REPLACED_BEFORE_CI" in a header comment)

STEP 4 — Write the deltas
  Apply the SAME quality bar from the original smoke/e2e/negative steps:
    - data-testid first, then ARIA, then id
    - programmatic auth via support/auth helper
    - no magic sleeps, no class-based selectors
    - parallel-safe data via crypto.randomUUID() / uuid4()
    - intercept aliases for mutation APIs
  If you weaken a rule (e.g. forced to use a CSS class), record it in
  `fragility_notes` with the reason.

STEP 5 — Patch APP_INDEX
  Build an `app_index_patch` block describing the surgical changes:
    pages_added:        [<new Page entries — full schema>]
    apis_added:         [<new endpoint entries discovered in delta_scan>]
    risk_flags_added:   [<new flags surfaced by delta_scan>]
    history_appended:   [{{
      "step": "extend",
      "summary": "...",
      "files_added": N,
      "files_modified": M
    }}]
  Do NOT rewrite or reorder existing fields. The orchestrator merges this
  patch into the on-disk APP_INDEX without touching anything else.

═══════════════════════════════════════════════════════════════════════════
HARD CONSTRAINTS — automatic failure
═══════════════════════════════════════════════════════════════════════════

- Output ONLY files that are NEW or explicitly MODIFIED. Do not echo back
  files you intend to leave untouched.
- modified_files must contain the FULL new contents (not a diff) — the
  caller writes the file verbatim.
- Do NOT regenerate page objects that already exist in inventory_json.
- Do NOT remove or rename existing files in this step. (That's a refactor
  pass, not an extension.)
- All new paths must be relative to the project bundle root and use
  forward slashes.
- If the gap turns out to be untestable, mark it in `coverage_documented_skip`
  and emit ZERO files for it — but DO append a checklist item to the
  upcoming /validate run via `regression_checklist_additions`.

═══════════════════════════════════════════════════════════════════════════
EXAMPLE OF THE QUALITY LEVEL EXPECTED
═══════════════════════════════════════════════════════════════════════════

Suppose the user reports: "we just shipped /wallet — also we don't test
the password reset flow". You triage and produce something shaped like:

```json
{{
  "new_files": {{
    "pages/wallet.page.js": "...",
    "e2e/wallet/01_view_balance.e2e.cy.js": "...",
    "e2e/auth/03_password_reset.e2e.cy.js": "...",
    "fixtures/upload_sample.png": "(binary)"
  }},
  "modified_files": {{
    "fixtures/test_users.json": "{{...full new contents...}}",
    "support/api_client.js": "{{...with new resetPassword() method...}}"
  }},
  "app_index_patch": {{
    "pages_added": [
      {{
        "id": "wallet_page",
        "path": "/wallet",
        "purpose": "User views their balance and transaction history",
        "importance": "high",
        "requires_auth": true,
        ...
      }}
    ],
    "apis_added": [
      {{ "method": "GET", "path": "/api/wallet/balance", ... }}
    ],
    "risk_flags_added": [],
    "history_appended": [{{ "step": "extend", "summary": "added wallet + password reset", "files_added": 4, "files_modified": 2 }}]
  }},
  ...
}}
```

═══════════════════════════════════════════════════════════════════════════
ANTI-PATTERNS — automatic failure
═══════════════════════════════════════════════════════════════════════════

- Returning a "files" map that re-emits unchanged page objects.
- Re-writing APP_INDEX from scratch — must be a patch.
- Adding test cases that duplicate existing smoke coverage.
- Inventing endpoints not present in delta_scan or APP_INDEX.discovered_apis.
- Wholesale renaming files (refactor concern, not extension concern).

═══════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════════════════

Return a single JSON object — no markdown fences, no prose:

{{
  "new_files": {{
    "<relative path>": "<full file contents>"
  }},
  "modified_files": {{
    "<relative path>": "<full new file contents>"
  }},
  "app_index_patch": {{
    "pages_added": [...],
    "apis_added": [...],
    "risk_flags_added": [...],
    "history_appended": [{{
      "step": "extend",
      "summary": "...",
      "files_added": <int>,
      "files_modified": <int>
    }}]
  }},
  "spurious_gaps": [
    {{ "gap": "...", "reason": "already covered by smoke/01_login.smoke.cy.js" }}
  ],
  "coverage_documented_skip": [
    {{ "gap": "...", "reason": "no test sandbox for real Stripe webhooks" }}
  ],
  "regression_checklist_additions": [
    "Rotate the new wallet_admin test user's credentials quarterly",
    ...
  ],
  "fragility_notes": [...],
  "summary": "<2-3 sentences: what gaps were addressed and any surprises>"
}}

Begin. Output the JSON now.
