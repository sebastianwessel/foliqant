"""Shared validation for trusted deployment HTTP endpoints."""

from urllib.parse import unquote, urlsplit


def validate_http_endpoint(value: str, *, allow_insecure_http: bool) -> None:
    """Reject ambiguous endpoints and require HTTPS unless explicitly relaxed."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid HTTP endpoint") from None
    if (
        parsed.scheme not in ({"https", "http"} if allow_insecure_http else {"https"})
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "\\" in value
        or any(segment in {".", ".."} for segment in unquote(parsed.path).split("/"))
        or (port is not None and port == 0)
    ):
        raise ValueError("invalid HTTP endpoint")
