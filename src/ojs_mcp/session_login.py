"""OJS form login and acquiring a CSRF token.

The sequence is forced by ``Validation::login()``, which calls
``$request->checkCSRF()`` (Validation.php:56). A POST without
``csrfToken`` ALWAYS fails and looks identical to a wrong password — 200
with the form page — while still consuming an attempt from
``RateLimitingService``. Hence a GET for the cookie and token first, only
then the POST.

Additional obstacles described in the spec (§3.5, §3.6, §6.2): reCAPTCHA
and ALTCHA on the login page (detected and aborting the sequence BEFORE
sending the password), and a forced password change
(``mustChangePassword``), which looks like a redirect but is not a
successful login.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import NoReturn

import httpx

from .config import SITE_CONTEXT, Config
from .dictionaries import ROLES
from .exceptions import AuthenticationError, LoginError

logger = logging.getLogger(__name__)

# Two-step extraction of the hidden CSRF field — see the docstring of
# `extract_csrf_from_form`: one regex for the whole tag, a second for its
# attributes, so it does not depend on their order.
_TAG_INPUT = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_CSRF_NAME_ATTR = re.compile(r"""name\s*=\s*["']csrfToken["']""", re.IGNORECASE)
_VALUE_ATTR = re.compile(r"""value\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_CURRENT_USER = re.compile(r"pkp\.currentUser\s*=\s*(\{.*?\})\s*;", re.DOTALL)

# Order of attempts: the editorial dashboard, then the reviewer one, then
# the author one — different roles see different dashboards, and
# `{context}/submissions` redirects, so it is not used as a token source.
DASHBOARDS = (
    "dashboard/editorial",
    "dashboard/reviewAssignments",
    "dashboard/mySubmissions",
)

# Methods that in OJS go through `ValidateCsrfToken` — only these get the
# `X-Csrf-Token` header (spec §6.2, step 3). Reads (GET/HEAD) are not
# CSRF-validated and should not carry this header.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Extracting the journal context (`{context}` in
# `/index.php/{context}/api/v1`) from the URL of a production request —
# see `Config.api_root`, the only place that builds this URL shape. This
# lets `SessionAuth` also handle requests to a DIFFERENT journal than
# `OJS_JOURNAL` (the `journal` tool parameter, multi-journal support from
# spec §7), without keeping its own copy of the context per request.
_CONTEXT_FROM_URL = re.compile(r"/index\.php/([^/]+)/api/v1")

# Detecting a bounce back to the login page in `Location` after step 1
# (see `login` below) — MUST match `/login` as its OWN path segment, not
# as an arbitrary substring. `"/login" in location` (the version before
# this fix — review, W5) also caught addresses where "login" is part of
# another word (e.g. `/user/loginHistory`) and incorrectly rejected a
# successful login that ended with such a redirect.
_LOGIN_SEGMENT = re.compile(r"/login(?:$|[/?#])")


def extract_csrf_from_form(html: str) -> str | None:
    """Find the ``csrfToken`` value in a hidden form field.

    Two-step, deliberately INDEPENDENT of attribute order within the
    ``<input>`` tag: first find the whole tag containing a matching
    ``name="csrfToken"``, only then pull ``value`` out of it. Backend
    pages, where we search for a token as a fallback in step 2, do not
    necessarily generate this tag with the same template as the login
    page (``name``/``value`` order, an ``id`` attribute in the middle,
    spaces around ``=``).
    """
    content = html or ""
    for tag in _TAG_INPUT.finditer(content):
        fragment = tag.group(0)
        if not _CSRF_NAME_ATTR.search(fragment):
            continue
        value = _VALUE_ATTR.search(fragment)
        if value:
            return value.group(1)
    return None


def extract_current_user(html: str) -> dict | None:
    """Extract the ``pkp.currentUser = {…};`` literal from a backend page.

    This literal (PKPTemplateManager, ``contexts => ['backend']``) carries
    the session's ``csrfToken`` as well as the user's identity (``id``,
    ``roles``, ``username``) and appears EXCLUSIVELY on dashboard pages,
    not on every OJS page.
    """
    match = _CURRENT_USER.search(html or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError as exc:
        # Not silently swallowed — this means OJS changed the literal's
        # format. We log and return None: the caller has a defined
        # fallback path (extract_csrf_from_form) and an explicit error
        # when both strategies fail.
        logger.warning("pkp.currentUser is not valid JSON: %s", exc)
        return None


def detect_captcha(html: str) -> str | None:
    """Return the name of the CAPTCHA mechanism on the login page, or
    ``None``.

    Detecting CAPTCHA is a matter of security, not convenience: sending a
    password despite CAPTCHA will fail anyway
    (``FormValidatorReCaptcha`` / ``altcha_on_login``), but it will still
    consume an attempt from the login rate limit and send credentials for
    no benefit.
    """
    content = html or ""
    if "g-recaptcha" in content:
        return "reCAPTCHA"
    if "altcha-widget" in content or "altcha_on_login" in content:
        return "ALTCHA"
    return None


def _describe_roles(role_ids: list | None) -> list[str]:
    """Turn numeric role identifiers (Role.php) into readable labels.

    An unknown code gets a fallback description instead of aborting the
    whole sequence — a newer OJS version may have added a role we do not
    yet know about.
    """
    if not role_ids:
        return []
    return [ROLES.get(id_, f"role with code {id_}") for id_ in role_ids]


class SessionAuth(httpx.Auth):
    """Authentication via session cookie and CSRF token (spec §6.2, §6.3).

    PITFALL — RECURSION AND THE ``Accept`` HEADER (a deliberate decision,
    also described in the Task 11 report): the production ``OjsClient``
    (``client.py``) creates its ``httpx.AsyncClient`` with ``auth=self``
    AND with an ``Accept: application/json`` header on the whole client.
    If the login sequence (``login`` — GET/POST against the HTML login and
    dashboard pages) went through THIS SAME client:

    * login requests would go through THIS SAME ``SessionAuth``
      instance's ``async_auth_flow`` — the strategy would try to log in
      while already logging in (recursion);
    * the login/dashboard pages would get a header requesting JSON
      instead of HTML, which could change the OJS response.

    That is why this class keeps its OWN, SEPARATE ``httpx.AsyncClient``
    (``self._login_client``) exclusively for the login sequence and for
    refreshing the CSRF token — no ``auth``, no ``Accept`` header, its own
    cookie jar. This cookie jar (the OJS session) is the only bridge
    between the two clients: ``async_auth_flow`` attaches it to EVERY
    production request before sending.

    Behavior:

    * lazy login — only on the first request, not in the constructor;
    * ``X-Csrf-Token`` is attached EXCLUSIVELY to ``POST``/``PUT``/
      ``PATCH``/``DELETE`` (``_WRITE_METHODS``), never to a read;
    * 401 -> one full re-login (steps 0-2 of ``login``) and a retry; a
      second failure passes through as an error response;
    * 403 on a write -> refresh JUST the CSRF token from the dashboard
      page, WITHOUT logging in again (a 403 with a live session means
      "the token expired/rotated", not "the session is dead" — that
      would be a 401);
    * every failed login attempt consumes a ``RateLimitingService``
      attempt — login is NEVER looped.

    The retry counters (``login_attempts``/``csrf_attempts`` in
    ``async_auth_flow``) live as LOCAL variables inside that method — i.e.
    separate for each call (each request). The shared state (CSRF token,
    cookies) is protected by ``self._lock`` (``asyncio.Lock``) and the
    ``self._generation`` counter: under the lock, we check whether the
    generation has changed since the moment the retry was decided on — if
    it has, another concurrent request has already logged in / refreshed
    the token, and we do not do it a second time. The generation ALSO
    advances on a FAILED attempt (the error goes into ``self._error``) —
    without this, requests waiting on the lock would see an unchanged
    generation and try to log in themselves, exactly the login loop the
    spec warns against (each failure consumes a
    ``RateLimitingService`` attempt). A waiting request that finds the
    generation closed with a stored error gets THE SAME error instead of
    a right to its own attempt.
    """

    # httpx unconditionally reads the ENTIRE body of an intermediate
    # response — see `AsyncClient._send_handling_auth`: `await
    # response.aread()` happens right after the generator
    # (`async_auth_flow` below) decides on another request, regardless of
    # this flag's value (this flag controls ONLY the default
    # `async_auth_flow` implementation that wraps the synchronous
    # `auth_flow` from the base class — we override `async_auth_flow`
    # directly, so httpx never checks it for us). We still set it to
    # `True`: that is an honest description of reality — the response
    # body really is read in this flow, just not because of this flag.
    # The retry decision itself is made earlier anyway, based on
    # `status_code`, available immediately after the headers.
    requires_response_body = True

    def __init__(self, config: Config) -> None:
        self.config = config
        self._csrf: str | None = None
        # The logged-in user's identity (``pkp.currentUser``, with roles
        # translated by ``_describe_roles``) — remembered from the last
        # successful login/token refresh. ``None`` until the (lazy) login
        # has happened. Exposed via the ``user`` property below — see W7
        # (review): ``whoami`` (``tools_read.py``) uses it on the session
        # path, instead of reporting "not implemented" even though this
        # data is already fetched in ``login()``.
        self._user: dict | None = None
        self._generation = 0
        # The error from the last CLOSED (successful or failed) login /
        # token-refresh attempt — see the ``self._generation`` paragraph
        # in the class docstring. ``None`` when that attempt succeeded.
        self._error: Exception | None = None
        self._lock = asyncio.Lock()
        # Its own, separate client — see the recursion paragraph above.
        # This same object (and its cookie jar) handles the whole login
        # sequence and CSRF token refreshes.
        self._login_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0)
        )

    async def aclose(self) -> None:
        """Close the own login client.

        This client (``self._login_client``) is a resource of THIS
        strategy, not of ``OjsClient`` — `OjsClient.aclose()` calls this
        method if the strategy exposes it
        (``getattr(auth, "aclose", None)``), so the process does not end
        up with a second, unmanaged httpx connection pool next to the
        production client.
        """
        await self._login_client.aclose()

    @property
    def user(self) -> dict | None:
        """The logged-in user's identity, if already known.

        ``None`` until some request has forced a (lazy) login — see
        ``async_auth_flow``. After a successful login: the
        ``pkp.currentUser`` dict (among others ``id``, ``username``,
        ``roles``, ``role_names``) — see ``login``/``_describe_roles``.
        """
        return self._user

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> NoReturn:  # pragma: no cover — see raise below
        """Refuses to be used with a SYNCHRONOUS client (``httpx.Client``).

        The base ``httpx.Auth`` class's default implementation silently
        does NOT call ``async_auth_flow`` for synchronous clients — it
        calls ``sync_auth_flow``, and its default variant (when not
        overridden) simply passes the request through WITHOUT ANY
        authentication: no session cookies, no ``X-Csrf-Token``. That
        silent omission of authentication would be much worse than an
        explicit error — hence this override, even though ``OjsClient``
        (the only production user of this class) always uses
        ``httpx.AsyncClient``.
        """
        raise RuntimeError(
            "SessionAuth requires httpx.AsyncClient — the login sequence "
            "does I/O and uses asyncio.Lock. Use httpx.AsyncClient instead "
            "of httpx.Client (synchronous)."
        )

    def _context(self, request: httpx.Request) -> str:
        """Determine the journal context from the request's URL (falling
        back to config).

        The SITE-level context (``SITE_CONTEXT``, ``"index"``) is NEVER
        used for logging in — dashboard pages (``DASHBOARDS``) are
        per-journal, they do not exist at the site level. Requests at
        that level (e.g. ``catalog.journals()`` without ``OJS_JOURNAL``,
        or an explicit ``journal="index"`` on a tool) still have to log
        in to SOME journal — we then use ``config.journal``.
        """
        match = _CONTEXT_FROM_URL.search(request.url.path)
        context = match.group(1) if match else None
        if context and context != SITE_CONTEXT:
            return context
        if self.config.journal:
            return self.config.journal
        raise AuthenticationError(
            "Could not determine a journal context to log in with — the "
            f"request address ({request.url}) points to the site level or "
            "has no recognizable context, and OJS_JOURNAL is not set. "
            "Session login requires a specific journal."
        )

    async def _login(self, context: str) -> None:
        """Full login sequence (steps 0-2 of ``login``)."""
        result = await login(self._login_client, self.config, context)
        self._csrf = result["csrf"]
        # Remember the identity (W7) — `login()` already computes it from
        # `pkp.currentUser`, formerly discarded here.
        self._user = result["user"]

    async def _refresh_csrf(self, context: str) -> None:
        """Refresh just the CSRF token from the dashboard page — without
        logging in again.

        Used after a 403 on a write: the session is still alive (OJS
        would otherwise return 401), only the CSRF token failed. Logging
        in again in this situation would be unnecessary and would consume
        a ``RateLimitingService`` attempt.
        """
        root = f"{self.config.base_url}/index.php/{context}"
        result = await _csrf_from_dashboard(self._login_client, root)
        if result is None:
            raise LoginError(
                "Could not refresh the CSRF token from any dashboard page "
                "— the session may have expired between requests. Try "
                "again; if the error repeats, use OJS_API_TOKEN."
            )
        self._csrf, user = result
        # (W7) This dashboard page may have been served only by the
        # fallback strategy (`extract_csrf_from_form`), which does not
        # carry an identity — `user` may then be `None`. In that case we
        # do not overwrite the identity known from the previous login;
        # only when this page actually returned one.
        if user is not None:
            self._user = user
        logger.info("Refreshed the session CSRF token (without logging in again).")

    async def _ensure_generation(
        self, generation_before: int, context: str, *, csrf_only: bool
    ) -> None:
        """Log in / refresh the token — unless another request already did.

        See the ``self._generation`` paragraph in the class docstring:
        under the lock, we check whether the shared state has changed
        since the caller decided to retry. If it has — the generation
        ``generation_before`` is already CLOSED (successfully or not) by
        another request; on success we use its effect, on failure we
        raise THE SAME stored error instead of trying again — otherwise
        every waiting request would consume its own attempt from the
        ``RateLimitingService`` limit, exactly the login loop the spec
        forbids.

        :raises Exception: the same exception that the login attempt
            failed with (our own, or someone else's, closed under the
            same generation).
        """
        async with self._lock:
            if self._generation != generation_before:
                if self._error is not None:
                    raise self._error
                return
            try:
                if csrf_only:
                    await self._refresh_csrf(context)
                else:
                    await self._login(context)
            except Exception as exc:
                # The generation also closes on FAILURE (see the method
                # docstring) — without this, waiting requests would try
                # to log in themselves.
                self._error = exc
                self._generation += 1
                raise
            self._error = None
            self._generation += 1

    def _prepare(self, request: httpx.Request) -> None:
        """Attach session cookies and (only for writes) ``X-Csrf-Token``."""
        # `http.cookiejar.CookieJar.add_cookie_header`, on which
        # `Cookies.set_cookie_header` is based, has a condition `if not
        # request.has_header("Cookie")` — it does NOT overwrite an
        # existing `Cookie` header. Without this `pop`, a retry after
        # logging in again (the second loop iteration in
        # `async_auth_flow`) would go out with the cookie from before the
        # re-login — i.e. with a DEAD session, and the whole 401 ->
        # re-login -> retry path would be functionally dead.
        request.headers.pop("Cookie", None)
        self._login_client.cookies.set_cookie_header(request)
        if request.method in _WRITE_METHODS and self._csrf:
            request.headers["X-Csrf-Token"] = self._csrf

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Attach session authentication and handle retries (spec §6.3)."""
        context = self._context(request)

        if self._csrf is None:
            await self._ensure_generation(self._generation, context, csrf_only=False)

        login_attempts = 0
        csrf_attempts = 0
        while True:
            self._prepare(request)
            generation_used = self._generation
            # A write retry (POST/PUT/PATCH/DELETE) sends the SAME
            # `request` object a second time — it assumes its body
            # stream is replayable. That is always true today:
            # `OjsClient` calls `httpx.AsyncClient.request(...,
            # json=body)`, and a body built from `json=` is a simple
            # in-memory byte `ByteStream` (iterable multiple times), not
            # a one-shot stream from a file — the same pattern used by
            # the builtin `httpx.DigestAuth`.
            response = yield request

            if response.status_code == 401 and login_attempts == 0:
                login_attempts += 1
                await self._ensure_generation(generation_used, context, csrf_only=False)
                continue

            if (
                response.status_code == 403
                and request.method in _WRITE_METHODS
                and csrf_attempts == 0
            ):
                csrf_attempts += 1
                await self._ensure_generation(generation_used, context, csrf_only=True)
                continue

            return


async def _csrf_from_dashboard(
    client: httpx.AsyncClient, root: str
) -> tuple[str, dict | None] | None:
    """Find the session CSRF token on the first available dashboard page.

    Shared implementation of Step 2 from spec §6.2, used both by a full
    login (``login`` below) and by ``SessionAuth._refresh_csrf`` (§6.3 —
    refreshing just the token without logging in again, when the session
    is still alive but the CSRF token has expired or rotated). Different
    roles see different dashboards, hence trying the addresses in
    ``DASHBOARDS`` in turn.

    :returns: ``(csrf, user)`` or ``None`` when no dashboard page returned
        a token. Does not raise — the error message differs depending on
        the caller (login vs. just refreshing the token), so the decision
        on the message content and on raising ``LoginError`` is left to
        the caller.
    """
    for dashboard in DASHBOARDS:
        page = await client.get(f"{root}/{dashboard}", follow_redirects=True)
        # A session can be dead despite a successful login (e.g. it
        # expired between requests). OJS then redirects the dashboard to
        # `/login`, and httpx follows that automatically
        # (`follow_redirects=True` above is deliberate — different
        # dashboards are real redirects to one another). The login page,
        # however, has its OWN hidden csrfToken field — that is a token
        # for (another) login, not a session token. We reject this page
        # BEFORE reaching for the fallback extraction, so as not to
        # return a silent false success.
        if page.status_code != 200 or "/login" in str(page.url):
            continue
        user = extract_current_user(page.text)
        if user is not None:
            user["role_names"] = _describe_roles(user.get("roles"))
        if user and user.get("csrfToken"):
            return user["csrfToken"], user
        # Fallback strategy: since the pkp.currentUser literal failed or
        # did not carry a token, try the hidden csrfToken field on the
        # same page before giving up on this dashboard page.
        fallback = extract_csrf_from_form(page.text)
        if fallback:
            return fallback, user
    return None


async def login(client: httpx.AsyncClient, config: Config, context: str) -> dict:
    """Run the full form-login sequence and return the identity.

    Steps (reasoning in spec §3.5, §3.6, §6.2):

    0. GET the login page — session cookie, a CSRF token for the login
       itself, CAPTCHA detection.
    1. POST the login data together with that token. Success is
       EXCLUSIVELY a redirect (3xx) whose target does not lead back to
       the login page or to a forced password change.
    2. GET the dashboard page (in order: editorial, reviewer, author) to
       extract the SESSION's CSRF token (different from the one from
       step 0) and the user's identity from ``pkp.currentUser``.

    :returns: ``{"csrf": str, "user": dict | None}``.
    :raises LoginError: when the instance has CAPTCHA, there is no CSRF
        token on the login page, the login is rejected, or no dashboard
        page returns the session's CSRF token.
    """
    if not config.username or not config.password:
        raise LoginError(
            "No login or password (OJS_USERNAME/OJS_PASSWORD) — there is "
            "nothing to log in with. Set both variables, or use "
            "OJS_API_TOKEN."
        )

    root = f"{config.base_url}/index.php/{context}"

    # Step 0 — session cookie and CSRF token from before login.
    # `follow_redirects=True` here is explicit and DESIRED (e.g.
    # `force_login_ssl` redirects to https) — unlike step 1 below, where
    # redirects must be observed raw. Do not unify these two.
    resp = await client.get(f"{root}/login", follow_redirects=True)
    mechanism = detect_captcha(resp.text)
    if mechanism:
        raise LoginError(
            f"The instance has {mechanism} on the login page, so logging "
            "in with a login and password is impossible (and would fail "
            "anyway, while consuming a login-limit attempt). Use "
            "OJS_API_TOKEN — generate a token in your OJS profile."
        )
    initial_csrf = extract_csrf_from_form(resp.text)
    if not initial_csrf:
        raise LoginError(
            "No csrfToken field found on the login page. OJS requires it "
            "when logging in (Validation::login), so we cannot proceed. "
            "Check that OJS_BASE_URL points to a working OJS 3.5+ "
            "instance, or use OJS_API_TOKEN."
        )

    # Step 1 — the actual login. `follow_redirects=False` is forced here
    # regardless of client settings: we need to see the redirect's
    # `Location` header, not the page it points to.
    resp = await client.post(
        f"{root}/login/signIn",
        data={
            "csrfToken": initial_csrf,
            "username": config.username,
            "password": config.password,
            "remember": "1",
        },
        follow_redirects=False,
    )
    # We require a NON-EMPTY Location: a 3xx without this header (a cached
    # 304, an application firewall, an unusual proxy) is not a successful
    # login, just missing information — it must not be passed through as
    # success by default. We check for a password change explicitly with
    # its own condition — today it is also caught by the "/login" segment
    # match (the address is `/login/changePassword/{user}`), but that is
    # a coincidence, not the deliberate safeguard the spec requires in
    # §6.2. `_LOGIN_SEGMENT` (not a bare substring — see W5, review)
    # matches "/login" EXCLUSIVELY as its own path segment, so a valid
    # target address that contains "login" as part of another word (e.g.
    # `/user/loginHistory`) is not incorrectly rejected here.
    location = resp.headers.get("location", "")
    ok = (
        300 <= resp.status_code < 400
        and bool(location)
        and not _LOGIN_SEGMENT.search(location)
        and "changepassword" not in location.lower()
    )
    if not ok:
        raise LoginError(
            "Login rejected. OJS does not distinguish the reason, so "
            "three are possible: a wrong password, an exhausted login "
            "attempt limit (RateLimitingService), or a forced password "
            "change on this account. Check the account in a browser; an "
            "API token (OJS_API_TOKEN) sidesteps this problem."
        )

    # Step 2 — the session's CSRF token and identity from the first
    # available backend page (implementation shared with refreshing the
    # token without logging in — see `_csrf_from_dashboard`).
    result = await _csrf_from_dashboard(client, root)
    if result is None:
        raise LoginError(
            "Logged in, but could not read a CSRF token from any "
            "dashboard page (neither from pkp.currentUser nor from a "
            "hidden field). Reads will work with this session's token, "
            "writes will not. Use OJS_API_TOKEN if you need to modify "
            "data."
        )
    csrf, user = result
    if user and user.get("csrfToken"):
        logger.info(
            "Logged in as %r (roles: %s).",
            user.get("username"),
            ", ".join(user["role_names"]) or "none",
        )
    return {"csrf": csrf, "user": user}
