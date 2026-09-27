import hashlib
import hmac
import os

from itsdangerous import (
    BadSignature,
    SignatureExpired,
    URLSafeTimedSerializer,
)

from .config import ENV


SECRET_KEY = ENV["SECRET_KEY"].encode()


def csrf_token(session_token: str) -> str:
    return hmac.new(
        SECRET_KEY,
        session_token.encode(),
        hashlib.sha256,
    ).hexdigest()


def csrf_valid(session_token: str, supplied: str) -> bool:
    if not session_token or not supplied:
        return False

    expected = csrf_token(session_token)

    return hmac.compare_digest(
        expected,
        supplied,
    )


_mfa_serializer = URLSafeTimedSerializer(
    SECRET_KEY,
    salt="netlog-manager-mfa-setup-v1",
)


def create_mfa_setup_token(
    *,
    user_id: int,
    secret: str,
) -> str:
    return _mfa_serializer.dumps(
        {
            "user_id": int(user_id),
            "secret": secret,
        }
    )


def read_mfa_setup_token(
    token: str,
    *,
    max_age: int = 600,
):
    try:
        data = _mfa_serializer.loads(
            token,
            max_age=max_age,
        )
    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(data, dict):
        return None

    try:
        user_id = int(data["user_id"])
        secret = str(data["secret"])
    except (KeyError, TypeError, ValueError):
        return None

    if not user_id or not secret:
        return None

    return {
        "user_id": user_id,
        "secret": secret,
    }
