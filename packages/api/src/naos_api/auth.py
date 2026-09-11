from typing import NoReturn

from fastapi import HTTPException, status


def require_principal() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication is not configured",
        headers={"WWW-Authenticate": "Bearer"},
    )
