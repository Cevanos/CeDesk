"""Errors a service raises; the API turns each into one HTTP status."""


class ServiceError(Exception):
    status = 400
    code: str | None = None  # the answer's error code; None: the class name

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotSignedIn(ServiceError):
    status = 401


class Forbidden(ServiceError):
    status = 403


class IpNotAllowed(Forbidden):
    """403 with code `IpNotAllowed`: the address isn't on the workspace's IP list (S4)."""


class PasswordExpired(Forbidden):
    """403 with code `PasswordExpired`: the member must choose a new password (S3)."""


class TwoFactorRequired(Forbidden):
    """403 `TwoFactorRequired`: send the authenticator code to POST /auth/2fa (S2)."""


class TwoFactorSetupRequired(Forbidden):
    """403 `TwoFactorSetupRequired`: the workspace requires 2FA; set it up first (S2)."""


class SsoRequired(Forbidden):
    """403 `SsoRequired`: the workspace signs in through its identity provider (S1)."""


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class Gone(ServiceError):
    status = 410


class TooLarge(ServiceError):
    status = 413


class Invalid(ServiceError):
    status = 422


class OverPlan(ServiceError):
    """Over a cloud plan limit (md O4): agents, tickets this month or storage."""

    status = 402


class TooManyAttempts(ServiceError):
    status = 429

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SlugTaken(Conflict):
    """409 `slug_taken`: another workspace has this address (self-service sign-up)."""

    code = "slug_taken"


class BadCode(Invalid):
    """422 `bad_code`: the sign-up code is wrong; the person may try again."""

    code = "bad_code"


class CodeExpired(Gone):
    """410 `code_expired`: the sign-up code ran out of time or tries; resend a new one."""

    code = "code_expired"


class SignupExpired(Gone):
    """410 `signup_expired`: unknown, replaced or over a day old; start the sign-up again."""

    code = "signup_expired"


class CaptchaFailed(Forbidden):
    """403 `captcha_failed`: the sign-up's CAPTCHA answer was missing or not accepted."""

    code = "captcha_failed"


class Unavailable(ServiceError):
    """A service we depend on (Cevanos) is down or slow: the caller tries again later."""

    status = 503


class CevanosNotConnected(Conflict):
    """409 `cevanos_not_connected`: an AI feature (md 16) without a Cevanos connection."""

    code = "cevanos_not_connected"


class CevanosScopeMissing(Conflict):
    """409 `cevanos_scope_missing`: the workspace's Cevanos key lacks the scope this needs."""

    code = "cevanos_scope_missing"


class CevanosRefused(Conflict):
    """409 `cevanos_refused`: Cevanos refused the key or the request (not a retry)."""

    code = "cevanos_refused"
