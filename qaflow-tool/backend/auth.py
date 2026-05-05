"""Authentication dependencies for FastAPI."""

from fastapi import Depends, Header, HTTPException, status

import db


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """Resolve the current user from the Authorization header."""
    token = _bearer(authorization)
    user = db.user_for_token(token)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    return user


def require_roles(*allowed: str):
    """Dependency factory: only users with one of the given roles may pass."""

    def _checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role '{user['role']}' not allowed (need one of: {', '.join(allowed)})",
            )
        return user

    return _checker
