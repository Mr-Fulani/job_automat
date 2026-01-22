#!/usr/bin/env python3
"""Минимальный сервер с видимым браузером для откликов"""
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from job_automation.services.webuse_apply import WebUseApplyService
from job_automation.config import get_settings

app = FastAPI()
service = WebUseApplyService()

class ApplyRequest(BaseModel):
    url: str
    message: str = ""

@app.post("/apply")
async def apply(request: ApplyRequest):
    # Принудительно включаем видимый браузер
    settings = get_settings()
    settings.browser_headless = False
    
    result = await service.apply(request.url, request.message)
    return result

@app.get("/health")
async def health():
    return {"status": "ok", "browser_headless": False}

if __name__ == "__main__":
    print("🚀 Запускаю сервер с ВИДИМЫМ браузером...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
