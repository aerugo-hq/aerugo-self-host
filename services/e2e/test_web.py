"""Browser tests (slice D5): the web app, used as people use it, and always within their permissions."""
import http.client
import json
import re
import uuid
import zipfile

from playwright.sync_api import expect

from conftest import WEB, WEB_B, WEB_C, sign_in

# The seed runbook, by its id: readable by the payments group only. Found by id, not title, because a copy synced from
# GitHub can share its title (and alice, linked to GitHub, may read that copy too). A result opens in the reader.
RUNBOOK = 'a.result[href="#/documents/pay-runbook"]'


def search(page, q: str):
    page.goto(f"{WEB}/#/search")
    box = page.get_by_placeholder("Search by keyword or by meaning")
    box.fill(q)
    box.press("Enter")
    expect(page.locator(".results-meta")).to_be_visible(timeout=30_000)
    return page.locator(".result-title")


def panel(page, heading: str):
    return page.locator("section.panel").filter(has=page.get_by_role("heading", name=heading, exact=True))


def test_signing_in_lands_on_home(page):
    sign_in(page, "alice")
    expect(page.get_by_role("link", name="Home", exact=True)).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("heading", name="Your teams")).to_be_visible()


def test_search_finds_alice_her_teams_runbook(page):
    sign_in(page, "alice")
    search(page, "incident")
    expect(page.locator(RUNBOOK)).to_have_count(1)


def test_search_never_shows_bob_another_teams_runbook(page):
    sign_in(page, "bob")
    search(page, "incident")
    expect(page.locator(RUNBOOK)).to_have_count(0)


def test_compliance_is_for_org_admins(page):
    sign_in(page, "bob")
    expect(page.get_by_role("link", name="Compliance")).to_have_count(0)
    page.goto(f"{WEB}/#/compliance")
    expect(page.get_by_text("Compliance settings are for org admins.")).to_be_visible()


def test_alice_imports_a_notion_export_and_finds_it(page, tmp_path):
    tag = uuid.uuid4().hex[:8]
    name, title = f"e2e-{tag}", f"E2E wiki {tag}"
    export = tmp_path / "export.zip"
    with zipfile.ZipFile(export, "w") as z:
        z.writestr(f"{title} {uuid.uuid4().hex}.md", f"# {title}\n\nImported through the browser, {tag}.")
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/admin/sources")
    imports = panel(page, "Imports")
    imports.get_by_label("Export (zip)").set_input_files(str(export))
    imports.get_by_label("Import name").fill(name)
    imports.get_by_label("group:payments").check()
    imports.get_by_role("button", name="Import", exact=True).click()
    expect(imports.locator("tr", has_text=name)).to_contain_text("1 page, 1 changed, 0 removed", timeout=120_000)
    try:
        expect(search(page, tag).filter(has_text=title)).to_have_count(1)
    finally:
        page.goto(f"{WEB}/#/admin/sources")
        panel(page, "Imports").locator("tr", has_text=name).get_by_role("button", name="Remove").click()
        page.get_by_role("alertdialog").get_by_role("button", name="Remove import").click()
        expect(panel(page, "Imports").locator("tr", has_text=name)).to_have_count(0)


def test_the_gallery_offers_every_source_and_opens_its_form(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/admin/sources")
    panel(page, "Connections").get_by_role("button", name="Connect a source").click()
    gallery = page.get_by_role("list", name="Sources you can connect")
    expect(gallery.get_by_role("listitem").filter(has_text="Confluence Cloud")).to_contain_text("Connect")  # every source is built
    gallery.get_by_role("listitem").filter(has_text="Confluence Cloud").get_by_role("button", name="Connect").click()
    form = page.get_by_role("form", name="Connect Confluence")
    expect(form.get_by_role("textbox", name="Confluence site", exact=True)).to_be_visible()
    expect(form.get_by_label("API token")).to_have_attribute("type", "password")  # a token is never shown
    expect(form.get_by_label(re.compile(r"^Keep")).locator("option[value=live]")).to_have_count(1)  # F11b: live only


def open_connector(page, name: str):
    page.goto(f"{WEB}/#/admin/sources")
    panel(page, "Connections").get_by_role("button", name="Connect a source").click()
    gallery = page.get_by_role("list", name="Sources you can connect")
    gallery.get_by_role("listitem").filter(has_text=name).get_by_role("button", name="Connect").click()


def test_an_org_admin_connects_google_workspace_as_a_directory(page):
    sign_in(page, "olivia")
    open_connector(page, "Directory")
    form = page.get_by_role("form", name="Connect a directory")
    form.get_by_label("Directory").select_option("google")
    expect(form.get_by_label("Admin to act as")).to_have_attribute("type", "email")
    expect(form.get_by_label("Service account key")).to_be_visible()
    expect(form.get_by_role("button", name="Connect Google Workspace")).to_be_visible()


def test_an_org_admin_connects_entra_with_a_certificate(page):
    sign_in(page, "olivia")
    open_connector(page, "Directory")
    form = page.get_by_role("form", name="Connect a directory")
    form.get_by_label("Directory").select_option("entra")
    expect(form.get_by_label("Tenant ID")).to_be_visible()
    expect(form.get_by_label("Application (client) ID")).to_be_visible()
    expect(form.get_by_label(re.compile(r"^Certificate"))).to_be_visible()  # its name includes its hint
    expect(form.get_by_label("API token")).to_have_count(0)  # it signs in with its certificate, never a secret
    expect(form.get_by_role("button", name="Connect Microsoft Entra")).to_be_visible()


def test_a_team_admin_is_told_a_directory_is_an_org_admins(page):
    sign_in(page, "alice")  # a directory decides who has left company-wide, so it isn't a team admin's to connect
    open_connector(page, "Directory")
    expect(page.get_by_text("Only an org admin connects a directory")).to_be_visible()
    expect(page.get_by_role("form", name="Connect a directory")).to_have_count(0)


def test_a_team_admin_opens_the_slack_form(page):
    sign_in(page, "alice")
    open_connector(page, "Slack")
    form = page.get_by_role("form", name="Connect Slack")
    expect(form.get_by_label("Bot token")).to_have_attribute("type", "password")  # a token is never shown
    expect(form.get_by_label("History")).to_have_value("365")
    expect(form.get_by_text("This Slack workspace is our company's own.")).to_be_visible()
    keep = form.get_by_label(re.compile(r"^Keep"))  # F11a: its text indexed (the default), or pointers only
    expect(keep).to_have_value("index")
    keep.select_option("pointer")
    expect(keep).to_have_value("pointer")
    expect(keep.locator("option[value=live]")).to_have_count(0)  # F11b: Slack's search reads as a person


def test_a_team_admin_confirms_capture_only_once_the_channel_asked_for_it(page):
    """C2b: a Slack connection's Capture panel lists its channels; Confirm is offered only once the channel's creator
    ran /aerugo capture on in it (a command signed with the app's signing secret), and confirming turns it on."""
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode
    sign_in(page, "alice")
    tag = uuid.uuid4().hex[:6].upper()
    channel, name, signing = f"CE2E{tag}", f"e2e-slack-{tag.lower()}", uuid.uuid4().hex
    creator = {"id": f"UE2E{tag}", "team_id": "T0SMOKE", "name": "alice", "profile": {"real_name": "Alice", "email": "alice@acme.test"}}
    state = {"users": [creator], "channels": {channel: {"name": f"e2e-{tag.lower()}", "private": True, "creator": creator["id"],
             "members": [creator["id"]], "messages": [{"ts": "1760000001.000001", "user": creator["id"], "text": f"Capture rehearsal {tag}"}]}}}
    assert page.request.post("http://fake-llm:8080/slack/setup", data=json.dumps(state),
                             headers={"Content-Type": "application/json"}).ok, "the fake Slack (fake-llm, from make smoke) isn't up"
    write, base = {"Origin": WEB, "Content-Type": "application/json"}, f"{WEB}/api/workspaces/payments/connections"
    made = page.request.post(f"{base}/slack", headers=write, data=json.dumps({
        "name": name, "api_base": "http://fake-llm:8080/slack/api", "bot_token": "xoxb-smoke-slack-token", "channels": [channel],
        "history_days": 3650, "every_minutes": 10080}))
    assert made.status == 201, made.text()

    def capture():
        listed = page.request.get(f"{base}/{name}/capture").json()["channels"]
        return next((c["capture"] for c in listed if c["channel"] == channel), None)

    def until(want):
        deadline = time.monotonic() + 90
        while capture() != want and time.monotonic() < deadline:
            time.sleep(2)
        assert capture() == want, f"the channel never reached {want}"

    try:
        assert page.request.post(f"{base}/{name}/webhook", headers=write, data=json.dumps({"signing_secret": signing})).status == 201
        until("off")
        page.goto(f"{WEB}/#/admin/sources")
        row = panel(page, "Connections").get_by_role("row").filter(has_text=name)
        row.get_by_role("button", name="Capture").click()
        group = page.get_by_role("group", name=f"Capture for {name}")
        expect(group.get_by_text("Off", exact=True)).to_be_visible(timeout=30_000)
        expect(group.get_by_role("button", name="Confirm")).to_have_count(0)  # nobody asked for it in the channel
        raw, stamp = urlencode({"command": "/aerugo", "text": "capture on", "channel_id": channel, "user_id": creator["id"],
                                "team_id": "T0SMOKE"}), str(int(time.time()))
        signature = "v0=" + hmac.new(signing.encode(), f"v0:{stamp}:{raw}".encode(), hashlib.sha256).hexdigest()
        asked = page.request.post(f"http://api:8000/v1/hooks/slack/{made.json()['id']}/commands", data=raw, headers={
            "Content-Type": "application/x-www-form-urlencoded", "X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature})
        assert asked.ok and asked.json()["response_type"] == "ephemeral", asked.text()
        until("requested")
        group.get_by_role("button", name="Close").click()
        row.get_by_role("button", name="Capture").click()
        expect(group.get_by_text("Asked for in the channel")).to_be_visible(timeout=30_000)
        group.get_by_role("button", name="Confirm").click()
        expect(group.get_by_text("On", exact=True)).to_be_visible(timeout=30_000)
        expect(page.get_by_text("Capture on: the channel is told")).to_be_visible()
        expect(group.get_by_role("button", name="Turn off")).to_be_visible()
    finally:
        page.request.delete(f"{base}/{name}", headers={"Origin": WEB})


def test_a_team_admin_chooses_how_slack_asks_are_answered(page):
    """C2d: a Slack connection's Answers panel starts off; the team admin picks one of the team's models and what may
    reach Slack, and the choice is kept; with nothing allowed to reach Slack, there's nothing to save."""
    sign_in(page, "alice")
    tag = uuid.uuid4().hex[:6].upper()
    name, channel = f"e2e-answers-{tag.lower()}", f"CE2A{tag}"
    write, base = {"Origin": WEB, "Content-Type": "application/json"}, f"{WEB}/api/workspaces/payments"
    page.request.post(f"{base}/models", headers=write, data=json.dumps({  # 201, or 409 when smoke already registered it
        "name": "smoke-fake", "provider": "openai_compatible", "model": "fake", "base_url": "http://fake-llm:8080/v1", "api_key": "unused"}))
    made = page.request.post(f"{base}/connections/slack", headers=write, data=json.dumps({
        "name": name, "api_base": "http://fake-llm:8080/slack/api", "bot_token": "xoxb-smoke-slack-token", "channels": [channel],
        "history_days": 3650, "every_minutes": 10080}))
    assert made.status == 201, made.text()
    try:
        page.goto(f"{WEB}/#/admin/sources")
        row = panel(page, "Connections").get_by_role("row").filter(has_text=name)
        row.get_by_role("button", name="Answers", exact=True).click()
        group = page.get_by_role("group", name=f"Answers for {name}")
        model = group.get_by_label("Model")
        expect(model).to_have_value("", timeout=30_000)  # off until the team's admin chooses
        expect(group.get_by_label("public")).to_be_checked()
        expect(group.get_by_label("internal")).to_be_checked()
        expect(group.get_by_label("confidential")).not_to_be_checked()
        model.select_option("smoke-fake")
        group.get_by_role("button", name="Save").click()
        expect(page.get_by_text("Slack asks are answered through smoke-fake")).to_be_visible(timeout=30_000)
        row.get_by_role("button", name="Answers", exact=True).click()
        expect(group.get_by_label("Model")).to_have_value("smoke-fake", timeout=30_000)
        group.get_by_label("public").uncheck()
        group.get_by_label("internal").uncheck()
        expect(group.get_by_role("button", name="Save")).to_be_disabled()
        kept = page.request.get(f"{base}/connections/{name}/answers").json()
        assert kept == {"model": "payments/smoke-fake", "classifications": ["internal", "public"]}, kept
    finally:
        page.request.delete(f"{base}/connections/{name}", headers={"Origin": WEB})


def test_a_team_admin_opens_the_microsoft_365_form(page):
    sign_in(page, "alice")
    open_connector(page, "Microsoft 365")
    form = page.get_by_role("form", name="Connect Microsoft 365")
    expect(form.get_by_label(re.compile(r"^Sites"))).to_be_visible()
    expect(form.get_by_label(re.compile(r"^Certificate"))).to_be_visible()
    expect(form.get_by_text("never tenant-wide")).to_be_visible()


def test_the_notion_form_says_who_will_read_it(page):
    sign_in(page, "alice")
    open_connector(page, "Notion")
    form = page.get_by_role("form", name="Connect Notion")
    expect(form.get_by_label("Integration token")).to_have_attribute("type", "password")
    expect(form).to_contain_text("readable by this team's members, and no one else")
    expect(form.get_by_label(re.compile(r"^Keep"))).to_have_count(0)  # only Confluence, Jira, and Slack keep pointers (F11a)


def test_the_data_catalog_form_fits_each_catalog(page):
    sign_in(page, "alice")
    open_connector(page, "Data catalog")
    form = page.get_by_role("form", name="Connect a data catalog")
    expect(form.get_by_label("OAuth secret")).to_have_attribute("type", "password")  # Unity Catalog first: a secret is never shown
    form.get_by_label("Catalog").select_option("dataplex")  # read as the runner itself: no secret, and an org admin's
    expect(form.get_by_label("OAuth secret")).to_have_count(0)
    expect(form.get_by_text("Only an org admin connects this one")).to_be_visible()
    expect(form.get_by_role("button", name="Connect Dataplex")).to_be_disabled()
    form.get_by_label("Catalog").select_option("datahub")
    expect(form.get_by_text("readable only by the people DataHub names as its owners")).to_be_visible()


def test_knowledge_health_lists_pages_that_need_someone(page):
    sign_in(page, "alice")  # payments' admin
    page.goto(f"{WEB}/#/t/payments/health")
    expect(page.get_by_role("heading", name="Knowledge health")).to_be_visible()
    expect(page.get_by_text("curated pages")).to_be_visible(timeout=30_000)  # the summary says how many need someone
    filters = page.get_by_role("group", name="Show pages that are")
    if filters.count():
        expect(filters.get_by_role("button", name=re.compile(r"^All "))).to_have_attribute("aria-pressed", "true")


def test_search_narrows_to_a_source(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/search")
    page.get_by_role("searchbox").or_(page.get_by_label("Search", exact=True)).first.fill("payments")
    page.keyboard.press("Enter")
    expect(page.get_by_text(re.compile(r"results? for"))).to_be_visible(timeout=30_000)
    sources = page.get_by_role("group", name="Only from these sources")
    if sources.count():
        first = sources.get_by_role("button").first
        first.click()
        expect(first).to_have_attribute("aria-pressed", "true")


def test_the_maps_clusters_are_named_and_keep_their_colors(page):
    sign_in(page, "olivia")  # every team's curated pages, linked: the map has clusters
    page.goto(f"{WEB}/#/map")
    legend = page.get_by_role("list", name="Clusters on the map")
    expect(legend.get_by_role("listitem").first).to_be_visible(timeout=60_000)
    before = [(item.inner_text(), item.locator(".swatch").get_attribute("class")) for item in legend.get_by_role("listitem").all()]
    page.reload()
    expect(legend.get_by_role("listitem").first).to_be_visible(timeout=60_000)
    after = [(item.inner_text(), item.locator(".swatch").get_attribute("class")) for item in legend.get_by_role("listitem").all()]
    assert before == after, f"the clusters' names or colors changed on reload: {before} then {after}"


def test_olivia_sets_up_and_removes_google_sign_in(page):
    sign_in(page, "olivia")
    page.goto(f"{WEB}/#/compliance/platform")
    google = panel(page, "Google sign-in")
    expect(google).to_contain_text(f"{WEB}/link/google/callback")
    google.get_by_label("Client ID").fill("e2e.apps.googleusercontent.com")
    google.get_by_label("Client secret").fill("e2e-secret")
    google.get_by_role("button", name=re.compile(r"^(Save|Replace)$")).click()
    expect(google).to_contain_text("e2e.apps.googleusercontent.com")
    google.get_by_role("button", name="Remove").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Remove sign-in").click()
    expect(google).to_contain_text("Not set up")


def test_a_session_outlives_the_web_server_it_began_on(page):
    sign_in(page, "alice")  # on the first web server
    elsewhere = page.request.get(f"{WEB_B}/api/whoami")  # the same browser's cookie, at a second one (H3)
    assert elsewhere.status == 200 and elsewhere.json()["username"] == "alice", \
        f"a second web server didn't know the session: {elsewhere.status}"


def test_a_write_must_come_from_the_apps_own_pages(page):
    sign_in(page, "alice")

    def refused(headers: dict) -> bool:  # M8: the proxy refused it as cross-site, before it reached the API
        r = page.request.post(f"{WEB}/api/whoami", headers=headers, data="{}")
        return r.status == 403 and "cross-site" in r.text()
    assert not refused({"Origin": WEB}) and not refused({"Referer": f"{WEB}/#/home"}), "the app's own write was refused"
    assert refused({"Origin": "https://attacker.example"}), "a write from another site went through"
    assert refused({"Referer": "https://attacker.example/page"}), "a write without an Origin, from another site's page, went through"
    assert refused({}), "a write with neither an Origin nor a Referer went through"


def test_an_api_that_doesnt_answer_in_time_gets_a_504_not_a_hung_request(page):
    sign_in(page, "alice")
    late = page.request.get(f"{WEB_C}/api/whoami")  # M9: a web server that gives the API a millisecond
    assert late.status == 504 and "in time" in late.json()["detail"], f"{late.status} {late.text()[:200]}"


def test_an_upload_past_the_limit_is_cut_off_without_being_held_whole(page):
    sign_in(page, "alice")
    session = next(c["value"] for c in page.context.cookies() if c["name"] == "kl_session")
    conn = http.client.HTTPConnection("localhost", 3000, timeout=120)  # M9: 60 MB, its length never declared
    conn.putrequest("POST", "/api/workspaces/payments/imports")
    for name, value in (("Cookie", f"kl_session={session}"), ("Origin", WEB), ("Transfer-Encoding", "chunked"),
                        ("Content-Type", "multipart/form-data; boundary=x")):
        conn.putheader(name, value)
    conn.endheaders()
    # A well-formed form whose file grows past the limit, so the API would read it to its end before refusing it.
    fields = b"".join(b'--x\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % pair
                      for pair in ((b"name", b"m9-oversized"), (b"readers", b"group:payments")))
    parts = [fields + b'--x\r\nContent-Disposition: form-data; name="file"; filename="export.zip"\r\n'
                      b"Content-Type: application/zip\r\n\r\n"] + [b"0" * (1 << 20)] * 60 + [b"\r\n--x--\r\n"]
    try:
        for part in parts:
            conn.send(b"%x\r\n%s\r\n" % (len(part), part))
        conn.send(b"0\r\n\r\n")
    except OSError:
        pass  # cut off mid-upload; the answer says why
    answer = conn.getresponse()
    detail = json.loads(answer.read() or b"{}").get("detail")
    # The proxy's own refusal, not the API's (which would mean the whole upload was held here first and sent on).
    assert answer.status == 413 and detail == "the file is over 50 MB", f"{answer.status} {detail}"


def test_during_a_pilot_someone_on_no_team_sees_why_and_nothing_else(page, browser):
    admin = browser.new_page()  # olivia, the org admin, in a browser of her own
    sign_in(admin, "olivia")
    mode = lambda value: admin.request.put(f"{WEB}/api/admin/admission", headers={"Origin": WEB}, data={"mode": value})
    try:
        assert mode("teams").status == 200  # G4: a pilot; nora is on no team
        sign_in(page, "nora", admitted=False)
        notice = page.get_by_text("Your team isn't on Aerugo yet")
        for _ in range(20):  # each API process reads the mode within half a minute
            if notice.is_visible():
                break
            page.wait_for_timeout(2000)
            page.reload()
        expect(notice).to_be_visible()
        expect(page.get_by_text("You're signed in as nora")).to_be_visible()
        expect(page.get_by_role("link", name="Map", exact=True)).to_have_count(0)  # none of the app's navigation
        expect(page.get_by_role("link", name="Sign out")).to_be_visible()
    finally:
        assert mode("org").status == 200
        admin.close()


def test_every_page_carries_a_content_security_policy_with_a_fresh_nonce(page):
    policies = [page.request.get(f"{WEB}/").headers.get("content-security-policy", "") for _ in range(2)]  # M1
    nonces = [re.search(r"'nonce-([A-Za-z0-9+/=]+)'", p) for p in policies]
    assert nonces[0] and nonces[1] and nonces[0].group(1) != nonces[1].group(1), f"no fresh nonce per page: {policies}"
    for directive in ("script-src 'self'", "'strict-dynamic'", "object-src 'none'", "frame-ancestors 'none'",
                      "frame-src https://www.youtube-nocookie.com https://player.vimeo.com https://www.loom.com"):
        assert directive in policies[0], f"the policy lacks {directive}: {policies[0]}"


def test_the_app_runs_under_its_content_security_policy(page):
    page.add_init_script("window.__violations = []; document.addEventListener('securitypolicyviolation', "
                         "(e) => window.__violations.push(`${e.violatedDirective} ${e.blockedURI}`));")
    sign_in(page, "alice")
    for view in ("#/map", "#/search", "#/documents", "#/ask", "#/home"):
        page.goto(f"{WEB}/{view}")
        page.get_by_role("link", name="Map", exact=True).wait_for()
    page.wait_for_timeout(1000)
    assert page.evaluate("window.__violations") == [], "the app broke its own content security policy"


def test_connected_accounts_offers_linking(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/accounts")
    expect(page.get_by_role("button", name="Link GitHub").first).to_be_visible()


def test_the_command_palette_reaches_a_section_in_one_step(page):
    sign_in(page, "olivia")
    page.keyboard.press("Control+k")
    palette = page.get_by_role("dialog", name="Command palette")
    palette.get_by_role("combobox", name="Command").fill("people's data")
    # As a person would, press Enter once the section is offered: it's listed only after the app knows who you are
    # (/v1/whoami), which can land after the palette opens.
    expect(palette.get_by_role("option", name=re.compile(r"^Compliance: People's data"))).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"#/compliance/people$"))
    expect(page.get_by_role("link", name="People's data")).to_have_attribute("aria-current", "page")
    expect(palette).to_have_count(0)


def test_the_palette_offers_only_what_you_can_use(page):
    sign_in(page, "bob")
    page.keyboard.press("Control+k")
    palette = page.get_by_role("dialog", name="Command palette")
    palette.get_by_role("combobox", name="Command").fill("compliance")
    expect(palette.get_by_role("option", name=re.compile(r"^Compliance"))).to_have_count(0)
    expect(palette.get_by_role("option", name=re.compile(r"^Search for"))).to_have_count(1)
    page.keyboard.press("Escape")
    expect(palette).to_have_count(0)


def test_g_then_a_letter_goes_there(page):
    sign_in(page, "alice")
    page.evaluate("document.activeElement && document.activeElement.blur()")  # shortcuts never fire while typing
    page.keyboard.press("g")
    page.keyboard.press("a")
    expect(page).to_have_url(re.compile(r"#/admin$"))
    expect(page.get_by_role("link", name="Sources")).to_have_attribute("aria-current", "page")


def test_the_ask_workspace_opens_with_your_history(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/ask")
    expect(page.get_by_role("heading", name="New conversation")).to_be_visible()
    expect(page.get_by_role("textbox", name="Question")).to_be_visible()
    expect(page.get_by_role("complementary", name="Your conversations")).to_be_visible()
    expect(page.get_by_role("complementary", name="Knowledge behind the answer")).to_contain_text("Ask a question to see")


def test_search_hands_asking_to_the_ask_workspace(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/search")
    page.get_by_role("tab", name="Ask").click()
    expect(page).to_have_url(re.compile(r"#/ask$"))
    expect(page.get_by_role("link", name="Ask", exact=True)).to_have_attribute("aria-current", "page")


def test_typing_at_focuses_a_question_on_a_document(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/ask")
    box = page.get_by_role("textbox", name="Question")
    box.click()
    box.press_sequentially("who owns @Payments")
    option = page.get_by_role("option", name="Payments API incident response").first  # synced copies share the title
    expect(option).to_be_visible()
    option.click()
    chips = page.get_by_role("group", name="Answering from")
    expect(chips).to_contain_text("@ Payments API incident response")
    expect(box).to_have_value("who owns ")
    chips.get_by_role("button", name="Stop focusing on Payments API incident response").click()
    expect(chips).to_have_count(0)


def test_documents_opens_a_document_and_asks_about_it(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/documents")
    expect(page.get_by_role("heading", name="Documentation", exact=True)).to_be_visible()
    spaces = page.get_by_role("navigation", name="Your spaces")
    expect(spaces.get_by_role("button", name=re.compile(r"^payments", re.I))).to_be_visible()  # every space you're in
    page.get_by_role("searchbox", name="Search every space's pages by title").fill("incident response")  # unfolds them all
    pages = page.get_by_role("navigation", name=re.compile(r"^Documents in payments$", re.I))
    pages.get_by_role("link", name="Payments API incident response").first.click()  # synced copies share the title
    expect(page).to_have_url(re.compile(r"#/documents/.+"))
    expect(page.locator(".reader-title")).to_have_text("Payments API incident response")
    expect(pages.locator('a[aria-current="page"]')).to_have_count(1)  # the tree shows where you are
    page.get_by_role("link", name="Ask about this").click()
    expect(page).to_have_url(re.compile(r"/ask/focus:"))  # in the page's own team: #/t/<team>/ask/focus:<id>
    expect(page.get_by_role("group", name="Answering from")).to_contain_text("@ Payments API incident response")


def test_a_team_admin_sees_its_pipeline(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/admin/pipeline")
    expect(page.get_by_role("heading", name="Pipeline", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Pipeline", exact=True)).to_have_attribute("aria-current", "page")


def test_a_team_admin_sees_its_costs(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/admin/costs")
    expect(page.get_by_role("heading", name="This month")).to_be_visible()
    expect(page.get_by_role("heading", name="Last 30 days")).to_be_visible()
    expect(page.get_by_role("link", name="Costs", exact=True)).to_have_attribute("aria-current", "page")


def test_the_developer_page_runs_a_read_with_your_session(page):
    sign_in(page, "alice")
    expect(page.get_by_text("All systems working")).to_be_visible()
    page.goto(f"{WEB}/#/developer")
    expect(page.get_by_role("heading", name="Connect an AI assistant")).to_be_visible()
    page.get_by_role("button", name="Run GET /v1/whoami").click()
    expect(page.locator(".op-result").first).to_contain_text("alice")


def test_a_team_scope_follows_you_everywhere(page):
    sign_in(page, "alice")
    page.locator(".scope > summary").click()
    page.locator(".scope-menu").get_by_role("link", name=re.compile("payments", re.I)).click()
    expect(page).to_have_url(re.compile(r"#/t/payments/home$"))
    team_pages = page.get_by_role("navigation", name=re.compile(r"^Pages in "))
    team_pages.get_by_role("link", name="Documents", exact=True).click()
    expect(page).to_have_url(re.compile(r"#/t/payments/documents$"))
    expect(page.get_by_role("navigation", name=re.compile(r"^Documents in "))).to_be_visible()
    expect(page.get_by_role("navigation", name="Breadcrumb")).to_contain_text("Documents")
    team_pages.get_by_role("link", name="Map", exact=True).click()
    expect(page).to_have_url(re.compile(r"#/t/payments/map$"))


def test_a_document_opens_in_its_space_at_its_own_address(page):
    sign_in(page, "alice")
    page.goto(f"{WEB}/#/t/payments/documents")
    tree = page.get_by_role("navigation", name=re.compile(r"^Documents in "))
    tree.get_by_role("link").first.click()
    expect(page).to_have_url(re.compile(r"#/t/payments/documents/.+"))
    expect(page.locator(".reader-title")).to_be_visible()
    expect(page.get_by_role("link", name="Ask about this")).to_be_visible()
    expect(tree.locator('a[aria-current="page"]')).to_have_count(1)
    page.go_back()
    expect(page).to_have_url(re.compile(r"#/t/payments/documents$"))
    expect(page.get_by_role("heading", name="Recently added")).to_be_visible()


def test_the_theme_follows_your_choice_and_stays(page):
    sign_in(page, "alice")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")  # light-first
    page.locator(".tb-account > summary").click()
    page.get_by_role("group", name="Theme").get_by_role("button", name="Dark").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")  # the server rendered it from the cookie
    page.locator(".tb-account > summary").click()
    page.get_by_role("group", name="Theme").get_by_role("button", name="System").click()
    expect(page.locator("html")).not_to_have_attribute("data-theme", re.compile(".*"))  # the operating system decides
