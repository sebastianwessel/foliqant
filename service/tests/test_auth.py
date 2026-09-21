"""Authentication policies verify credentials without leaking or trusting token grants."""

import asyncio
import base64
import json
import time
from collections.abc import Callable

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import TypeAdapter, ValidationError

from foliqant.adapters.auth import (
    BearerAuthenticator,
    DevelopmentAuthenticator,
    JwtAuthenticator,
)
from foliqant.contracts.auth import AuthConfig, BearerAuthConfig, JwtAuthConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity


def _jwt_config(**overrides: object) -> JwtAuthConfig:
    return JwtAuthConfig.model_validate(
        {
            "type": "jwt",
            "issuer": "https://issuer.example/",
            "audience": "foliqant-service",
            "algorithms": ["RS256"],
            "jwks_url": "https://issuer.example/.well-known/jwks.json",
            "workflows": ["classify", "route_case"],
            **overrides,
        },
        strict=True,
    )


def _rsa_key(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, object]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    value = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    value.update({"kid": kid, "alg": "RS256", "use": "sig", "key_ops": ["verify"]})
    return private, value


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    return {
        "iss": "https://issuer.example/",
        "aud": "foliqant-service",
        "iat": now,
        "exp": now + 300,
        "tenant_id": "tenant-a",
        "sub": "person-a",
        **overrides,
    }


def _token(
    private: rsa.RSAPrivateKey,
    kid: str,
    *,
    claims: dict[str, object] | None = None,
) -> str:
    return jwt.encode(claims or _claims(), private, algorithm="RS256", headers={"kid": kid})


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_raw(private: rsa.RSAPrivateKey, header: bytes, payload: bytes) -> str:
    signing_input = f"{_b64(header)}.{_b64(payload)}".encode()
    signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{_b64(signature)}"


def test_auth_config_is_closed_discriminated_and_rejects_explicit_null_identity() -> None:
    adapter = TypeAdapter(AuthConfig)
    assert adapter.validate_python({"type": "development"}, strict=True).type == "development"
    bearer = adapter.validate_python(
        {
            "type": "bearer",
            "bindings": {"operator": {"token_env": "SERVICE_TOKEN", "workflows": ["classify"]}},
        },
        strict=True,
    )
    assert isinstance(bearer, BearerAuthConfig)

    invalid: list[dict[str, object]] = [
        {
            "type": "bearer",
            "bindings": {
                "operator": {
                    "token_env": "SERVICE_TOKEN",
                    "tenant_id": None,
                    "workflows": ["classify"],
                }
            },
        },
        {
            "type": "bearer",
            "bindings": {
                "operator": {
                    "token_env": "SERVICE_TOKEN",
                    "principal_id": None,
                    "workflows": ["classify"],
                }
            },
        },
        {
            "type": "jwt",
            "issuer": "http://issuer.example/",
            "audience": "foliqant-service",
            "algorithms": ["RS256"],
            "jwks_url": "https://issuer.example/jwks",
            "workflows": ["classify"],
        },
        {
            "type": "jwt",
            "issuer": "https://issuer.example/",
            "audience": "foliqant-service",
            "algorithms": ["HS256"],
            "jwks_url": "https://issuer.example/jwks",
            "workflows": ["classify"],
        },
        {"type": "development", "workflows": ["classify"]},
    ]
    for value in invalid:
        with pytest.raises(ValidationError):
            adapter.validate_python(value, strict=True)


@pytest.mark.asyncio
async def test_bearer_snapshots_secrets_and_returns_only_configured_grants() -> None:
    environment = {"SERVICE_TOKEN": "opaque-token"}
    config = BearerAuthConfig.model_validate(
        {
            "type": "bearer",
            "bindings": {
                "operator": {
                    "token_env": "SERVICE_TOKEN",
                    "principal_id": "operator-a",
                    "workflows": ["classify"],
                }
            },
        },
        strict=True,
    )
    authenticator = BearerAuthenticator(config, environment=environment)
    environment["SERVICE_TOKEN"] = "changed-after-startup"

    for scheme in ("Bearer", "bearer", "bEaReR"):
        caller = await authenticator.authenticate(f"{scheme} opaque-token")
        assert caller.identity == Identity(principal_id="operator-a")
        assert caller.workflows == frozenset({"classify"})
    assert "opaque-token" not in repr(authenticator)
    for authorization in (None, "Basic opaque-token", "Bearer  opaque-token", "Bearer wrong"):
        with pytest.raises(ServiceError) as raised:
            await authenticator.authenticate(authorization)
        assert raised.value.code is ErrorCode.UNAUTHENTICATED
        assert "opaque-token" not in str(raised.value)


def test_bearer_rejects_missing_invalid_and_duplicate_startup_credentials() -> None:
    config = BearerAuthConfig.model_validate(
        {
            "type": "bearer",
            "bindings": {
                "one": {"token_env": "TOKEN_ONE", "workflows": ["classify"]},
                "two": {"token_env": "TOKEN_TWO", "workflows": ["classify"]},
            },
        },
        strict=True,
    )
    for environment in (
        {},
        {"TOKEN_ONE": " ", "TOKEN_TWO": "valid"},
        {"TOKEN_ONE": "same", "TOKEN_TWO": "same"},
    ):
        with pytest.raises(ServiceError) as raised:
            BearerAuthenticator(config, environment=environment)
        assert raised.value.code is ErrorCode.INVALID_CONFIGURATION
        assert "same" not in str(raised.value)


@pytest.mark.asyncio
async def test_development_authenticator_never_interprets_supplied_credentials() -> None:
    authenticator = DevelopmentAuthenticator(frozenset({"classify"}))
    caller = await authenticator.authenticate(None)
    assert caller.identity == Identity()
    assert caller.workflows == frozenset({"classify"})
    with pytest.raises(ServiceError, match="Authentication is required"):
        await authenticator.authenticate("Bearer anything")


@pytest.mark.asyncio
async def test_jwt_verifies_signature_policy_and_independently_optional_identity() -> None:
    private, public = _rsa_key("current")
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, request=request, json={"keys": [public]})

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    principal_only = _claims()
    principal_only.pop("tenant_id")
    caller = await authenticator.authenticate(
        f"bEaReR {_token(private, 'current', claims=principal_only)}"
    )
    assert caller.identity == Identity(principal_id="person-a")
    assert caller.workflows == frozenset({"classify", "route_case"})

    no_identity = _claims()
    no_identity.pop("tenant_id")
    no_identity.pop("sub")
    caller = await authenticator.authenticate(
        f"Bearer {_token(private, 'current', claims=no_identity)}"
    )
    assert caller.identity == Identity()
    assert len(requests) == 1
    assert requests[0].url == httpx2.URL("https://issuer.example/.well-known/jwks.json")
    assert requests[0].method == "GET"
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": int(time.time()) - 1},
        {"iat": int(time.time()) + 300},
        {"aud": "other-service"},
        {"iss": "https://other.example/"},
        {"tenant_id": None},
        {"sub": "bad\x00identity"},
        {"iat": True},
        {"nbf": True},
    ],
)
async def test_jwt_rejects_invalid_time_scope_and_identity_claims(
    claim_overrides: dict[str, object],
) -> None:
    private, public = _rsa_key("current")
    client = _client(lambda request: httpx2.Response(200, request=request, json={"keys": [public]}))
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    token = _token(private, "current", claims=_claims(**claim_overrides))
    with pytest.raises(ServiceError) as raised:
        await authenticator.authenticate(f"Bearer {token}")
    assert raised.value.code is ErrorCode.UNAUTHENTICATED
    assert token not in str(raised.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_jwt_rejects_missing_required_claims_and_header_confusion() -> None:
    private, public = _rsa_key("current")
    other_private, _ = _rsa_key("other")
    client = _client(lambda request: httpx2.Response(200, request=request, json={"keys": [public]}))
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    missing_exp = _claims()
    missing_exp.pop("exp")
    values = [
        _token(private, "current", claims=missing_exp),
        _token(other_private, "current"),
        jwt.encode(_claims(), private, algorithm="PS256", headers={"kid": "current"}),
        jwt.encode(
            _claims(),
            private,
            algorithm="RS256",
            headers={"kid": "current", "jku": "https://evil.example/jwks"},
        ),
        _signed_raw(
            private,
            b'{"alg":"RS256","alg":"RS256","kid":"current"}',
            json.dumps(_claims(), separators=(",", ":")).encode(),
        ),
    ]
    for token in values:
        with pytest.raises(ServiceError) as raised:
            await authenticator.authenticate(f"Bearer {token}")
        assert raised.value.code is ErrorCode.UNAUTHENTICATED
    await client.aclose()


@pytest.mark.asyncio
async def test_unknown_kid_refreshes_once_and_accepts_rotated_key() -> None:
    first_private, first_public = _rsa_key("first")
    second_private, second_public = _rsa_key("second")
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        keys = [first_public] if calls == 1 else [second_public]
        return httpx2.Response(200, request=request, json={"keys": keys})

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    assert (
        await authenticator.authenticate(f"Bearer {_token(first_private, 'first')}")
    ).identity.tenant_id == "tenant-a"
    assert (
        await authenticator.authenticate(f"Bearer {_token(second_private, 'second')}")
    ).identity.tenant_id == "tenant-a"
    assert calls == 2
    unknown = _token(second_private, "unknown")
    for _ in range(2):
        with pytest.raises(ServiceError) as raised:
            await authenticator.authenticate(f"Bearer {unknown}")
        assert raised.value.code is ErrorCode.UNAUTHENTICATED
    # The successful unknown-kid rotation consumed the refresh cooldown, so a
    # second unknown key cannot trigger another endpoint request immediately.
    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_jwks_key_without_alg_uses_only_configured_compatible_algorithm() -> None:
    private, public = _rsa_key("current")
    del public["alg"]
    client = _client(lambda request: httpx2.Response(200, request=request, json={"keys": [public]}))
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    caller = await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
    assert caller.identity == Identity("tenant-a", "person-a")
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_first_use_shares_one_bounded_jwks_refresh() -> None:
    private, public = _rsa_key("current")
    calls = 0

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx2.Response(200, request=request, json={"keys": [public]})

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    token = _token(private, "current")
    callers = await asyncio.gather(
        *(authenticator.authenticate(f"Bearer {token}") for _ in range(20))
    )
    assert calls == 1
    assert {caller.identity for caller in callers} == {Identity("tenant-a", "person-a")}
    await authenticator.aclose()  # injected clients remain host-owned
    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_failed_refresh_is_cooled_down_without_serial_stampede() -> None:
    private, _ = _rsa_key("current")
    calls = 0

    async def unavailable(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx2.Response(503, request=request)

    client = _client(unavailable)
    authenticator = JwtAuthenticator(
        _jwt_config(request_timeout=0.1, jwks_refresh_cooldown=1.0),
        http_client=client,
    )
    token = _token(private, "current")
    results = await asyncio.gather(
        *(authenticator.authenticate(f"Bearer {token}") for _ in range(20)),
        return_exceptions=True,
    )
    assert calls == 1
    assert all(
        isinstance(result, ServiceError)
        and result.code is ErrorCode.DEPENDENCY_FAILURE
        and result.retryable
        for result in results
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_jwks_lock_wait_is_inside_the_authentication_timeout() -> None:
    private, public = _rsa_key("current")
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, request=request, json={"keys": [public]})

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(request_timeout=0.01), http_client=client)
    await authenticator._cache_lock.acquire()
    try:
        with pytest.raises(ServiceError) as raised:
            await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
        assert raised.value.code is ErrorCode.TIMEOUT
        assert raised.value.retryable is True
        assert calls == 0
    finally:
        authenticator._cache_lock.release()
    caller = await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
    assert caller.identity == Identity("tenant-a", "person-a")
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_jwks_response_cleanup_is_inside_the_authentication_timeout() -> None:
    private, public = _rsa_key("current")
    close_started = asyncio.Event()
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        response = httpx2.Response(200, request=request, json={"keys": [public]})

        async def hanging_close() -> None:
            close_started.set()
            await asyncio.Event().wait()

        response.aclose = hanging_close
        return response

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(request_timeout=0.01), http_client=client)
    with pytest.raises(ServiceError) as raised:
        await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
    assert close_started.is_set()
    assert raised.value.code is ErrorCode.TIMEOUT
    assert raised.value.retryable is True
    with pytest.raises(ServiceError) as cooled_down:
        await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
    assert cooled_down.value.code is ErrorCode.DEPENDENCY_FAILURE
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_stalled_jwks_body_and_cleanup_share_one_deadline() -> None:
    private, _ = _rsa_key("current")
    body_started = asyncio.Event()
    close_started = asyncio.Event()

    class StalledStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            body_started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self) -> None:
            close_started.set()
            await asyncio.Event().wait()

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            stream=StalledStream(),
        )

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(request_timeout=0.01), http_client=client)
    with pytest.raises(ServiceError) as raised:
        await asyncio.wait_for(
            authenticator.authenticate(f"Bearer {_token(private, 'current')}"),
            timeout=0.2,
        )
    assert body_started.is_set()
    assert close_started.is_set()
    assert raised.value.code is ErrorCode.TIMEOUT
    assert raised.value.retryable is True
    await client.aclose()


@pytest.mark.asyncio
async def test_jwks_cleanup_timeout_preserves_active_dependency_failure() -> None:
    private, _ = _rsa_key("current")
    close_started = asyncio.Event()

    def respond(request: httpx2.Request) -> httpx2.Response:
        response = httpx2.Response(503, request=request)

        async def hanging_close() -> None:
            close_started.set()
            await asyncio.Event().wait()

        response.aclose = hanging_close
        return response

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(request_timeout=0.01), http_client=client)
    with pytest.raises(ServiceError) as raised:
        await asyncio.wait_for(
            authenticator.authenticate(f"Bearer {_token(private, 'current')}"),
            timeout=0.2,
        )
    assert close_started.is_set()
    assert raised.value.code is ErrorCode.DEPENDENCY_FAILURE
    assert raised.value.retryable is True
    await client.aclose()


@pytest.mark.asyncio
async def test_first_valid_jwks_without_requested_key_uses_unknown_key_cooldown() -> None:
    requested_private, _ = _rsa_key("requested")
    _, published = _rsa_key("published")
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, request=request, json={"keys": [published]})

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(jwks_refresh_cooldown=1.0), http_client=client)
    token = _token(requested_private, "requested")
    for _ in range(2):
        with pytest.raises(ServiceError) as raised:
            await authenticator.authenticate(f"Bearer {token}")
        assert raised.value.code is ErrorCode.UNAUTHENTICATED
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_jwt_callers_do_not_share_identity() -> None:
    private, public = _rsa_key("current")
    client = _client(lambda request: httpx2.Response(200, request=request, json={"keys": [public]}))
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    tenant_only = _claims(tenant_id="tenant-one")
    tenant_only.pop("sub")
    principal_only = _claims(sub="person-two")
    principal_only.pop("tenant_id")
    first, second = await asyncio.gather(
        authenticator.authenticate(f"Bearer {_token(private, 'current', claims=tenant_only)}"),
        authenticator.authenticate(f"Bearer {_token(private, 'current', claims=principal_only)}"),
    )
    assert first.identity == Identity(tenant_id="tenant-one")
    assert second.identity == Identity(principal_id="person-two")
    await client.aclose()


@pytest.mark.asyncio
async def test_jwks_failures_are_bounded_and_safe() -> None:
    private, _ = _rsa_key("current")
    token = _token(private, "current")

    def oversized(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=b"{" + b"x" * 400 + b"}",
        )

    client = _client(oversized)
    authenticator = JwtAuthenticator(_jwt_config(max_jwks_bytes=256), http_client=client)
    with pytest.raises(ServiceError) as raised:
        await authenticator.authenticate(f"Bearer {token}")
    assert raised.value.code is ErrorCode.DEPENDENCY_FAILURE
    assert raised.value.retryable is True
    assert token not in str(raised.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_jwks_fetch_cancellation_propagates() -> None:
    private, _ = _rsa_key("current")
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(request: httpx2.Request) -> httpx2.Response:
        started.set()
        await release.wait()
        return httpx2.Response(500, request=request)

    client = _client(wait_for_release)
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    task = asyncio.create_task(authenticator.authenticate(f"Bearer {_token(private, 'current')}"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await client.aclose()


@pytest.mark.asyncio
async def test_owned_jwks_client_has_explicit_bounded_close() -> None:
    authenticator = JwtAuthenticator(_jwt_config())
    assert not authenticator._client.is_closed
    await authenticator.aclose()
    assert authenticator._client.is_closed


def test_jwks_key_type_confusion_is_rejected_as_safe_dependency_failure() -> None:
    _, public = _rsa_key("current")
    public["kty"] = "oct"
    config = _jwt_config()

    async def check() -> None:
        private, _ = _rsa_key("unused")
        client = _client(
            lambda request: httpx2.Response(200, request=request, json={"keys": [public]})
        )
        authenticator = JwtAuthenticator(config, http_client=client)
        with pytest.raises(ServiceError) as raised:
            await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
        assert raised.value.code is ErrorCode.DEPENDENCY_FAILURE
        await client.aclose()

    asyncio.run(check())


async def test_jwks_requests_identity_encoding_and_rejects_unexpected_compression():
    private, _ = _rsa_key("current")
    closed = False

    class UnreadStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("unexpected compressed content must not be consumed")
            yield b"unreachable"

        async def aclose(self):
            nonlocal closed
            closed = True

    def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=UnreadStream(),
        )

    client = _client(respond)
    authenticator = JwtAuthenticator(_jwt_config(), http_client=client)
    with pytest.raises(ServiceError) as caught:
        await authenticator.authenticate(f"Bearer {_token(private, 'current')}")
    assert caught.value.code is ErrorCode.DEPENDENCY_FAILURE
    assert closed
    await client.aclose()
