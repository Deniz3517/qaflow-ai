"""Playwright-based test runner.

Detects the seeded UI bugs in the buggy-app and captures screenshots.
"""

import asyncio
from pathlib import Path
from typing import List

from playwright.async_api import async_playwright


async def _run_checks(page) -> List[dict]:
    """Returns a list of detected bug dicts."""
    bugs = []

    # ---- BUG #3492 — login button alignment
    button_box = await page.locator("#login-button").bounding_box()
    card_box = await page.locator(".card").bounding_box()
    if button_box and card_box:
        button_center = button_box["x"] + button_box["width"] / 2
        card_center = card_box["x"] + card_box["width"] / 2
        if abs(button_center - card_center) > 25:
            bugs.append({
                "id": 3492,
                "title": "Login button misaligned",
                "type": "UI",
                "evidence": (
                    f"button center x={button_center:.0f}, "
                    f"card center x={card_center:.0f}, "
                    f"offset={abs(button_center - card_center):.0f}px"
                ),
            })

    # ---- BUG #3493 — email input type
    email_type = await page.locator("#email").get_attribute("type")
    if email_type != "email":
        bugs.append({
            "id": 3493,
            "title": "Email input missing type validation",
            "type": "Functional",
            "evidence": f'#email type attribute is "{email_type or "text"}", expected "email"',
        })

    # ---- BUG #3494 — error message contrast
    await page.fill("#email", "x")
    await page.fill("#password", "y")
    await page.click("#login-button")
    await page.wait_for_timeout(150)
    status_color = await page.evaluate(
        "() => getComputedStyle(document.getElementById('status')).color"
    )
    # rgb(71, 85, 105) === #475569
    if status_color.replace(" ", "") == "rgb(71,85,105)":
        bugs.append({
            "id": 3494,
            "title": "Error message has poor contrast",
            "type": "Accessibility",
            "evidence": f"status color computed as {status_color} on dark navy background — fails WCAG AA",
        })

    # ---- BUG #3495 — title alignment
    title_align = await page.evaluate(
        "() => getComputedStyle(document.querySelector('.app-title')).textAlign"
    )
    if title_align not in ("center", "-webkit-center"):
        bugs.append({
            "id": 3495,
            "title": "App title not centered in header",
            "type": "UI",
            "evidence": f'.app-title text-align is "{title_align}", expected "center"',
        })

    return bugs


async def _screenshot(url: str, screenshot_path: Path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 480, "height": 600})
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=True)
        await browser.close()


async def run_ui_suite(target_url: str, screenshot_path: Path) -> dict:
    """Runs the UI suite against `target_url` and saves a full-page screenshot.

    Returns: {"bugs": [...], "screenshot": str(path), "passed": int, "failed": int}
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 480, "height": 600})
        page = await context.new_page()
        await page.goto(target_url, wait_until="networkidle")

        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path), full_page=True)

        bugs = await _run_checks(page)
        await browser.close()

    total_checks = 4
    return {
        "bugs": bugs,
        "screenshot": str(screenshot_path),
        "passed": total_checks - len(bugs),
        "failed": len(bugs),
        "total": total_checks,
    }


def run_ui_suite_sync(target_url: str, screenshot_path: Path) -> dict:
    return asyncio.run(run_ui_suite(target_url, screenshot_path))


def screenshot_sync(url: str, screenshot_path: Path):
    return asyncio.run(_screenshot(url, screenshot_path))
