import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.catalog import Catalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.passthrough import request_impl, validate_path

BASE = "https://x.edu/index.php/annual/api/v1"


def _setup(allow_writes=False):
    cfg = Config(base_url="https://x.edu", journal="annual", allow_writes=allow_writes)
    client = OjsClient(cfg, TokenAuth("tok"))
    return cfg, client, Catalog(client, cfg)


@pytest.mark.parametrize(
    "bad", ["../../etc", "https://evil.example/x", "//evil", "a/../../b"]
)
def test_validate_path_rejects_escaping_the_api(bad):
    with pytest.raises(ValueError):
        validate_path(bad)


def test_validate_path_accepts_a_plain_path():
    assert validate_path("/submissions/1") == "submissions/1"


@respx.mock
async def test_get_works_without_the_flag():
    respx.get(f"{BASE}/vocabs").mock(return_value=httpx.Response(200, json={"ok": 1}))
    cfg, client, catalog = _setup()
    assert await request_impl(client, catalog, cfg, "vocabs") == {"ok": 1}
    await client.aclose()


@respx.mock
async def test_write_without_the_flag_is_rejected():
    cfg, client, catalog = _setup(allow_writes=False)
    with pytest.raises(PermissionError) as exc:
        await request_impl(client, catalog, cfg, "announcements", method="POST")
    assert "OJS_ALLOW_WRITES" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_write_with_the_flag_goes_through():
    respx.post(f"{BASE}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 5})
    )
    cfg, client, catalog = _setup(allow_writes=True)
    result = await request_impl(
        client, catalog, cfg, "announcements", method="POST", body={"title": "x"}
    )
    assert result == {"id": 5}
    await client.aclose()


@respx.mock
async def test_journal_index_gives_the_site_level():
    respx.get("https://x.edu/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    cfg, client, catalog = _setup()
    await request_impl(client, catalog, cfg, "contexts", journal="index")
    await client.aclose()


@respx.mock
async def test_lists_in_params_are_comma_encoded():
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    cfg, client, catalog = _setup()
    await request_impl(client, catalog, cfg, "submissions", params={"status": [1, 3]})
    assert route.calls.last.request.url.params["status"] == "1,3"
    await client.aclose()


# Security tests — path-validation allow-list


@pytest.mark.parametrize(
    "must_reject",
    [
        # Schemes
        "HTTPS://evil.com",
        "HtTpS://evil.com",
        "javascript://evil.com",
        "http://evil.com",
        # Network-style paths
        "//evil.com",
        "///evil.com",
        "​//evil.com",  # ZERO WIDTH SPACE (U+200B) before //
        "﻿//evil.com",  # BOM (U+FEFF) before //
        # Going up a level
        "..",
        "../etc",
        "a/..",
        "a/../etc",
        # Backslashes
        "\\\\evil.com",
        "http:\\\\evil.com",
        "/\\evil.com",
        # Percent-encoding
        "submissions/%2e%2e/1",
        "submissions/..%2f1",
        # Control characters
        "submissions/\r1",
        "submissions/\n1",
        "submissions/\x001",
        # Empty segments
        "",
        "/",
        "submissions//files",
        "submissions/",
        "/submissions/",
        # Whitespace
        " ",
    ],
)
def test_validate_path_must_reject(must_reject):
    """Security tests — every dangerous input must be rejected."""
    with pytest.raises(ValueError):
        validate_path(must_reject)


@pytest.mark.parametrize(
    "must_allow",
    [
        # Plain paths
        "submissions/1",
        "/submissions/1",
        "issues/current",
        "vocabs",
        # More complex
        "stats/publications/timeline",
        "emailTemplates/SUBMISSION_ACK",
        # Dots inside segments (not "..")
        "file..txt",
        "..submissions",
        "submissions..",
        "sub..mission",
    ],
)
def test_validate_path_must_allow(must_allow):
    """Regression tests — legal paths must pass through unchanged."""
    # Check that it does not raise ValueError.
    result = validate_path(must_allow)
    # A leading / should be stripped.
    assert not result.startswith("/")
    # The path itself should be unchanged (apart from the leading /).
    if must_allow.startswith("/"):
        assert result == must_allow[1:]
    else:
        assert result == must_allow
