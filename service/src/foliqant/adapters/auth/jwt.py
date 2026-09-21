"""Native-async JWT authentication with a bounded, fixed-origin JWKS cache."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
from collections.abc import Mapping
from typing import Any, cast

import httpx2
import jwt

from foliqant.contracts.auth import JwtAlgorithm, JwtAuthConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity, validate_identity_id
from foliqant.ports.auth import AuthenticatedCaller

_MAX_TOKEN_BYTES = 16_384
_MAX_HEADER_BYTES = 4096
_MAX_PAYLOAD_BYTES = 65_536
_CLOSE_TIMEOUT_SECONDS = 5.0
_KEY_TYPE_BY_ALGORITHM: dict[JwtAlgorithm, tuple[str, str | None]] = {
    JwtAlgorithm.RS256: ("RSA", None),
    JwtAlgorithm.ES256: ("EC", "P-256"),
    JwtAlgorithm.EDDSA: ("OKP", "Ed25519"),
}


class _DuplicateJsonKey(ValueError):
    """Internal marker that carries no attacker-controlled content."""


def _invalid_configuration() -> ServiceError:
    return ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _unauthenticated() -> ServiceError:
    return ServiceError(ErrorCode.UNAUTHENTICATED)


def _dependency_failure() -> ServiceError:
    return ServiceError(ErrorCode.DEPENDENCY_FAILURE, retryable=True)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _json_object(data: bytes, *, authentication: bool) -> dict[str, object]:
    error = _unauthenticated if authentication else _dependency_failure
    try:
        value = json.loads(data, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, RecursionError):
        raise error() from None
    if not isinstance(value, dict):
        raise error()
    return cast(dict[str, object], value)


def _decode_segment(segment: str, *, maximum: int) -> bytes:
    if not segment or len(segment) > ((maximum + 2) // 3) * 4 + 4:
        raise _unauthenticated()
    try:
        encoded = segment.encode("ascii")
        base64url = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        if any(byte not in base64url for byte in encoded):
            raise ValueError
        decoded = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
    except (UnicodeEncodeError, ValueError, binascii.Error):
        raise _unauthenticated() from None
    if len(decoded) > maximum:
        raise _unauthenticated()
    return decoded


def _validated_compact_token(authorization: str | None) -> tuple[str, dict[str, object]]:
    if (
        authorization is None
        or authorization[:7].lower() != "bearer "
        or authorization.count(" ") != 1
    ):
        raise _unauthenticated()
    token = authorization[7:]
    try:
        raw = token.encode("ascii")
    except UnicodeEncodeError:
        raise _unauthenticated() from None
    if not raw or len(raw) > _MAX_TOKEN_BYTES or any(byte <= 32 or byte == 127 for byte in raw):
        raise _unauthenticated()
    segments = token.split(".")
    if len(segments) != 3 or not segments[2]:
        raise _unauthenticated()
    header = _json_object(
        _decode_segment(segments[0], maximum=_MAX_HEADER_BYTES), authentication=True
    )
    # Reject duplicate claim names and oversized claim objects before PyJWT parses
    # the same compact token. The verified result remains PyJWT's result below.
    _json_object(_decode_segment(segments[1], maximum=_MAX_PAYLOAD_BYTES), authentication=True)
    return token, header


def _header_policy(
    header: Mapping[str, object], algorithms: frozenset[JwtAlgorithm]
) -> tuple[str, JwtAlgorithm]:
    if any(name in header for name in ("crit", "jku", "jwk", "x5u", "x5c")):
        raise _unauthenticated()
    kid = header.get("kid")
    algorithm_value = header.get("alg")
    if (
        not isinstance(kid, str)
        or not kid
        or len(kid) > 256
        or kid != kid.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in kid)
        or not isinstance(algorithm_value, str)
    ):
        raise _unauthenticated()
    try:
        algorithm = JwtAlgorithm(algorithm_value)
    except ValueError:
        raise _unauthenticated() from None
    if algorithm not in algorithms:
        raise _unauthenticated()
    return kid, algorithm


def _safe_jwk(
    value: object,
    *,
    allowed_algorithms: frozenset[JwtAlgorithm],
) -> tuple[tuple[str, JwtAlgorithm], jwt.PyJWK]:
    if not isinstance(value, dict):
        raise _dependency_failure()
    key = cast(dict[str, object], value)
    kid = key.get("kid")
    alg = key.get("alg")
    kty = key.get("kty")
    if (
        not isinstance(kid, str)
        or not kid
        or len(kid) > 256
        or kid != kid.strip()
        or not isinstance(kty, str)
        or "d" in key
    ):
        raise _dependency_failure()
    if "alg" not in key:
        candidates = [
            candidate
            for candidate in allowed_algorithms
            if _KEY_TYPE_BY_ALGORITHM[candidate][0] == kty
            and (
                _KEY_TYPE_BY_ALGORITHM[candidate][1] is None
                or _KEY_TYPE_BY_ALGORITHM[candidate][1] == key.get("crv")
            )
        ]
        if len(candidates) != 1:
            raise _dependency_failure()
        algorithm = candidates[0]
    else:
        if not isinstance(alg, str):
            raise _dependency_failure()
        try:
            algorithm = JwtAlgorithm(alg)
        except ValueError:
            raise _dependency_failure() from None
        if algorithm not in allowed_algorithms:
            # A JWKS may publish unrelated asymmetric algorithms; ignore those at
            # the collection layer rather than passing them into this function.
            raise _dependency_failure()
    expected_kty, expected_curve = _KEY_TYPE_BY_ALGORITHM[algorithm]
    if kty != expected_kty or (expected_curve is not None and key.get("crv") != expected_curve):
        raise _dependency_failure()
    if key.get("use", "sig") != "sig":
        raise _dependency_failure()
    operations = key.get("key_ops")
    if operations is not None:
        if (
            not isinstance(operations, list)
            or any(not isinstance(item, str) for item in operations)
            or "verify" not in operations
            or "sign" in operations
        ):
            raise _dependency_failure()
    try:
        parsed = jwt.PyJWK.from_dict(cast(Any, key), algorithm=algorithm.value)
    except Exception:
        raise _dependency_failure() from None
    return (kid, algorithm), parsed


class JwtAuthenticator:
    """Verify JWTs against a fixed issuer policy and native-async JWKS cache."""

    def __init__(
        self,
        config: JwtAuthConfig,
        *,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        try:
            validated = JwtAuthConfig.model_validate(config.model_dump(mode="python"), strict=True)
        except Exception:
            raise _invalid_configuration() from None
        self._config = validated
        self._algorithms = frozenset(validated.algorithms)
        self._caller_workflows = frozenset(validated.workflows)
        self._client = http_client or httpx2.AsyncClient(
            timeout=httpx2.Timeout(validated.request_timeout),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = http_client is None
        self._keys: dict[tuple[str, JwtAlgorithm], jwt.PyJWK] = {}
        self._cache_initialized = False
        self._cache_expires_at = 0.0
        self._last_unknown_refresh_at = float("-inf")
        self._last_failed_refresh_at = float("-inf")
        self._cache_lock = asyncio.Lock()

    async def authenticate(self, authorization: str | None) -> AuthenticatedCaller:
        """Verify a bearer JWT and return identity plus configured workflow grants."""
        token, header = _validated_compact_token(authorization)
        kid, algorithm = _header_policy(header, self._algorithms)
        key = await self._key(kid, algorithm)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm.value],
                audience=self._config.audience,
                issuer=self._config.issuer,
                options={
                    "require": ["exp", "iat", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except Exception:
            raise _unauthenticated() from None
        if not isinstance(claims, dict):
            raise _unauthenticated()
        for name in ("exp", "iat", "nbf"):
            if name == "nbf" and name not in claims:
                continue
            value = claims.get(name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise _unauthenticated()
        tenant_id = self._identity_claim(claims, self._config.tenant_claim)
        principal_id = self._identity_claim(claims, self._config.principal_claim)
        try:
            identity = Identity(tenant_id=tenant_id, principal_id=principal_id)
        except ServiceError:
            raise _unauthenticated() from None
        return AuthenticatedCaller(identity=identity, workflows=self._caller_workflows)

    async def _key(self, kid: str, algorithm: JwtAlgorithm) -> jwt.PyJWK:
        loop = asyncio.get_running_loop()
        now = loop.time()
        cached = self._keys.get((kid, algorithm))
        if cached is not None and now < self._cache_expires_at:
            return cached
        deadline = now + self._config.request_timeout
        try:
            async with asyncio.timeout_at(deadline):
                await self._cache_lock.acquire()
        except TimeoutError:
            raise ServiceError(ErrorCode.TIMEOUT, retryable=True) from None
        try:
            now = loop.time()
            cached = self._keys.get((kid, algorithm))
            if cached is not None and now < self._cache_expires_at:
                return cached
            if now - self._last_failed_refresh_at < self._config.jwks_refresh_cooldown:
                raise _dependency_failure()
            cache_is_fresh = now < self._cache_expires_at
            if cache_is_fresh:
                if now - self._last_unknown_refresh_at < self._config.jwks_refresh_cooldown:
                    raise _unauthenticated()
                self._last_unknown_refresh_at = now
            elif cached is None and self._cache_initialized:
                self._last_unknown_refresh_at = now
            try:
                await self._refresh(now, deadline=deadline)
            except asyncio.CancelledError:
                raise
            except ServiceError:
                self._last_failed_refresh_at = loop.time()
                raise
            self._last_failed_refresh_at = float("-inf")
            cached = self._keys.get((kid, algorithm))
            if cached is None:
                # The refreshed document was valid but did not contain this key.
                self._last_unknown_refresh_at = loop.time()
                raise _unauthenticated()
            return cached
        finally:
            self._cache_lock.release()

    async def _refresh(self, refreshed_at: float, *, deadline: float) -> None:
        request = self._client.build_request(
            "GET",
            self._config.jwks_url,
            headers={
                "Accept": "application/json, application/jwk-set+json",
                "Accept-Encoding": "identity",
            },
        )
        response: httpx2.Response | None = None
        active_error: BaseException | None = None
        document: dict[str, object] | None = None
        try:
            async with asyncio.timeout_at(deadline):
                response = await self._client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                )
                if response.status_code != 200:
                    raise _dependency_failure()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type not in {"application/json", "application/jwk-set+json"}:
                    raise _dependency_failure()
                if response.headers.get("content-encoding", "identity").strip().lower() not in {
                    "",
                    "identity",
                }:
                    raise _dependency_failure()
                chunks: list[bytes] = []
                size = 0
                # Iterate the negotiated identity stream directly. HTTPX's
                # aiter_bytes/aiter_raw helpers close in their own unbounded
                # generator finally block, outside this adapter's cleanup
                # control. The explicit close below owns that bounded phase.
                stream = cast(httpx2.AsyncByteStream, response.stream)
                async for chunk in stream:
                    size += len(chunk)
                    if size > self._config.max_jwks_bytes:
                        raise _dependency_failure()
                    chunks.append(chunk)
                document = _json_object(b"".join(chunks), authentication=False)
        except asyncio.CancelledError as error:
            active_error = error
        except ServiceError as error:
            active_error = error
        except TimeoutError:
            active_error = ServiceError(ErrorCode.TIMEOUT, retryable=True)
        except httpx2.HTTPError:
            active_error = _dependency_failure()
        except Exception:
            active_error = _dependency_failure()
        if response is not None:
            cleanup_deadline = (
                asyncio.get_running_loop().time()
                if isinstance(active_error, asyncio.CancelledError)
                else deadline
            )
            try:
                async with asyncio.timeout_at(cleanup_deadline):
                    await response.aclose()
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                if active_error is None:
                    active_error = ServiceError(ErrorCode.TIMEOUT, retryable=True)
            except Exception:
                if active_error is None:
                    active_error = _dependency_failure()
        if active_error is not None:
            raise active_error
        if document is None:  # pragma: no cover - closed branches assign or fail
            raise _dependency_failure()

        values = document.get("keys")
        if not isinstance(values, list) or not values or len(values) > self._config.max_cached_keys:
            raise _dependency_failure()
        keys: dict[tuple[str, JwtAlgorithm], jwt.PyJWK] = {}
        for value in values:
            if isinstance(value, dict):
                declared_algorithm = value.get("alg")
                if declared_algorithm is not None and declared_algorithm not in {
                    algorithm.value for algorithm in self._algorithms
                }:
                    continue
            identity, parsed = _safe_jwk(value, allowed_algorithms=self._algorithms)
            if identity in keys:
                raise _dependency_failure()
            keys[identity] = parsed
        if not keys:
            raise _dependency_failure()
        self._keys = keys
        self._cache_initialized = True
        self._cache_expires_at = refreshed_at + self._config.jwks_cache_ttl

    @staticmethod
    def _identity_claim(claims: Mapping[str, object], name: str) -> str | None:
        if name not in claims:
            return None
        value = claims[name]
        try:
            return validate_identity_id(value)
        except ServiceError:
            raise _unauthenticated() from None

    async def aclose(self) -> None:
        """Close the owned HTTP pool within a fixed cleanup bound."""
        if not self._owns_client:
            return
        try:
            async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
                await self._client.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            return


__all__ = ["JwtAuthenticator"]
