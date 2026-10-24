"""Exception types used across Tweetus Deletus."""


class TweetusError(Exception):
    """Base class for all Tweetus Deletus errors."""


class ConfigError(TweetusError):
    """Something is wrong with the configuration file or arguments."""


class AuthError(TweetusError):
    """OAuth / token problems (no token, refresh failed, scope missing)."""


class ApiError(TweetusError):
    """An X API request failed in a way we cannot recover from."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload
