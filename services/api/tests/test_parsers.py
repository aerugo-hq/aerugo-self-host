"""Parsers (M4): how a source's text becomes sections a search can cite, and how a source's answers are read."""
import httpx

from kl import confluence, gdrive, jira, markdown, source_http


# ---- Markdown
def test_markdown_sections_carry_their_heading_path_and_the_first_heading_is_the_title():
    title, sections = markdown.parse("# Runbook\n\nIntro.\n\n## Ledger\n\n### Checks\n\nRun them.\n", "fallback")
    assert title == "Runbook"
    assert sections == [("Runbook", "Intro."), ("Runbook > Ledger > Checks", "Run them.")]


def test_markdown_headings_inside_a_code_fence_are_text():
    _, sections = markdown.parse("# Doc\n\n```\n# not a heading\n```\n", "fallback")
    assert sections == [("Doc", "```\n# not a heading\n```")]


def test_markdown_front_matter_isnt_content_and_a_titleless_page_takes_the_fallback():
    title, sections = markdown.parse("---\nid: x\n---\nJust text.\n", "From the file name")
    assert title == "From the file name" and sections == [(None, "Just text.")]


def test_a_long_markdown_section_is_split_at_paragraphs_within_the_limit():
    _, sections = markdown.parse("# T\n\n" + "a" * 30 + "\n\n" + "b" * 30 + "\n", "f", max_chars=40)
    assert [body for _, body in sections] == ["a" * 30, "b" * 30]


# ---- Jira's document format
def test_jira_rich_text_becomes_a_line_per_block_with_mentions_named():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Ask "}, {"type": "mention", "attrs": {"text": "@Bob"}}]},
        {"type": "bulletList", "content": [{"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "check   the ledger"}]}]}]}]}
    assert jira.adf_text(doc) == "Ask @Bob\ncheck the ledger"


def test_jira_plain_text_from_an_older_server_is_kept():
    assert jira.adf_text("  plain words \n") == "plain words"


# ---- Confluence's storage format
def test_confluence_storage_becomes_a_section_per_heading():
    _, sections = confluence.storage_sections("<p>Intro</p><h2>Steps</h2><p>Do this.</p>", "Page")
    assert sections == [(None, "Intro"), ("Steps", "Do this.")]


def test_a_confluence_table_becomes_a_pipe_table():
    _, sections = confluence.storage_sections("<table><tr><th>Asset</th><th>Owner</th></tr><tr><td>ORDER_HIST</td><td>a|b</td></tr></table>", "P")
    assert sections == [(None, "| Asset | Owner |\n|---|---|\n| ORDER_HIST | a\\|b |")]


def test_a_confluence_task_says_whether_its_done_from_its_status_alone():
    storage = ("<ac:task-list><ac:task><ac:task-status>complete</ac:task-status><ac:task-body>Ship it</ac:task-body></ac:task>"
               "<ac:task><ac:task-status>incomplete</ac:task-status><ac:task-body>Tell legal</ac:task-body></ac:task></ac:task-list>")
    body = confluence.storage_sections(storage, "P")[1][0][1]
    assert "- [x] Ship it" in body and "- [ ] Tell legal" in body


def test_malformed_confluence_markup_keeps_what_was_read():
    title, sections = confluence.storage_sections("<p>Kept</p><p", "P")
    assert title == "P" and sections[0][1].startswith("Kept")


# ---- what a source says about its own limits
def test_a_drive_changes_token_is_refused_only_when_drive_says_so():
    refused = httpx.Response(400, json={"error": {"errors": [{"reason": "invalid", "location": "pageToken"}]}})
    assert gdrive.token_refused(refused) and gdrive.token_refused(httpx.Response(410))
    assert not gdrive.token_refused(httpx.Response(400, json={"error": {"errors": [{"location": "driveId"}]}}))
    assert not gdrive.token_refused(httpx.Response(400, text="not json"))
    assert not gdrive.token_refused(httpx.Response(500))  # an outage is never read as an expired token


def test_a_throttled_call_waits_as_long_as_the_source_asks_and_no_longer_than_a_worker_waits():
    assert source_http.wait(httpx.Response(429, headers={"Retry-After": "2"}), 0) == 2.0
    assert source_http.wait(httpx.Response(429, headers={"Retry-After": "3600"}), 0) is None
    assert source_http.wait(httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), 0) == 0.0


def test_a_source_that_doesnt_say_how_long_gets_capped_backoff_with_jitter():
    first, ninth = source_http.wait(httpx.Response(503), 0), source_http.wait(httpx.Response(503), 9)
    assert first is not None and 1.0 <= first < 2.0
    assert ninth is not None and 30.0 <= ninth < 31.0
