"""AI Test Cover Engine — generates senior-quality automation suites from a URL.

Output is a multi-file bundle organized like a real test project, written to:

    qaflow-ai/tests/{project}/{project}-{framework_folder_name}/[{env}/]{file_path}

Each generator returns a dict ``{relative_path: file_contents}`` so the
output reads like something a senior automation engineer would commit to
a fresh repo: page objects (or resource files), separate spec/test files,
config where appropriate.

Modes:
  - black-box   only DOM scan (Playwright)
  - gray-box    DOM + pasted source snippets
  - white-box   DOM + cloned repo (read-only)

Engines:
  - mock     deterministic templates for every supported framework
  - claude   live LLM call when ANTHROPIC_API_KEY is set; falls back to
             mock on any failure so demos keep working
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse


Framework = Literal["cypress", "playwright", "selenium", "robot"]
Language = Literal["javascript", "typescript", "python"]
Mode = Literal["black-box", "gray-box", "white-box"]


# ---------------------------------------------------------------------------
# 1. Page scanner
# ---------------------------------------------------------------------------

async def _login_form_flow(page, auth: dict) -> None:
    """Fill a login form and wait for the post-auth state.

    Expected shape::

        {"kind": "form",
         "login_url": "https://...",
         "username_field": "#email",
         "password_field": "#password",
         "submit": "#login-btn",
         "credentials": {"username": "...", "password": "..."}}

    Best-effort — failures here are surfaced as RuntimeError to the caller.
    """
    creds = auth.get("credentials") or {}
    await page.goto(auth["login_url"], wait_until="domcontentloaded", timeout=15_000)
    if auth.get("username_field"):
        await page.fill(auth["username_field"], creds.get("username", ""))
    if auth.get("password_field"):
        await page.fill(auth["password_field"], creds.get("password", ""))
    if auth.get("submit"):
        await page.click(auth["submit"])
    # Wait for navigation/network settle so the protected page is reachable.
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass


async def _scan_async(
    url: str,
    auth: dict | None = None,
    capture_baseline: bool = False,
) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        try:
            if auth and auth.get("kind") == "form":
                await _login_form_flow(page, auth)
            await page.goto(url, wait_until="networkidle", timeout=15_000)
        except Exception as e:
            await browser.close()
            raise RuntimeError(f"failed to load {url}: {e}")

        title = await page.title()
        baseline_b64 = None
        if capture_baseline:
            try:
                shot = await page.screenshot(full_page=True)
                import base64
                baseline_b64 = base64.b64encode(shot).decode("ascii")
            except Exception:
                pass

        elements = await page.evaluate(
            """() => {
              const text = (el) => (el.innerText || el.textContent || '').trim().slice(0, 120);
              const headings = [...document.querySelectorAll('h1,h2,h3,h4')].map(h => ({
                tag: h.tagName.toLowerCase(),
                id: h.id || null, class: h.className || null, text: text(h),
              }));
              const buttons = [...document.querySelectorAll('button, [role="button"]')].map(b => ({
                id: b.id || null, class: b.className || null,
                type: b.getAttribute('type') || null, text: text(b),
                'data-testid': b.getAttribute('data-testid') || null,
              }));
              const links = [...document.querySelectorAll('a[href]')].map(a => ({
                href: a.getAttribute('href'), text: text(a),
                id: a.id || null, class: a.className || null,
              }));
              const inputs = [...document.querySelectorAll('input, textarea, select')].map(i => ({
                tag: i.tagName.toLowerCase(), id: i.id || null,
                name: i.getAttribute('name') || null,
                type: i.getAttribute('type') || null,
                placeholder: i.getAttribute('placeholder') || null,
                required: i.hasAttribute('required'),
                'aria-label': i.getAttribute('aria-label') || null,
              }));
              const forms = [...document.querySelectorAll('form')].map(f => ({
                id: f.id || null, action: f.getAttribute('action') || null,
                method: f.getAttribute('method') || 'GET',
                input_ids: [...f.querySelectorAll('input,textarea,select')].map(i => i.id || i.name).filter(Boolean),
              }));
              const images = [...document.querySelectorAll('img')].slice(0, 20).map(i => ({
                alt: i.getAttribute('alt') || null,
                src_ends: (i.getAttribute('src') || '').split('/').pop(),
              }));
              const landmarks = ['header','nav','main','footer','aside']
                .flatMap(t => [...document.querySelectorAll(t)].map(el => ({ landmark: t, id: el.id || null, class: el.className || null })));
              return { headings, buttons, links, inputs, forms, images, landmarks };
            }"""
        )
        await browser.close()

    elements["title"] = title
    elements["url"] = url
    elements["counts"] = {k: len(v) for k, v in elements.items() if isinstance(v, list)}
    if baseline_b64:
        elements["baseline_screenshot_b64"] = baseline_b64
    return elements


def scan_page(url: str, auth: dict | None = None,
              capture_baseline: bool = False) -> dict:
    return asyncio.run(_scan_async(url, auth, capture_baseline))


# ---------------------------------------------------------------------------
# Crawl mode — BFS from a start URL, same-origin, budget-bounded
# ---------------------------------------------------------------------------

async def _crawl_async(
    start_url: str,
    max_pages: int = 8,
    same_origin: bool = True,
    auth: dict | None = None,
) -> list[dict]:
    """Visit linked pages BFS up to ``max_pages``. Returns list of scan dicts."""
    visited: set[str] = set()
    queue: list[str] = [start_url]
    pages: list[dict] = []
    start_host = urlparse(start_url).hostname

    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            scan = await _scan_async(url, auth=auth)
        except Exception as e:
            pages.append({"url": url, "error": str(e), "title": None,
                          "headings": [], "buttons": [], "links": [],
                          "inputs": [], "forms": [], "landmarks": [],
                          "counts": {}})
            continue
        pages.append(scan)
        for link in scan.get("links", []):
            href = link.get("href")
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(url, href).split("#", 1)[0]
            if same_origin and urlparse(full).hostname not in (None, "", start_host):
                continue
            if full not in visited:
                queue.append(full)
    return pages


def crawl_pages(start_url: str, max_pages: int = 8,
                same_origin: bool = True,
                auth: dict | None = None) -> list[dict]:
    return asyncio.run(_crawl_async(start_url, max_pages, same_origin, auth))


# ---------------------------------------------------------------------------
# 2. Generator
# ---------------------------------------------------------------------------

@dataclass
class GenerateOptions:
    url: str
    framework: Framework = "cypress"
    language: Language = "javascript"
    mode: Mode = "black-box"
    test_focus: list[str] = field(default_factory=lambda: ["smoke"])
    source_paste: str | None = None
    source_repo_url: str | None = None
    extra_instructions: str | None = None
    project: str | None = None    # only used to name page-object class nicely


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "page"


def _camel(s: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", s)
    return "".join(p.capitalize() for p in parts if p) or "Page"


def _path_slug_from_url(url: str) -> str:
    """Derive a stable slug for filenames from the URL path. /login.html → login."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url).path or "/"
    except Exception:
        p = url
    base = p.strip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return _slug(base or "home")


# --- Cypress (JS) — Page Object Model -------------------------------------

def _mock_cypress_js(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    title = scan.get("title") or "Page"
    page_class = _camel(title)
    page_var = page_class[:1].lower() + page_class[1:]
    file_slug = _path_slug_from_url(opts.url)

    # ---- Page object ----
    locators: list[str] = []
    locators.append(f"  url() {{ return {opts.url!r}; }}")
    locators.append(f"  pageTitle() {{ return cy.title(); }}")

    for h in (scan.get("headings") or [])[:4]:
        if not h.get("text"): continue
        method = _slug(h["text"])[:32] + "Heading"
        sel = f"#{h['id']}" if h.get("id") else h["tag"]
        locators.append(f"  {method}() {{ return cy.get({sel!r}); }}")

    for b in (scan.get("buttons") or [])[:6]:
        if not b.get("id") and not b.get("text"): continue
        method = _slug(b.get('text') or b.get('id'))[:32] + "Button"
        if b.get("data-testid"):
            sel = f'[data-testid="{b["data-testid"]}"]'
        elif b.get("id"):
            sel = f"#{b['id']}"
        else:
            sel = f"button:contains('{b['text'][:30]}')"
        locators.append(f"  {method}() {{ return cy.get({sel!r}); }}")

    for inp in (scan.get("inputs") or [])[:8]:
        if not inp.get("id"): continue
        method = _slug(inp["id"])[:32] + "Input"
        locators.append(f"  {method}() {{ return cy.get('#{inp['id']}'); }}")

    for a in (scan.get("links") or [])[:5]:
        if not a.get("href") or a["href"].startswith("#"): continue
        method = _slug(a.get("text") or a["href"])[:30] + "Link"
        locators.append(f"  {method}() {{ return cy.get(\"a[href='{a['href']}']\"); }}")

    page_object = (
        "// Page object generated by QAFLOW Test Cover Engine.\n"
        f"// Source URL: {opts.url}\n"
        "//\n"
        "// Encapsulates every locator + high-level action for the page so spec\n"
        "// files stay declarative. Add domain actions (e.g. `loginAs`) on top of\n"
        "// the locators below as your suite grows.\n\n"
        f"export class {page_class} {{\n"
        f"  visit() {{ cy.visit(this.url()); }}\n\n"
        + "\n".join(locators) + "\n"
        "}\n"
    )

    # ---- Spec ----
    spec_lines = [
        '/// <reference types="cypress" />',
        f"import {{ {page_class} }} from '../pages/{file_slug}_page.js';",
        "",
        f"describe('{title} — AI smoke', () => {{",
        f"  const {page_var} = new {page_class}();",
        f"  beforeEach(() => {page_var}.visit());",
        "",
        "  it('renders the expected document title', () => {",
        f"    {page_var}.pageTitle().should('include', {title!r});",
        "  });",
        "",
    ]

    for h in (scan.get("headings") or [])[:3]:
        if not h.get("text"): continue
        method = _slug(h["text"])[:32] + "Heading"
        spec_lines += [
            f"  it({h['text'][:60]!r}, () => {{",
            f"    {page_var}.{method}().should('contain.text', {h['text'][:80]!r});",
            "  });",
            "",
        ]

    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id") and not b.get("text"): continue
        method = _slug(b.get('text') or b.get('id'))[:32] + "Button"
        label = b.get('text') or b.get('id') or 'button'
        spec_lines += [
            f"  it('{label[:30]} button is visible and labelled', () => {{",
            f"    {page_var}.{method}().should('be.visible')",
        ]
        if b.get("text"):
            spec_lines.append(f"      .and('contain.text', {b['text'][:40]!r});")
        else:
            spec_lines.append("      ;")
        spec_lines += ["  });", ""]

    for form in (scan.get("forms") or []):
        ids = [i for i in form.get("input_ids", []) if i]
        if not ids: continue
        spec_lines += [
            f"  it('{form.get('id') or 'form'} exposes the expected inputs', () => {{",
        ]
        for inp in ids[:5]:
            method = _slug(inp)[:32] + "Input"
            spec_lines.append(f"    {page_var}.{method}().should('exist');")
        spec_lines += ["  });", ""]

    if "accessibility" in (opts.test_focus or []):
        spec_lines += [
            "  it('major landmarks are present (a11y)', () => {",
            "    cy.get('header').should('exist');",
            "    cy.get('nav').should('exist');",
            "    cy.get('main, section, footer').should('exist');",
            "  });",
            "",
        ]

    spec_lines.append("});\n")
    spec = "\n".join(spec_lines)

    return {
        f"pages/{file_slug}_page.js": page_object,
        f"e2e/{file_slug}.cy.js": spec,
        "README.md": (
            f"# {title} — Cypress suite\n\n"
            f"Generated by QAFLOW AI Test Cover Engine for `{opts.url}`.\n\n"
            "## Layout\n"
            "- `pages/` — page objects (locators + reusable actions).\n"
            "- `e2e/` — spec files; one per page, importing the matching page object.\n\n"
            "## Run\n"
            "```bash\nnpx cypress run --spec 'e2e/**/*.cy.js'\n```\n"
        ),
    }


# --- Playwright (JS / TS) — POM ---------------------------------------------

def _mock_playwright_js(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    title = scan.get("title") or "Page"
    page_class = _camel(title)
    file_slug = _path_slug_from_url(opts.url)

    page_object = (
        "// Page object generated by QAFLOW AI Test Cover Engine.\n"
        f"export class {page_class} {{\n"
        "  constructor(page) { this.page = page; }\n\n"
        f"  async goto() {{ await this.page.goto({opts.url!r}); }}\n"
        "  pageTitle() { return this.page.title(); }\n"
    )
    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id") and not b.get("text"): continue
        method = _slug(b.get('text') or b.get('id'))[:32] + "Button"
        sel = f"#{b['id']}" if b.get("id") else f"button:has-text({b['text'][:30]!r})"
        page_object += f"  {method}() {{ return this.page.locator({sel!r}); }}\n"
    page_object += "}\n"

    spec = (
        "import { test, expect } from '@playwright/test';\n"
        f"import {{ {page_class} }} from '../pages/{file_slug}_page.js';\n\n"
        f"test.describe('{title}', () => {{\n"
        f"  test('renders the expected title', async ({{ page }}) => {{\n"
        f"    const p = new {page_class}(page);\n"
        f"    await p.goto();\n"
        f"    expect(await p.pageTitle()).toContain({title!r});\n"
        "  });\n"
        "});\n"
    )

    return {
        f"pages/{file_slug}_page.js": page_object,
        f"tests/{file_slug}.spec.js": spec,
    }


# --- Robot Framework — resource + test --------------------------------------

def _mock_robot_framework(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    title = scan.get("title") or "Page"
    file_slug = _path_slug_from_url(opts.url)

    # Resource: shared keywords
    resource_lines = [
        "*** Settings ***",
        f"Documentation     Page object resource for {title}",
        "Library           SeleniumLibrary",
        "",
        "*** Variables ***",
        f"${{URL}}          {opts.url}",
        "${BROWSER}        Chrome",
        "",
        "*** Keywords ***",
        f"Open {title} Page".replace("·", "-"),
        "    Open Browser    ${URL}    ${BROWSER}",
        "    Maximize Browser Window",
        "",
        "Close Test Browser",
        "    Close All Browsers",
        "",
    ]
    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id") and not b.get("text"): continue
        kw = (b.get("text") or b.get("id") or "Button")[:40].title()
        sel = f"id={b['id']}" if b.get("id") else f"xpath=//button[contains(.,'{b['text'][:30]}')]"
        resource_lines += [
            f"{kw} Button Should Be Visible",
            f"    Element Should Be Visible    {sel}",
            "",
        ]
    for form in (scan.get("forms") or []):
        for inp in (form.get("input_ids") or [])[:5]:
            if not inp: continue
            resource_lines += [
                f"Field {inp.title()} Should Exist",
                f"    Page Should Contain Element    id={inp}",
                "",
            ]
    resource = "\n".join(resource_lines)

    # Test suite: imports the resource
    test_lines = [
        "*** Settings ***",
        f"Documentation     AI smoke for {title}",
        f"Resource          ../resources/{file_slug}.resource",
        f"Test Setup        Open {title} Page".replace("·", "-"),
        "Test Teardown     Close Test Browser",
        "",
        "*** Test Cases ***",
        f"{title} Loads With Expected Title".replace("·", "-"),
        f"    Title Should Be    {title}",
        "",
    ]
    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id") and not b.get("text"): continue
        kw = (b.get("text") or b.get("id") or "Button")[:40].title()
        test_lines += [
            f"{kw} Button Visibility",
            f"    {kw} Button Should Be Visible",
            "",
        ]
    for form in (scan.get("forms") or []):
        ids = [i for i in form.get("input_ids", []) if i]
        if not ids: continue
        test_lines.append(f"{form.get('id') or 'Form'} Exposes Expected Fields".title())
        for inp in ids[:5]:
            test_lines.append(f"    Field {inp.title()} Should Exist")
        test_lines.append("")
    test_suite = "\n".join(test_lines)

    return {
        f"resources/{file_slug}.resource": resource,
        f"tests/{file_slug}.robot": test_suite,
        "README.md": (
            f"# {title} — Robot Framework suite\n\n"
            "## Layout\n"
            "- `resources/` — Robot resource files holding shared keywords and locators.\n"
            "- `tests/` — Robot test suites consuming those resources.\n\n"
            "## Run\n"
            "```bash\n"
            "robot --variable BROWSER:Chrome tests/\n"
            "```\n"
        ),
    }


# --- Pytest + Playwright — class-based POM ----------------------------------

def _mock_pytest_playwright(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    title = scan.get("title") or "Page"
    page_class = _camel(title)
    file_slug = _path_slug_from_url(opts.url)

    page_lines = [
        "# Page object generated by QAFLOW AI Test Cover Engine.",
        "from playwright.sync_api import Page, Locator",
        "",
        "",
        f"class {page_class}:",
        f"    URL = {opts.url!r}",
        "",
        "    def __init__(self, page: Page) -> None:",
        "        self.page = page",
        "",
        "    def visit(self) -> None:",
        "        self.page.goto(self.URL)",
        "",
        "    def page_title(self) -> str:",
        "        return self.page.title()",
        "",
    ]
    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id"): continue
        method = _slug(b.get('text') or b.get('id'))[:32] + "_button"
        page_lines += [
            f"    def {method}(self) -> Locator:",
            f"        return self.page.locator('#{b['id']}')",
            "",
        ]
    page_object = "\n".join(page_lines)

    test_lines = [
        "import pytest",
        "from playwright.sync_api import expect",
        f"from pages.{file_slug}_page import {page_class}",
        "",
        "",
        "@pytest.fixture",
        f"def {file_slug}_page(page):",
        f"    p = {page_class}(page)",
        "    p.visit()",
        "    return p",
        "",
        "",
        "class TestSmoke:",
        f"    def test_page_title(self, {file_slug}_page):",
        f"        assert {title!r} in {file_slug}_page.page_title()",
        "",
    ]
    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id"): continue
        method = _slug(b.get('text') or b.get('id'))[:32] + "_button"
        test_lines += [
            f"    def test_{method}_visible(self, {file_slug}_page):",
            f"        expect({file_slug}_page.{method}()).to_be_visible()",
            "",
        ]
    test_suite = "\n".join(test_lines)

    return {
        f"pages/{file_slug}_page.py": page_object,
        f"tests/test_{file_slug}.py": test_suite,
        "conftest.py": (
            "import pytest\n\n\n"
            "@pytest.fixture(scope='session')\n"
            "def browser_context_args(browser_context_args):\n"
            "    return {**browser_context_args, 'viewport': {'width': 1280, 'height': 800}}\n"
        ),
    }


# --- Pytest + Selenium — class-based POM ------------------------------------

def _mock_pytest_selenium(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    title = scan.get("title") or "Page"
    page_class = _camel(title)
    file_slug = _path_slug_from_url(opts.url)

    page_lines = [
        "# Page object generated by QAFLOW AI Test Cover Engine.",
        "from selenium.webdriver.common.by import By",
        "",
        "",
        f"class {page_class}:",
        f"    URL = {opts.url!r}",
        "",
        "    def __init__(self, driver) -> None:",
        "        self.driver = driver",
        "",
        "    def visit(self) -> None:",
        "        self.driver.get(self.URL)",
        "",
    ]
    for b in (scan.get("buttons") or [])[:4]:
        if not b.get("id"): continue
        method = _slug(b.get('text') or b.get('id'))[:32] + "_button"
        page_lines += [
            f"    def {method}(self):",
            f"        return self.driver.find_element(By.ID, {b['id']!r})",
            "",
        ]
    page_object = "\n".join(page_lines)

    test_lines = [
        "import pytest",
        "from selenium import webdriver",
        "from selenium.webdriver.chrome.service import Service",
        "from webdriver_manager.chrome import ChromeDriverManager",
        f"from pages.{file_slug}_page import {page_class}",
        "",
        "",
        "@pytest.fixture",
        "def driver():",
        "    drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()))",
        "    yield drv",
        "    drv.quit()",
        "",
        "",
        "class TestSmoke:",
        f"    def test_page_title(self, driver):",
        f"        page = {page_class}(driver)",
        "        page.visit()",
        f"        assert {title!r} in driver.title",
        "",
    ]
    test_suite = "\n".join(test_lines)

    return {
        f"pages/{file_slug}_page.py": page_object,
        f"tests/test_{file_slug}.py": test_suite,
    }


# --- Dispatcher ------------------------------------------------------------

def _generate_with_mock(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    if opts.framework == "cypress" and opts.language in ("javascript", "typescript"):
        return _mock_cypress_js(scan, opts)
    if opts.framework == "playwright" and opts.language in ("javascript", "typescript"):
        return _mock_playwright_js(scan, opts)
    if opts.framework == "playwright" and opts.language == "python":
        return _mock_pytest_playwright(scan, opts)
    if opts.framework == "robot":
        return _mock_robot_framework(scan, opts)
    if opts.framework == "selenium":
        return _mock_pytest_selenium(scan, opts)
    return {"NOT_WIRED.txt": (
        "Mock generator for this framework/language combination is not yet wired.\n"
        "Set ANTHROPIC_API_KEY to use the live AI engine.\n"
    )}


def _maybe_clone_repo(repo_url: str) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="qaflow-src-"))
    try:
        subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", repo_url, str(tmp)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        chunks: list[str] = []
        budget = 30_000
        for p in sorted(tmp.rglob("*")):
            if not p.is_file() or p.suffix not in (".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".py"):
                continue
            try: text = p.read_text()
            except Exception: continue
            chunk = f"### FILE: {p.relative_to(tmp).as_posix()}\n```\n{text[:5000]}\n```\n"
            if len(chunk) > budget: break
            budget -= len(chunk); chunks.append(chunk)
        return "\n\n".join(chunks)
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _generate_with_claude(scan: dict, opts: GenerateOptions) -> dict[str, str]:
    import anthropic

    framework_hint = {
        "cypress":     "Cypress 13 with Page Object Model (separate `pages/` and `e2e/` files)",
        "playwright":  "Playwright Test, fixture-based, with Page Object Model",
        "selenium":    "pytest + Selenium with class-based POM",
        "robot":       "Robot Framework, resource files for keywords + test suites that import them",
    }[opts.framework]
    lang_hint = {
        "javascript": "modern JavaScript (ESM)", "typescript": "TypeScript", "python": "Python 3.11+",
    }[opts.language]

    extra = ""
    if opts.mode == "gray-box" and opts.source_paste:
        extra += f"\nSOURCE FILES:\n```\n{opts.source_paste[:25_000]}\n```"
    elif opts.mode == "white-box" and opts.source_repo_url:
        cloned = _maybe_clone_repo(opts.source_repo_url)
        if cloned: extra += f"\nSOURCE FILES (cloned from {opts.source_repo_url}):\n{cloned}"
    if opts.extra_instructions:
        extra += f"\nEXTRA INSTRUCTIONS:\n{opts.extra_instructions}"

    focus = ", ".join(opts.test_focus) if opts.test_focus else "smoke"
    project_hint = f"Project name: {opts.project}" if opts.project else ""

    prompt = f"""You are a senior automation engineer designing a fresh test project.

Generate a small but production-quality test suite for the page below using
{framework_hint} in {lang_hint}.

URL:           {opts.url}
Page title:    {scan.get('title')}
Mode:          {opts.mode}
Test focus:    {focus}
{project_hint}

PAGE STRUCTURE (Playwright scan):
{_format_scan_for_prompt(scan)}
{extra}

Return ONLY a JSON object of this exact shape — no prose, no markdown fences:

  {{
    "files": {{
      "<relative path>": "<full file contents>",
      ...
    }},
    "summary": "<one short sentence about what was generated>"
  }}

Hard constraints:
- Apply real OOP / page-object discipline. Locators + actions belong in
  page objects (or Robot resources); spec/test files orchestrate flows.
- Never duplicate a selector across pages and tests.
- File paths are relative to a fresh `{{project}}-{opts.framework}/` folder. Use
  reasonable subfolders (e.g. `pages/`, `e2e/`, `tests/`, `resources/`).
- Prefer stable selectors: `data-testid` > `id` > role/text. Avoid CSS that
  ties to implementation.
- Include a tiny README.md explaining how to run the suite.
- No external test fixtures unless the scan implies them.
"""

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
        raw = re.sub(r"\n```\s*$", "", raw)
    data = json.loads(raw)
    files = data.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError("claude returned `files` that is not an object")
    return {str(k): str(v) for k, v in files.items()}


def _format_scan_for_prompt(scan: dict) -> str:
    lines = [f"- title: {scan.get('title')}"]
    for key in ("headings", "buttons", "links", "inputs", "forms", "landmarks"):
        items = scan.get(key, [])
        lines.append(f"\n## {key} ({len(items)})")
        for it in items[:25]: lines.append(f"- {it}")
    return "\n".join(lines)


def generate_tests(scan: dict, opts: GenerateOptions) -> dict:
    """Returns ``{files, engine, framework, language, [fallback_reason], scan_summary, url}``."""
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            files = _generate_with_claude(scan, opts)
            return {"files": files, "engine": "claude",
                    "framework": opts.framework, "language": opts.language,
                    "url": opts.url}
        except Exception as e:
            return {
                "files": _generate_with_mock(scan, opts),
                "engine": "mock",
                "framework": opts.framework, "language": opts.language,
                "fallback_reason": f"claude_failed: {type(e).__name__}: {e}",
                "url": opts.url,
            }
    return {"files": _generate_with_mock(scan, opts), "engine": "mock",
            "framework": opts.framework, "language": opts.language, "url": opts.url}


# ---------------------------------------------------------------------------
# 3. Save bundle into the new tests/ root
# ---------------------------------------------------------------------------

def _slug_segment(s: str | None, fallback: str = "") -> str:
    if not s: return fallback
    out = re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-").lower()
    return out.lstrip(".") or fallback


def _resolve_framework_id(framework: str) -> str:
    import frameworks as fwmod
    if fwmod.by_id(framework): return framework
    for spec in fwmod.REGISTRY:
        if spec.folder_name == framework: return spec.id
    return "cypress-js"


def _tests_root() -> Path:
    """Top-level qaflow-ai/tests/ — the new home for AI-generated test projects."""
    import frameworks as fwmod
    root = fwmod.QAFLOW_ROOT / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_relpath(rel: str) -> str:
    """Reject upward path components so generated paths stay inside the bundle."""
    parts = [p for p in re.split(r"[\\/]+", rel) if p and p != "." and p != ".."]
    return "/".join(parts)


def list_projects() -> list[dict]:
    """Walk qaflow-ai/tests/ and surface every project + its framework folders."""
    import frameworks as fwmod
    root = _tests_root()
    if not root.exists():
        return []

    out: dict[str, dict] = {}
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = project_dir.name
        rec = out.setdefault(slug, {
            "name": slug, "slug": slug,
            "spec_count": 0, "frameworks": [], "envs": [],
        })
        for fw_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            # Folder pattern: {project}-{framework_folder_name}
            framework = None
            if fw_dir.name.startswith(slug + "-"):
                folder_name = fw_dir.name[len(slug) + 1:]
                framework = next((s for s in fwmod.REGISTRY if s.folder_name == folder_name), None)
            files = [p for p in fw_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
            rec["spec_count"] += len(files)
            rec["frameworks"].append({
                "id":         framework.id if framework else "unknown",
                "name":       framework.name if framework else fw_dir.name,
                "folder":     str(fw_dir),
                "spec_count": len(files),
            })
    return list(out.values())


def save_bundle(
    files: dict[str, str],
    framework: str,
    project: str,
    env: str | None = None,
) -> dict:
    """Write every entry of ``files`` under ``tests/{project}/{project}-{folder_name}/[{env}/]``.

    Returns the bundle root path and the list of relative file paths written.
    """
    import frameworks as fwmod

    if not files:
        raise ValueError("no files to save")
    if not project or not project.strip():
        raise ValueError("project is required (used as the bundle folder name)")

    framework_id = _resolve_framework_id(framework)
    spec = fwmod.by_id(framework_id)
    if not spec:
        raise ValueError(f"unknown framework: {framework}")

    project_slug = _slug_segment(project)
    env_slug = _slug_segment(env)

    bundle_root = _tests_root() / project_slug / f"{project_slug}-{spec.folder_name}"
    if env_slug:
        bundle_root = bundle_root / env_slug

    written: list[dict] = []
    for rel, content in files.items():
        safe = _safe_relpath(rel)
        if not safe:
            continue
        target = bundle_root / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append({
            "path":        safe,
            "abs_path":    str(target),
            "size_bytes":  len(content.encode("utf-8")),
        })

    return {
        "bundle_root":     str(bundle_root),
        "bundle_relative": str(bundle_root.relative_to(fwmod.QAFLOW_ROOT)),
        "framework":       framework_id,
        "framework_name":  spec.name,
        "project":         project_slug,
        "env":             env_slug or None,
        "files":           written,
    }


# Legacy single-file API kept for any older caller.
def save_spec(filename: str, code: str, framework: str,
              project: str | None = None, env: str | None = None) -> dict:
    return save_bundle(
        files={filename: code},
        framework=framework,
        project=project or "unnamed",
        env=env,
    )


# ---------------------------------------------------------------------------
# Coverage preview — what tests will be generated, before generating
# ---------------------------------------------------------------------------

def estimate_coverage(scan: dict, focus: list[str]) -> dict:
    """Return a coverage estimate based on scanned elements + selected focuses."""
    items: list[dict] = []
    focus = focus or ["smoke"]
    is_smoke = "smoke" in focus
    is_func  = "functional" in focus
    is_a11y  = "accessibility" in focus
    is_visu  = "ui_visual" in focus
    is_xb    = "cross_browser" in focus
    is_perf  = "performance" in focus

    if is_smoke or is_visu:
        items.append({"category": "smoke", "name": "page renders the expected title",
                      "covered": True})
    for h in (scan.get("headings") or [])[:4]:
        items.append({
            "category": "smoke",
            "name": f"renders heading: {(h.get('text') or '(empty)')[:60]}",
            "covered": bool(h.get("text")),
        })
    for b in (scan.get("buttons") or [])[:5]:
        sel = b.get("data-testid") or b.get("id") or b.get("text") or ""
        items.append({
            "category": "functional",
            "name": f"button '{(b.get('text') or sel or 'button')[:30]}' is visible",
            "covered": bool(b.get("id") or b.get("text")),
        })
    for a in (scan.get("links") or [])[:5]:
        if not a.get("href") or a["href"].startswith("#"):
            continue
        items.append({
            "category": "functional",
            "name": f"link '{(a.get('text') or a['href'])[:30]}' points to {a['href'][:40]}",
            "covered": True,
        })
    for f in (scan.get("forms") or []):
        ids = [i for i in (f.get("input_ids") or []) if i]
        if not ids: continue
        items.append({
            "category": "functional",
            "name": f"form {(f.get('id') or 'unnamed')} exposes its inputs",
            "covered": len(ids) > 0,
        })
    if is_a11y:
        items.append({"category": "a11y",
                      "name": "major landmarks are present (header/nav/main/footer)",
                      "covered": len(scan.get("landmarks") or []) >= 3})
    if is_perf:
        items.append({"category": "perf", "name": "DOMContentLoaded under budget",
                      "covered": False, "note": "needs Claude or hand-tuning"})
    if is_xb:
        items.append({"category": "cross-browser",
                      "name": "same suite runs in Chrome / Firefox / WebKit",
                      "covered": False, "note": "framework config; not a per-test thing"})

    total   = len(items)
    covered = sum(1 for x in items if x["covered"])
    return {
        "items": items,
        "total": total,
        "covered_estimate": covered,
        "ratio": round(covered / total, 2) if total else 0,
    }


# ---------------------------------------------------------------------------
# Quality scorecard — heuristic A-F grade for the generated suite
# ---------------------------------------------------------------------------

def score_suite(files: dict[str, str]) -> dict:
    score = 100
    notes: list[dict] = []
    code_blob = "\n".join(files.values())

    n_testid = len(re.findall(r"data-testid", code_blob))
    n_id     = len(re.findall(r"#[A-Za-z][\w-]*", code_blob))
    n_tag    = len(re.findall(r"cy\.get\(['\"][a-z]+['\"]", code_blob))

    if n_testid == 0 and n_id > 0:
        notes.append({"level": "warn",
                      "msg": f"No data-testid selectors; {n_id} ID-based selectors (medium stability)"})
        score -= 10
    elif n_testid > 0:
        notes.append({"level": "ok", "msg": f"{n_testid} data-testid selectors — stable"})
    if n_tag > 0:
        notes.append({"level": "warn",
                      "msg": f"{n_tag} tag-only selectors — fragile to refactors"})
        score -= 5

    n_visible = code_blob.count("be.visible") + code_blob.count("to_be_visible") + code_blob.count("Should Be Visible")
    n_text    = (code_blob.count("contain.text") + code_blob.count("have.text")
                 + code_blob.count("to_have_text"))
    n_attr    = code_blob.count("have.attr") + code_blob.count("to_have_attribute")
    if n_visible > (n_text + n_attr) * 2 and n_text + n_attr < 3:
        notes.append({"level": "warn",
                      "msg": "Mostly visibility checks — consider adding text/attribute assertions"})
        score -= 8
    if (n_text + n_attr) >= 3:
        notes.append({"level": "ok",
                      "msg": f"{n_text} text + {n_attr} attribute assertions — varied"})

    has_pom = any(
        re.search(r"export\s+class\s+\w+", c) or re.search(r"^class\s+\w+", c, re.M)
        or re.search(r"\*\*\*\s+Keywords\s+\*\*\*", c)
        for c in files.values()
    )
    if has_pom:
        notes.append({"level": "ok", "msg": "Page object / keyword discipline detected"})
    else:
        notes.append({"level": "warn", "msg": "No page object / keyword module found"})
        score -= 15

    if len(files) < 2:
        notes.append({"level": "warn", "msg": "Only 1 file generated — POM split missing"})
        score -= 10

    n_tests = (
        code_blob.count("it(") + code_blob.count("test(")
        + code_blob.count("def test_") + len(re.findall(r"^[A-Z].*\n\s+\w", code_blob, re.M))
    )
    if n_tests < 3:
        notes.append({"level": "warn", "msg": f"Only ~{n_tests} test cases — sparse coverage"})
        score -= 5

    score = max(0, min(100, score))
    grade = ("A" if score >= 90 else "B" if score >= 80 else
             "C" if score >= 70 else "D" if score >= 55 else "F")
    return {"score": score, "grade": grade,
            "tests_estimate": n_tests, "files": len(files), "notes": notes}


# ---------------------------------------------------------------------------
# Regenerate diff — compare a fresh generation to the on-disk version
# ---------------------------------------------------------------------------

def diff_against_existing(framework: str, project: str, env: str | None,
                          new_files: dict[str, str]) -> dict:
    """Return per-file status + unified diff against any previously saved bundle."""
    import frameworks as fwmod
    framework_id = _resolve_framework_id(framework)
    spec = fwmod.by_id(framework_id)
    if not spec:
        return {"items": [], "exists": False}
    project_slug = _slug_segment(project)
    env_slug = _slug_segment(env)
    bundle_root = _tests_root() / project_slug / f"{project_slug}-{spec.folder_name}"
    if env_slug:
        bundle_root = bundle_root / env_slug
    if not bundle_root.exists():
        return {"items": [], "exists": False, "bundle_root": str(bundle_root)}

    # Skip binary artefacts (e.g. baseline.png) so the text diff doesn't blow up.
    BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
                   ".zip", ".gz", ".woff", ".woff2", ".ttf", ".ico"}

    items: list[dict] = []
    new_paths = set(new_files.keys())
    existing_paths: set[str] = set()
    for p in bundle_root.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            if p.suffix.lower() in BINARY_EXTS:
                continue
            existing_paths.add(str(p.relative_to(bundle_root)))

    def _safe_read(p: Path) -> str:
        try:
            return p.read_text()
        except (UnicodeDecodeError, OSError):
            return ""

    for path in sorted(new_paths | existing_paths):
        old = _safe_read(bundle_root / path) if path in existing_paths else ""
        new = new_files.get(path, "")
        if path not in existing_paths:
            status = "added"
        elif path not in new_paths:
            status = "removed"
        elif old == new:
            status = "unchanged"
        else:
            status = "modified"
        diff = ""
        if status == "modified":
            diff = "".join(difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", n=2,
            ))
        items.append({"path": path, "status": status,
                      "old_bytes": len(old.encode("utf-8")),
                      "new_bytes": len(new.encode("utf-8")),
                      "diff": diff})
    return {"exists": True, "bundle_root": str(bundle_root), "items": items}


# ---------------------------------------------------------------------------
# Run-this-suite-now — dispatch to the right framework runner
# ---------------------------------------------------------------------------

def run_suite(framework: str, project: str, env: str | None = None,
              timeout_s: int = 300) -> dict:
    """Execute the saved bundle in its workspace. Returns log + exit_code."""
    import frameworks as fwmod
    framework_id = _resolve_framework_id(framework)
    spec = fwmod.by_id(framework_id)
    if not spec:
        raise ValueError(f"unknown framework: {framework}")

    project_slug = _slug_segment(project)
    env_slug = _slug_segment(env)
    bundle_root = _tests_root() / project_slug / f"{project_slug}-{spec.folder_name}"
    if env_slug:
        bundle_root = bundle_root / env_slug
    if not bundle_root.exists():
        raise FileNotFoundError(f"bundle not found: {bundle_root}")

    qaflow_root = fwmod.QAFLOW_ROOT
    cypress_ws  = qaflow_root / "cypress-tests"

    if framework_id in ("cypress-js", "cypress-ts"):
        # Use the bundle as the cypress project; reuse cypress-tests' node_modules.
        # Don't require('cypress') in the config — the bundle dir has no
        # node_modules. Plain module.exports avoids the resolver dance.
        cfg = bundle_root / "cypress.config.js"
        cfg.write_text(
            "// Auto-generated by QAFLOW Test Cover Engine.\n"
            "module.exports = {\n"
            "  e2e: {\n"
            "    specPattern: 'e2e/**/*.cy.js',\n"
            "    supportFile: false,\n"
            "    video: false,\n"
            "    screenshotOnRunFailure: false,\n"
            "  },\n"
            "};\n"
        )
        cmd = [str(cypress_ws / "node_modules" / ".bin" / "cypress"),
               "run", "--project", str(bundle_root), "--browser", "chrome"]
        cwd = str(cypress_ws)
        env_extra = {"CYPRESS_CACHE_FOLDER": "/tmp/qaflow-cypress-cache"}
    elif framework_id == "robot-py":
        venv = qaflow_root / "test-frameworks" / "robot-py" / ".venv"
        cmd = [str(venv / "bin" / "robot"), "tests"]
        cwd = str(bundle_root)
        env_extra = {}
    elif framework_id == "pytest-playwright":
        venv = qaflow_root / "test-frameworks" / "pytest-playwright" / ".venv"
        cmd = [str(venv / "bin" / "pytest"), "tests", "-q"]
        cwd = str(bundle_root)
        env_extra = {}
    elif framework_id == "selenium-py":
        venv = qaflow_root / "test-frameworks" / "selenium-py" / ".venv"
        cmd = [str(venv / "bin" / "pytest"), "tests", "-q"]
        cwd = str(bundle_root)
        env_extra = {}
    elif framework_id == "playwright-js":
        cmd = ["npx", "playwright", "test"]
        cwd = str(qaflow_root / "test-frameworks" / "playwright-js")
        env_extra = {}
    else:
        raise ValueError(f"no runner wired for {framework_id}")

    env = os.environ.copy()
    env.update(env_extra)
    started = __import__("time").time()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True,
            timeout=timeout_s,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        rc  = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") + f"\n[runner] timed out after {timeout_s}s"
        rc  = -1
        timed_out = True

    # Try to extract pass/fail counts from common reporter outputs.
    summary = _parse_run_output(out)

    return {
        "framework_id": framework_id,
        "bundle":       str(bundle_root),
        "cmd":          " ".join(cmd),
        "exit_code":    rc,
        "duration_s":   round(__import__("time").time() - started, 1),
        "timed_out":    timed_out,
        "passed":       summary.get("passed"),
        "failed":       summary.get("failed"),
        "log_tail":     out[-6000:],  # cap response size
    }


def _parse_run_output(text: str) -> dict:
    """Best-effort pass/fail count extraction across reporter formats."""
    m = re.search(r"(\d+)\s+passing", text)
    p = int(m.group(1)) if m else None
    m = re.search(r"(\d+)\s+failing", text)
    f = int(m.group(1)) if m else None
    if p is None and f is None:
        m = re.search(r"(\d+)\s+passed", text)
        p = int(m.group(1)) if m else None
        m = re.search(r"(\d+)\s+failed", text)
        f = int(m.group(1)) if m else None
    if p is None and f is None:
        # Robot 'Critical: X passed, Y failed'
        m = re.search(r"(\d+)\s+critical tests,\s+(\d+)\s+passed,\s+(\d+)\s+failed", text)
        if m:
            p = int(m.group(2)); f = int(m.group(3))
    return {"passed": p, "failed": f}


# ---------------------------------------------------------------------------
# Visual baseline — write the captured PNG into the bundle
# ---------------------------------------------------------------------------

def write_visual_baseline_to_bundle(framework: str, project: str,
                                    env: str | None,
                                    baseline_b64: str) -> str | None:
    import base64, frameworks as fwmod
    framework_id = _resolve_framework_id(framework)
    spec = fwmod.by_id(framework_id)
    if not spec or not baseline_b64:
        return None
    project_slug = _slug_segment(project)
    env_slug = _slug_segment(env)
    bundle_root = _tests_root() / project_slug / f"{project_slug}-{spec.folder_name}"
    if env_slug:
        bundle_root = bundle_root / env_slug
    bundle_root.mkdir(parents=True, exist_ok=True)
    target = bundle_root / "baseline.png"
    try:
        target.write_bytes(base64.b64decode(baseline_b64))
        return str(target)
    except Exception:
        return None
