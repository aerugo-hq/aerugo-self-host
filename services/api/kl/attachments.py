"""Attachments as their own documents (Q7, K2b): a Confluence page's or a Jira issue's attached files, each read
through the extractors, with exactly its page's readers (an attachment is never readable by anyone its page isn't),
up to MAX_BYTES and only in formats the extractors read. Its text opens with the page it's attached to, by address,
so the references pass (K2) links the two as a recorded link, with that address as its evidence."""
from collections.abc import Callable

from .extract import ExtractionError, extract, supported
from .sync_items import Item

MAX_BYTES = 20 * 1024 * 1024
MAX_PER_PAGE = 50


def attachment_item(key: str, uri: str, version: str, name: str, size: int | None, page_title: str, page_uri: str, path: str,
                    acl: list[str], download: Callable[[], bytes]) -> Item | None:
    """One attachment as an item, or None when it's too large or a format nothing reads."""
    if not name or not supported(name) or (size is not None and size > MAX_BYTES):
        return None

    def sections():
        data = download()
        if len(data) > MAX_BYTES:
            raise ExtractionError("the attachment is larger than its listing said")
        title, parts = extract(name, data, name)
        return title or name, [(None, f"Attached to {page_title}: {page_uri}")] + parts
    return Item(key=key, uri=uri, version=version, title=name, path=path, acl=acl, sections=sections)
