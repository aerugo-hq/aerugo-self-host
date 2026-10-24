"""The prototype's own check (D0), run in the browser tests' image against the repository mounted at /repo:

    docker compose --profile web --profile e2e run --rm --no-deps -v "$(pwd):/repo:ro" -w /repo/docs/design/prototype e2e \
        pytest -q -p no:cacheprovider --browser chromium check_prototype.py

Every screen, for every persona, at phone, tablet, and desktop widths, renders a page (never "Not found"), raises no
script error, and scrolls no wider than the window; every link on a page leads to a screen; keyboard focus shows; and
the screens that act (the runner, Review, connecting a source, the persona switch) do what they say. Screenshots of the
main screens land in /shots when it's mounted."""
import os
import re

import pytest
from playwright.sync_api import expect

PAGE = "file:///repo/docs/design/prototype/index.html"
PERSONAS = ["ana", "cora", "fay", "kai", "olivia"]
WIDTHS = [(375, 812), (768, 1024), (1440, 900)]
UNITS = ["returns-window", "no-receipt", "cc-returns-stale", "sick-or", "sick-ca", "injury-steps", "pos-offline", "recall-steps", "escalation",
         "first-week", "manager-on-duty", "district-manager", "risk", "pos-help", "cc-supervisor", "returns-owner", "sell-through",
         "entrance-mats", "recall-decision", "recall-lesson", "dec-24-hours", "key-OR101", "poles-ship"]
ROUTES = ["home", "ask", "knowledge", "knowledge?kind=procedure&mine=0", "people", "review", "review/confirm", "review/conflicts", "review/stale",
          "review/corrections", "review/gaps", "review/drafts", "documents", "map", "handovers", "settings", "settings/sources/stores-slack",
          "settings/sources/dist-teams", "settings/organization", "setup", "setup/team", "welcome", "connect", "frontline", "you", "systems",
          "run/injury-steps", "run/pos-offline", "run/recall-steps", "run/escalation", "run/first-week"] + [f"knowledge/{u}" for u in UNITS]
SHOTS = os.path.isdir("/shots")


def errors_on(page) -> list[str]:
    found = []
    page.on("pageerror", lambda e: found.append(str(e)))
    # the app's stylesheet names its select chevron at the site's root (/chevron-down.svg), which a file has no root for
    page.on("console", lambda m: found.append(m.text) if m.type == "error" and "ERR_FILE_NOT_FOUND" not in m.text else None)
    return found


def as_persona(page, who: str) -> None:
    page.goto(f"{PAGE}#/home")
    page.select_option("#persona", who)


@pytest.mark.parametrize("width,height", WIDTHS)
@pytest.mark.parametrize("who", PERSONAS)
def test_every_screen_renders_for_every_persona_and_width(page, who, width, height):
    errors = errors_on(page)
    page.set_viewport_size({"width": width, "height": height})
    as_persona(page, who)
    page.mouse.move(width - 4, height - 4)  # off the rail, which opens under a resting pointer
    for path in ROUTES:
        page.evaluate(f"location.hash = '#/{path}'")
        heading = page.locator("#view h1").first
        expect(heading).to_be_visible()
        assert heading.inner_text() != "Not found", f"{path} as {who} found nothing"
        wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert wide <= 1, f"{path} as {who} at {width}px scrolls {wide}px sideways"
        if SHOTS and path in ("home", "ask", "knowledge", "knowledge/returns-window", "run/injury-steps", "review", "people",
                              "settings/sources/stores-slack", "connect", "frontline", "welcome") and who in ("ana", "cora", "olivia"):
            page.screenshot(path=f"/shots/{who}-{path.replace('/', '_')}-{width}.png", full_page=True)
    assert not errors, f"script errors as {who} at {width}px: {errors}"


@pytest.mark.parametrize("who", PERSONAS)
def test_every_link_on_every_screen_leads_to_a_screen(page, who):
    as_persona(page, who)
    hrefs = set()
    for path in ROUTES:
        page.evaluate(f"location.hash = '#/{path}'")
        hrefs |= set(page.eval_on_selector_all("a[href^='#/']", "els => els.map(e => e.getAttribute('href'))"))
    for href in sorted(hrefs):
        page.evaluate(f"location.hash = {href!r}")
        assert page.locator("#view h1").first.inner_text() != "Not found", f"{href}, linked as {who}, leads nowhere"


def test_keyboard_focus_starts_at_the_skip_link_and_shows(page):
    page.goto(f"{PAGE}#/home")  # a first visit: focus starts at the top (moving between pages focuses each page's heading)
    page.keyboard.press("Tab")
    focused = page.evaluate("[document.activeElement.className, getComputedStyle(document.activeElement).outlineStyle]")
    assert focused[0] == "skip", f"the first Tab went to {focused[0]!r}, not the skip link"
    page.keyboard.press("Tab")
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline not in ("none", ""), "focus doesn't show on the rail"


def test_the_runner_steps_through_and_fits_the_place(page):
    as_persona(page, "ana")
    page.evaluate("location.hash = '#/run/injury-steps'")
    expect(page.locator(".run-steps li")).to_have_count(7)  # Oregon: no California safety log step
    expect(page.locator("#view .lede")).to_have_text("Step 1 of 7")
    page.get_by_role("button", name="Done, next step").click()
    expect(page.locator("#view .lede")).to_have_text("Step 2 of 7")
    expect(page.locator(".run-steps li.done")).to_have_count(1)
    page.select_option("#persona", "cora")
    page.evaluate("location.hash = '#/run/injury-steps'")
    expect(page.locator(".run-steps li")).to_have_count(8)  # California adds its safety log
    expect(page.locator(".run-steps")).to_contain_text("safety log")


def test_review_appears_only_with_something_waiting_and_settles(page):
    as_persona(page, "ana")
    expect(page.locator("#nav").get_by_role("link", name=re.compile("^Review"))).to_have_count(0)
    page.select_option("#persona", "cora")
    review = page.locator("#nav").get_by_role("link", name=re.compile("^Review"))
    expect(review).to_have_count(1)
    review.click()
    expect(page.get_by_role("navigation", name="Review").get_by_role("link")).to_have_count(5)
    first = page.locator(".inbox > li").first
    first.get_by_role("button").first.click()
    expect(first.get_by_role("status")).to_contain_text("done")


def test_connecting_a_source_walks_its_steps_and_the_persona_changes_the_page(page):
    as_persona(page, "olivia")
    page.evaluate("location.hash = '#/connect'")
    for n in range(2, 7):
        page.get_by_role("button", name="Continue").click()
        expect(page.locator("#view .lede")).to_contain_text(f"Step {n} of 6")
    expect(page.get_by_role("link", name="Open the source")).to_be_visible()
    page.evaluate("location.hash = '#/home'")
    expect(page.locator("#view")).to_contain_text("Getting acme started")
    page.select_option("#persona", "fay")
    expect(page.locator("#view")).to_contain_text("Contact center agent at Boise contact center")
    expect(page.locator("#nav").get_by_role("link", name="Settings")).to_have_count(0)
