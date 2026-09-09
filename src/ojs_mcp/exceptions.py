"""Domain exceptions for the server.

Mapping an OJS response to these types happens on the triple (HTTP status,
authentication path in use, method) — NEVER on the content of the `error`
field. OJS middleware returns `{"error": __('key')}`, i.e. text TRANSLATED
into the instance's locale; matching on the key would not work.
"""

from __future__ import annotations


class OjsError(RuntimeError):
    """Base for anything that went wrong on the OJS side."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Verbatim `error` content from OJS — context for the user, not a key.
        self.detail = detail


class AuthenticationError(OjsError):
    """401 — no user, or no role. OJS does not distinguish between the two."""


class AuthorizationError(OjsError):
    """403 — requesting someone else's data, or an invalid CSRF token on a write."""


class ValidationError(OjsError):
    """400 or 422 with a field object — input validation errors.

    OJS returns 422 interchangeably with 400 whenever it raises a
    `ValidationException` instead of a plain 400 error — see
    `OjsClient._to_error` (`client.py`), which maps both statuses to this
    same exception.
    """


class NotFoundError(OjsError):
    """404 — unknown journal, or an endpoint outside this OJS version."""


class ServerConfigError(OjsError):
    """500 — most commonly a missing `api_key_secret` in config.inc.php."""


class LoginError(OjsError):
    """Form login failed (password, CAPTCHA, rate limit)."""


class InputError(ValueError):
    """The input supplied by the caller is invalid — a name outside a
    dictionary (``dictionaries.to_values``), a field outside the allowed
    list (``tools_write._check_editable_fields``), a path outside
    ``api/v1`` (``passthrough.validate_path``), or a missing required
    value (an empty announcement title, an empty field dict). NOT a
    programming defect.

    Subclass of ``ValueError`` — existing code that catches
    ``except ValueError`` (and tests using
    ``pytest.raises(ValueError)``) keeps working unchanged. Reason for a
    separate type: ``mcp_errors.DOMAIN_ERRORS`` must be able to tell "a
    message deliberately written for the reader" apart from an
    accidental ``ValueError`` that is actually a programming defect
    (e.g. a failed type conversion buried deep inside some call) — that
    kind would otherwise reach the model as if it were a deliberate
    message. If ``DOMAIN_ERRORS`` caught bare ``ValueError``, the
    guarantee would rest on COINCIDENCE (today nothing else happens to
    raise it), not on TYPE — a defect flagged in the Round 1 review of
    Task 13.
    """


class WritesDisabledError(PermissionError):
    """The call tries to change data while the server is running in
    read-only mode (``OJS_ALLOW_WRITES`` unset) — see
    ``passthrough.request_impl``.

    Subclass of ``PermissionError`` for the same reason ``InputError``
    is a subclass of ``ValueError`` — see its docstring.
    """
