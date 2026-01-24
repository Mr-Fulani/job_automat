import logging
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from .config import get_settings
from .services import browser_manager, VacancySearchService, VacancyApplyService
from .services.webuse_apply import WebUseApplyService
from .admin_auth import require_admin
from .storage.users_db import init_db, list_users, get_user, create_user, get_profile_json, upsert_profile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
logger = logging.getLogger("HHServer")


class ApplyRequest(BaseModel):
    """Тело запроса для отклика на вакансию."""
    url: HttpUrl
    message: str = ""


class ApplyResponse(BaseModel):
    status: str
    message: str


class ErrorResponse(BaseModel):
    error: str
    message: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db(settings.app_db_path)
    _bootstrap_admin(settings.app_db_path)
    logger.info("Starting browser manager...")
    await browser_manager.start()
    yield
    logger.info("Shutting down browser manager...")
    await browser_manager.stop()


def _users_dir(user_slug: str) -> Path:
    return Path("data") / "users" / user_slug


def _session_path(user_slug: str) -> Path:
    return _users_dir(user_slug) / "hh_session.json"


def _candidate_profile_path(user_slug: str) -> Path:
    return _users_dir(user_slug) / "candidate_profile.json"


def _bootstrap_admin(db_path: Path) -> None:
    admin_slug = "admin"
    if get_user(db_path, admin_slug) is not None:
        return

    create_user(db_path, slug=admin_slug, display_name="Admin")

    legacy_profile = Path("data/candidate_profile.json")
    if legacy_profile.exists():
        try:
            data = json.loads(legacy_profile.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                upsert_profile(db_path, user_slug=admin_slug, profile_json=data)
        except Exception:
            pass


app = FastAPI(
    title="HH.ru Automation API",
    description="Async API for searching and applying to vacancies on HH.ru",
    version="2.0.0",
    lifespan=lifespan
)

# Middleware для CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Экземпляры сервисов
search_service = VacancySearchService()
apply_service = VacancyApplyService()
webuse_apply_service = WebUseApplyService()


@app.get("/search")
async def search_vacancies(
    text: str = Query(default="Frontend", description="Search query text"),
    page: int = Query(default=0, ge=0, description="Page number (0-indexed)")
) -> list[dict]:
    """
    Поиск вакансий на HH.ru.
    
    Возвращает список вакансий с заголовком, URL, работодателем и описанием.
    """
    logger.info(f"Search request: text='{text}', page={page}")
    
    try:
        vacancies = await search_service.search(query=text, page_num=page)
        return vacancies
    except FileNotFoundError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/apply", response_model=ApplyResponse)
async def apply_to_vacancy(request: ApplyRequest) -> ApplyResponse:
    """
    Отклик на вакансию с опциональным сопроводительным письмом.
    
    Возвращает статус и сообщение результата отклика.
    """
    logger.info(f"Apply request: url={request.url}")
    
    try:
        settings = get_settings()
        
        # Выбор сервиса в зависимости от настроек
        if settings.use_web_use:
            logger.info("Используем Web-Use сервис")
            result = await webuse_apply_service.apply(str(request.url), request.message)
        else:
            logger.info("Используем стандартный сервис")
            result = await apply_service.apply(str(request.url), request.message)
        
        return ApplyResponse(**result)
    except Exception as e:
        logger.error(f"Apply failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "session_exists": settings.session_file.exists(),
        "version": "3.0.0",
        "web_use_enabled": settings.use_web_use
    }


@app.get("/ui/login", response_class=HTMLResponse)
async def ui_login(request: Request, next: str = "/ui/users"):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next": next, "error": ""},
    )


@app.post("/ui/login")
async def ui_login_post(
    request: Request,
    token: str = Form(default=""),
    next: str = Form(default="/ui/users"),
):
    settings = get_settings()
    if not settings.admin_token:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "ADMIN_TOKEN is not set"},
            status_code=500,
        )
    if token != settings.admin_token:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Invalid token"},
            status_code=401,
        )
    resp = RedirectResponse(url=next or "/ui/users", status_code=302)
    resp.set_cookie("admin_token", token, httponly=True, samesite="lax")
    return resp


@app.get("/ui/users", response_class=HTMLResponse)
async def ui_users(request: Request, message: str = "", error: str = ""):
    require_admin(request)
    settings = get_settings()
    users = list_users(settings.app_db_path)
    return templates.TemplateResponse(
        "users_list.html",
        {"request": request, "users": users, "message": message, "error": error},
    )


@app.post("/ui/users")
async def ui_users_create(
    request: Request,
    slug: str = Form(default=""),
    display_name: str = Form(default=""),
):
    require_admin(request)
    settings = get_settings()
    slug = str(slug or "").strip()
    display_name = str(display_name or "").strip()
    if not slug or not display_name:
        return await ui_users(request, error="slug and display_name are required")

    try:
        create_user(settings.app_db_path, slug=slug, display_name=display_name)
        return await ui_users(request, message=f"created: {slug}")
    except Exception as e:
        return await ui_users(request, error=str(e))


@app.get("/ui/users/{slug}", response_class=HTMLResponse)
async def ui_user_edit(request: Request, slug: str, message: str = "", error: str = ""):
    require_admin(request)
    settings = get_settings()
    user = get_user(settings.app_db_path, slug)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    profile = get_profile_json(settings.app_db_path, slug) or {}
    profile_text = json.dumps(profile, ensure_ascii=False, indent=2)
    session_exists = _session_path(slug).exists()
    return templates.TemplateResponse(
        "user_edit.html",
        {
            "request": request,
            "user": user,
            "profile_json_text": profile_text,
            "session_exists": session_exists,
            "message": message,
            "error": error,
        },
    )


@app.post("/ui/users/{slug}/profile")
async def ui_user_save_profile(request: Request, slug: str, profile_json: str = Form(default="")):
    require_admin(request)
    settings = get_settings()
    user = get_user(settings.app_db_path, slug)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        data = json.loads(profile_json or "{}")
        if not isinstance(data, dict):
            return await ui_user_edit(request, slug, error="profile_json must be a JSON object")
        upsert_profile(settings.app_db_path, user_slug=slug, profile_json=data)

        _users_dir(slug).mkdir(parents=True, exist_ok=True)
        _candidate_profile_path(slug).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return await ui_user_edit(request, slug, message="saved")
    except Exception as e:
        return await ui_user_edit(request, slug, error=str(e))


@app.post("/ui/users/{slug}/validate")
async def ui_user_validate(request: Request, slug: str, profile_json: str = Form(default="")):
    require_admin(request)
    settings = get_settings()
    user = get_user(settings.app_db_path, slug)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        data = json.loads(profile_json or "{}")
        if not isinstance(data, dict):
            return await ui_user_edit(request, slug, error="profile_json must be a JSON object")

        from .models.candidate import CandidateProfile

        CandidateProfile(**data)
        return await ui_user_edit(request, slug, message="valid")
    except Exception as e:
        return await ui_user_edit(request, slug, error=str(e))


@app.post("/ui/users/{slug}/session")
async def ui_user_upload_session(request: Request, slug: str, session_file: UploadFile = File(...)):
    require_admin(request)
    settings = get_settings()
    user = get_user(settings.app_db_path, slug)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        content = await session_file.read()
        _users_dir(slug).mkdir(parents=True, exist_ok=True)
        _session_path(slug).write_bytes(content)
        return await ui_user_edit(request, slug, message="session uploaded")
    except Exception as e:
        return await ui_user_edit(request, slug, error=str(e))


@app.get("/api/users")
async def api_list_users(request: Request) -> list[dict]:
    require_admin(request)
    settings = get_settings()
    users = list_users(settings.app_db_path)
    return [u.__dict__ for u in users]


@app.get("/api/users/{slug}/profile")
async def api_get_profile(request: Request, slug: str) -> dict:
    require_admin(request)
    settings = get_settings()
    user = get_user(settings.app_db_path, slug)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return get_profile_json(settings.app_db_path, slug) or {}


def run():
    """Запуск сервера."""
    import uvicorn
    settings = get_settings()
    
    logger.info(f"Starting HH Automation API on http://{settings.server_host}:{settings.server_port}")
    logger.info("Endpoints:")
    logger.info("  GET  /search?text=Frontend&page=0")
    logger.info("  POST /apply  { 'url': '...', 'message': '...' }")
    logger.info("  GET  /health")
    logger.info("  GET  /docs  (Swagger UI)")
    
    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        log_level="info"
    )


if __name__ == "__main__":
    run()
