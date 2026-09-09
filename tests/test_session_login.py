import httpx
import pytest
import respx

from ojs_mcp.config import Config
from ojs_mcp.exceptions import LoginError
from ojs_mcp.session_login import (
    detect_captcha,
    extract_csrf_from_form,
    extract_current_user,
    login,
)

BASE = "https://x.edu/index.php/annual"
CFG = Config(base_url="https://x.edu", journal="annual", username="u", password="p")

LOGIN_PAGE = """
<html><form method="post" action="/index.php/annual/login/signIn">
<input type="hidden" name="csrfToken" value="TOKEN-FROM-FORM" />
<input type="text" name="username"><input type="password" name="password">
</form></html>
"""

DASHBOARD_PAGE = """
<html><script>
pkp.currentUser = {"csrfToken":"SESSION-TOKEN","id":42,"roles":[16,65536],
"username":"editor"};
</script></html>
"""


def test_extract_csrf_from_form():
    assert extract_csrf_from_form(LOGIN_PAGE) == "TOKEN-FROM-FORM"


def test_extract_csrf_returns_none_when_missing():
    assert extract_csrf_from_form("<html></html>") is None


def test_extract_current_user():
    data = extract_current_user(DASHBOARD_PAGE)
    assert data["csrfToken"] == "SESSION-TOKEN"
    assert data["id"] == 42
    assert data["roles"] == [16, 65536]


def test_extract_current_user_invalid_json_gives_none():
    # `pkp.currentUser` in an unexpected format: we do not abort the
    # sequence here, just log and return None — login() has a defined
    # fallback path and an explicit error when both strategies fail.
    html = "<script>pkp.currentUser = {invalid json};</script>"
    assert extract_current_user(html) is None


@pytest.mark.parametrize(
    "html,expected",
    [
        ('<div class="g-recaptcha"></div>', "reCAPTCHA"),
        ("<altcha-widget challengeurl='x'></altcha-widget>", "ALTCHA"),
        ("<html>nothing</html>", None),
    ],
)
def test_detect_captcha(html, expected):
    assert detect_captcha(html) == expected


@respx.mock
async def test_full_login_sequence():
    page = respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(200, html=LOGIN_PAGE)
    )
    signin = respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=DASHBOARD_PAGE)
    )

    async with httpx.AsyncClient(follow_redirects=False) as client:
        result = await login(client, CFG, "annual")

    assert page.called
    # CRITICAL: Validation::login() calls checkCSRF() — a POST without
    # csrfToken always fails and is indistinguishable from a wrong password.
    sent = signin.calls.last.request.content.decode()
    assert "csrfToken=TOKEN-FROM-FORM" in sent
    assert result["csrf"] == "SESSION-TOKEN"
    assert result["user"]["id"] == 42
    # dictionaries.ROLES used for a readable role description — not dead code.
    assert result["user"]["role_names"] == ["journal manager", "author"]


@respx.mock
async def test_200_with_form_is_a_failure():
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(200, html=LOGIN_PAGE)
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    # The message must list all three causes — OJS does not distinguish them.
    detail = str(exc.value)
    assert "password" in detail.lower()
    assert "limit" in detail.lower()


@respx.mock
async def test_redirect_to_changepassword_is_a_failure():
    """Regression W5 (review): the address is DELIBERATELY without
    `/login` — the earlier stub (`/login/changePassword/u`) already
    contained the `/login` substring, so removing the
    `and "changepassword" not in location.lower()` condition did not
    fail this test (it would have failed anyway, through the bare
    `/login` match). This address isolates the password-change check
    from the `/login` check.
    """
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{BASE}/user/changePassword"}
        )
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    assert "password" in str(exc.value).lower()


@respx.mock
async def test_location_with_login_as_part_of_a_word_is_not_rejected():
    """Regression W5 (review, second part): the `"/login" in location`
    match was a substring check — a valid target address containing
    "login" as PART of another word (e.g. `/loginHistory`) would be
    incorrectly rejected as a bounce back to the login page. Only
    `/login` as its OWN path segment must be rejected.
    """
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{BASE}/user/loginHistory"}
        )
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=DASHBOARD_PAGE)
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        result = await login(client, CFG, "annual")
    assert result["csrf"] == "SESSION-TOKEN"


@respx.mock
async def test_captcha_aborts_without_sending_the_password():
    """Regression W3 (review): a stub WITHOUT `csrfToken` meant that even
    completely removing CAPTCHA detection (`mechanism = None`) did not
    fail this test — without the CAPTCHA branch, the sequence still
    failed at the NEXT step (no `csrfToken` on the login page), and THAT
    message also contains "OJS_API_TOKEN". The stub page must have a
    valid `csrfToken` field, so the ONLY possible cause of the abort is
    CAPTCHA — and we assert the mechanism's name, not just a mention of
    the token.
    """
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, html='<div class="g-recaptcha"></div>' + LOGIN_PAGE
        )
    )
    signin = respx.post(f"{BASE}/login/signIn")
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    detail = str(exc.value)
    assert "reCAPTCHA" in detail
    assert "OJS_API_TOKEN" in detail
    # The password was NOT sent.
    assert not signin.called


@respx.mock
async def test_no_csrf_on_login_page_is_an_explicit_error():
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(200, html="<html></html>")
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    assert "csrf" in str(exc.value).lower()


@respx.mock
async def test_falls_back_to_the_next_dashboard():
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(return_value=httpx.Response(403))
    respx.get(f"{BASE}/dashboard/reviewAssignments").mock(
        return_value=httpx.Response(200, html=DASHBOARD_PAGE)
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        result = await login(client, CFG, "annual")
    assert result["csrf"] == "SESSION-TOKEN"


@respx.mock
async def test_missing_password_does_not_call_the_network():
    cfg_no_password = Config(base_url="https://x.edu", journal="annual", username="u")
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, cfg_no_password, "annual")
    assert "OJS_PASSWORD" in str(exc.value)


@pytest.mark.parametrize(
    "html",
    [
        # Reversed order: value before name.
        '<input type="hidden" value="TOK" name="csrfToken" />',
        # An id attribute in the middle, between name and value.
        '<input name="csrfToken" id="csrf" value="TOK" />',
        # Spaces around the equals sign.
        '<input name = "csrfToken" value = "TOK" />',
    ],
)
def test_extract_csrf_independent_of_attribute_order(html):
    assert extract_csrf_from_form(html) == "TOK"


@respx.mock
async def test_3xx_without_location_is_a_failure():
    # K1: a cached 304 / application firewall / unusual proxy does not
    # carry a Location header — that is NOT a successful login, just
    # missing information. Without checking `bool(location)`, such a 3xx
    # would pass through as a successful login (an empty string does not
    # contain "/login").
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(return_value=httpx.Response(304))
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    assert "password" in str(exc.value).lower()


@respx.mock
async def test_step1_does_not_follow_the_clients_own_follow_redirects():
    """Regression on the deliberate `follow_redirects=False` decision in
    step 1.

    The client here has global redirect-following TURNED ON — just like
    the production `OjsClient` (`client.py:47`), unlike the other tests
    in this file. The login redirect's target is stubbed as 200 with the
    login page — a trap that would catch the code if `POST
    /login/signIn` followed the redirect automatically instead of seeing
    the raw `Location` header. Without an explicit
    `follow_redirects=False` on this request, this test would end with
    `LoginError` instead of returning a correct login result.
    """
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
    )
    # Trap: if the POST followed the redirect, it would land here.
    respx.get(f"{BASE}/dashboard").mock(
        return_value=httpx.Response(200, html=LOGIN_PAGE)
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=DASHBOARD_PAGE)
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await login(client, CFG, "annual")
    assert result["csrf"] == "SESSION-TOKEN"


@respx.mock
async def test_dashboard_redirected_to_login_does_not_give_a_false_success():
    """Regression on K2: the session is dead between step 1 and step 2.

    OJS redirects a dashboard GET to `/login`; httpx follows it (because
    step 2 deliberately has `follow_redirects=True`) and lands on the
    login page with a 200 status. This page HAS its own hidden csrfToken
    field — that is a token for (another) login, not a session token.
    Returning it as a success would be the same silent false "yes" the
    spec guards against for extracting the session token.
    """
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
    )
    for dashboard in (
        "dashboard/editorial",
        "dashboard/reviewAssignments",
        "dashboard/mySubmissions",
    ):
        respx.get(f"{BASE}/{dashboard}").mock(
            return_value=httpx.Response(302, headers={"Location": f"{BASE}/login"})
        )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    assert "csrf" in str(exc.value).lower()


@respx.mock
async def test_falls_back_to_the_hidden_field_when_no_pkp_current_user():
    # D5: a dashboard page without the pkp.currentUser literal (e.g. an
    # OJS version where the backend does not embed the identity on this
    # particular subpage), but with a hidden csrfToken field — the
    # second, fallback extraction strategy.
    page_without_literal = (
        '<html><input type="hidden" name="csrfToken" value="FALLBACK" /></html>'
    )
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=page_without_literal)
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        result = await login(client, CFG, "annual")
    assert result["csrf"] == "FALLBACK"
    assert result["user"] is None


@respx.mock
async def test_no_dashboard_without_a_token_is_an_explicit_error():
    # D5: all three dashboards respond 200, but none carries either
    # pkp.currentUser or the hidden field — must fail with an explicit
    # LoginError (a message about OJS_API_TOKEN), never a silent None.
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=LOGIN_PAGE))
    respx.post(f"{BASE}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/dashboard"})
    )
    for dashboard in (
        "dashboard/editorial",
        "dashboard/reviewAssignments",
        "dashboard/mySubmissions",
    ):
        respx.get(f"{BASE}/{dashboard}").mock(
            return_value=httpx.Response(200, html="<html>no token</html>")
        )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(LoginError) as exc:
            await login(client, CFG, "annual")
    assert "OJS_API_TOKEN" in str(exc.value)
