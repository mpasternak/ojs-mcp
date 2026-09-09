import json
from typing import Any

import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.catalog import Catalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.exceptions import InputError
from ojs_mcp.tools_write import (
    EDITABLE_FIELDS,
    add_editorial_decision_impl,
    create_announcement_impl,
    edit_publication_metadata_impl,
    publish_publication_impl,
    register_write_tools,
    unpublish_publication_impl,
)

BASE = "https://x.edu/index.php/annual/api/v1"


def _setup():
    cfg = Config(base_url="https://x.edu", journal="annual", allow_writes=True)
    client = OjsClient(cfg, TokenAuth("tok"))
    return client, Catalog(client, cfg)


class _FakeMcp:
    """An MCP server stub collecting tools registered via `.tool()`."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


# --- add_editorial_decision --------------------------------------------------


@respx.mock
async def test_decision_is_translated_to_a_number():
    route = respx.post(f"{BASE}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 6})
    )
    client, catalog = _setup()
    await add_editorial_decision_impl(client, catalog, submission=7, decision="decline")
    body = route.calls.last.request.content.decode()
    assert '"decision": 6' in body or '"decision":6' in body
    # stageId is computed by the server from the decision type — must not be sent.
    assert "stageId" not in body
    await client.aclose()


@respx.mock
async def test_unknown_decision_lists_the_allowed_ones_and_does_not_touch_the_network():
    # D9, Round 1 review of Task 13: the route is REGISTERED, so we can
    # explicitly assert it was NOT called — the mere absence of
    # `@respx.mock` would only prove the lack of a request indirectly
    # (by the test failing/hanging if the request actually went out).
    route = respx.post(f"{BASE}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client, catalog = _setup()
    with pytest.raises(InputError) as exc:
        await add_editorial_decision_impl(
            client, catalog, submission=7, decision="nonsense"
        )
    assert "accept" in str(exc.value)
    assert route.called is False
    await client.aclose()


@respx.mock
async def test_decision_with_review_round_and_actions_in_the_body():
    route = respx.post(f"{BASE}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 4})
    )
    client, catalog = _setup()
    await add_editorial_decision_impl(
        client,
        catalog,
        submission=7,
        decision="pending_revisions",
        review_round=42,
        actions=[{"id": "sendEmail"}],
    )
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "decision": 4,
        "reviewRoundId": 42,
        "actions": [{"id": "sendEmail"}],
    }
    await client.aclose()


@respx.mock
async def test_decision_without_optional_fields_does_not_send_them():
    route = respx.post(f"{BASE}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 2})
    )
    client, catalog = _setup()
    await add_editorial_decision_impl(client, catalog, submission=7, decision="accept")
    body = json.loads(route.calls.last.request.content)
    assert body == {"decision": 2}
    await client.aclose()


@respx.mock
async def test_decision_translates_the_response_to_names():
    respx.post(f"{BASE}/submissions/7/decisions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 99,
                "decision": 6,
                "stageId": 4,
                "submissionId": 7,
                "_href": "https://x.edu/api/v1/submissions/7/decisions/99",
            },
        )
    )
    client, catalog = _setup()
    result = await add_editorial_decision_impl(
        client, catalog, submission=7, decision="decline"
    )
    assert result["decision_name"] == "decline"
    assert result["stage_name"] == "editing"
    # `_href` is not in DECISION_FIELDS — no reason to show the model a URL.
    assert "_href" not in result
    assert result["journal"] == "annual"
    await client.aclose()


# --- edit_publication_metadata -------------------------------------------------


@respx.mock
async def test_edit_rejects_a_field_outside_the_list_and_does_not_touch_the_network():
    route = respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    client, catalog = _setup()
    with pytest.raises(InputError) as exc:
        await edit_publication_metadata_impl(
            client, catalog, submission=1, publication=2, fields={"id": 99}
        )
    assert "id" in str(exc.value)
    for name in EDITABLE_FIELDS:
        assert name in str(exc.value)
    assert route.called is False
    await client.aclose()


@respx.mock
async def test_edit_rejects_an_empty_field_dict_and_does_not_touch_the_network():
    # D1, Round 1 review of Task 13: `fields={}` would pass key validation
    # (an empty set contains nothing outside the list) and would send a
    # meaningless `PUT` with an empty body to production.
    route = respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    client, catalog = _setup()
    with pytest.raises(InputError):
        await edit_publication_metadata_impl(
            client, catalog, submission=1, publication=2, fields={}
        )
    assert route.called is False
    await client.aclose()


@respx.mock
async def test_edit_rejects_a_readonly_field_despite_looking_like_metadata():
    # `categoryIds`, `citationsRaw`, `locale` are flagged `readOnly` in
    # the publication.json schema (pkp-lib, main branch) — see the
    # reasoning at `EDITABLE_FIELDS` in tools_write.py. They must not
    # pass despite sounding like plain metadata.
    route = respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    client, catalog = _setup()
    for field in ("categoryIds", "citationsRaw", "locale"):
        with pytest.raises(InputError) as exc:
            await edit_publication_metadata_impl(
                client, catalog, submission=1, publication=2, fields={field: "x"}
            )
        assert field in str(exc.value)
    assert route.called is False
    await client.aclose()


@respx.mock
async def test_edit_multilingual_field_without_distortion():
    route = respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2, "title": {"pl": "Tytuł"}})
    )
    client, catalog = _setup()
    fields = {"title": {"pl": "Tytuł testowy", "en": "Test title"}}
    await edit_publication_metadata_impl(
        client, catalog, submission=1, publication=2, fields=fields
    )
    body = json.loads(route.calls.last.request.content)
    assert body == fields
    await client.aclose()


@respx.mock
async def test_edit_sends_exactly_the_given_fields():
    route = respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    client, catalog = _setup()
    fields = {"sectionId": 3, "pages": "12-34", "copyrightYear": 2026}
    await edit_publication_metadata_impl(
        client, catalog, submission=1, publication=2, fields=fields
    )
    body = json.loads(route.calls.last.request.content)
    assert body == fields
    await client.aclose()


@respx.mock
async def test_edit_trims_the_response_and_translates_status():
    respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 2,
                "submissionId": 1,
                "status": 3,
                "title": {"pl": "Tytuł"},
                "_href": "https://x.edu/api/v1/submissions/1/publications/2",
                "authors": [{"id": 5, "fullName": "Jan Kowalski", "email": "j@x.edu"}],
            },
        )
    )
    client, catalog = _setup()
    result = await edit_publication_metadata_impl(
        client, catalog, submission=1, publication=2, fields={"sectionId": 1}
    )
    assert result["status_name"] == "published"
    assert "_href" not in result
    assert result["authors"] == [
        {"id": 5, "fullName": "Jan Kowalski", "email": "j@x.edu"}
    ]
    assert result["journal"] == "annual"
    await client.aclose()


# --- publish_publication / unpublish_publication -----------------------------------


@respx.mock
async def test_publish_sends_a_put_without_a_body():
    route = respx.put(f"{BASE}/submissions/1/publications/2/publish").mock(
        return_value=httpx.Response(200, json={"id": 2, "status": 3})
    )
    client, catalog = _setup()
    result = await publish_publication_impl(
        client, catalog, submission=1, publication=2
    )
    request = route.calls.last.request
    assert request.content == b""
    assert "content-type" not in request.headers
    assert result["status_name"] == "published"
    await client.aclose()


@respx.mock
async def test_unpublish_sends_a_put_without_a_body():
    route = respx.put(f"{BASE}/submissions/1/publications/2/unpublish").mock(
        return_value=httpx.Response(200, json={"id": 2, "status": 1})
    )
    client, catalog = _setup()
    result = await unpublish_publication_impl(
        client, catalog, submission=1, publication=2
    )
    request = route.calls.last.request
    assert request.content == b""
    assert "content-type" not in request.headers
    assert result["status_name"] == "queued"
    await client.aclose()


@respx.mock
async def test_publish_without_a_response_body_gives_an_explicit_confirmation():
    # D7, Round 1 review of Task 13: an empty OJS response must not
    # return the model just `{"journal": ...}` — indistinguishable from a
    # bug in our trimming code.
    respx.put(f"{BASE}/submissions/1/publications/2/publish").mock(
        return_value=httpx.Response(200, content=b"")
    )
    client, catalog = _setup()
    result = await publish_publication_impl(
        client, catalog, submission=1, publication=2
    )
    assert result["completed"] is True
    assert result["note"]
    assert result["journal"] == "annual"
    await client.aclose()


# --- create_announcement ----------------------------------------------------------


@respx.mock
async def test_create_announcement_sends_the_title_and_never_sends_an_email():
    # W1, Round 1 review of Task 13: `send_email` removed from the
    # signature — `sendEmail` is hardcoded as `False` (the key stays,
    # because OJS reads it without a default value).
    route = respx.post(f"{BASE}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 3, "title": {"pl": "Nabór"}})
    )
    client, catalog = _setup()
    await create_announcement_impl(client, catalog, title={"pl": "Nabór"})
    body = json.loads(route.calls.last.request.content)
    assert body == {"title": {"pl": "Nabór"}, "sendEmail": False}
    await client.aclose()


@respx.mock
async def test_create_announcement_with_optional_fields():
    route = respx.post(f"{BASE}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 3})
    )
    client, catalog = _setup()
    await create_announcement_impl(
        client,
        catalog,
        title={"pl": "Nabór"},
        content={"pl": "Treść"},
        summary={"pl": "Skrót"},
        type_id=2,
        expiry_date="2026-12-31",
    )
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "title": {"pl": "Nabór"},
        "sendEmail": False,
        "description": {"pl": "Treść"},
        "descriptionShort": {"pl": "Skrót"},
        "typeId": 2,
        "dateExpire": "2026-12-31",
    }
    await client.aclose()


@respx.mock
async def test_create_announcement_requires_a_title_and_does_not_touch_the_network():
    route = respx.post(f"{BASE}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 3})
    )
    client, catalog = _setup()
    with pytest.raises(InputError):
        await create_announcement_impl(client, catalog, title={})
    assert route.called is False
    await client.aclose()


# --- register_write_tools -----------------------------------------------------------


@respx.mock
async def test_register_write_tools_registers_all_tools_and_works():
    # Registration and one functional call on the stub — checking the
    # DESCRIPTIONS visible to the model (the warning, decision/field
    # names) lives in `tests/test_server.py` on a REAL server, not here
    # (D11, Round 1 review of Task 13: the stub reads `fn.__doc__`, which
    # is not guaranteed to be the same as `Tool.description`, which is
    # what the model actually sees).
    client, catalog = _setup()
    mcp = _FakeMcp()
    register_write_tools(mcp, client, catalog)

    expected = {
        "add_editorial_decision",
        "edit_publication_metadata",
        "publish_publication",
        "unpublish_publication",
        "create_announcement",
    }
    assert set(mcp.tools) == expected
    assert len(mcp.tools) == 5

    route = respx.post(f"{BASE}/submissions/1/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 2})
    )
    result = await mcp.tools["add_editorial_decision"](submission=1, decision="accept")
    assert json.loads(route.calls.last.request.content) == {"decision": 2}
    assert result["decision_name"] == "accept"
    await client.aclose()
