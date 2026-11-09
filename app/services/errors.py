"""Errors raised by services. `key` is a translation key, shown to the user via t()."""


class ServiceError(Exception):
    def __init__(self, key: str, **values):
        super().__init__(key)
        self.key = key
        self.values = values


class FieldErrors(Exception):
    """Several form problems at once: {field_name: (translation_key, values)}."""

    def __init__(self, errors: dict):
        super().__init__("invalid form")
        self.errors = errors


class UploadError(ServiceError):
    pass


class EmailDeliveryError(ServiceError):
    def __init__(self):
        super().__init__("email.failed")


class OtpError(ServiceError):
    pass


class OtpCooldown(OtpError):
    def __init__(self, seconds: int):
        super().__init__("otp.cooldown", seconds=seconds)
        self.seconds = seconds


class NeedsVerification(Exception):
    def __init__(self, email: str):
        super().__init__(email)
        self.email = email
