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


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class Invalid(ServiceError):
    status = 422
