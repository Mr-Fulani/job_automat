from fastapi import HTTPException, Request

from .config import get_settings


def require_admin(request: Request) -> None:
    settings = get_settings()
    token = request.headers.get("X-Admin-Token")
    if not token:
        token = request.cookies.get("admin_token")

    if not settings.admin_token:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not set")

    if token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
