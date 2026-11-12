"""Errors a service raises; the API turns each into one HTTP status."""


class ServiceError(Exception):
    status = 400

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


class TooManyAttempts(ServiceError):
    status = 429

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(ServiceError):
    """A service we depend on (Cevanos) is down or slow: the caller tries again later."""

    status = 503
