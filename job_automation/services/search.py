"""Асинхронный сервис поиска вакансий."""

import logging
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from ..config import get_settings
from .browser import browser_manager

logger = logging.getLogger(__name__)


@dataclass
class Vacancy:
    """Модель данных вакансии."""
    title: str
    url: str
    employer: str
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "employer": self.employer,
            "description": self.description
        }


class VacancySearchService:
    """Сервис для поиска вакансий на HH.ru."""

    def __init__(self) -> None:
        self._settings = get_settings()

    async def _get_vacancy_description(self, page: Page, url: str) -> str:
        """
        Переход на страницу вакансии и извлечение полного описания.
        
        Аргументы:
            page: Страница браузера для использования.
            url: URL вакансии.
            
        Возвращает:
            Полный текст описания вакансии.
        """
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector("[data-qa='vacancy-description']", timeout=10000)
            
            description_el = page.locator("[data-qa='vacancy-description']")
            if await description_el.count() > 0:
                return (await description_el.inner_text()).strip()
            return ""
            
        except Exception as e:
            logger.warning(f"Failed to get description for {url}: {e}")
            return ""

    async def _check_bot_protection(self, page: Page) -> bool:
        """Проверка, сработала ли защита от ботов (капча)."""
        try:
            url = page.url.lower()
            title = (await page.title()).lower()

            if "captcha" in url or "captcha" in title:
                return True

            captcha_selectors = [
                'iframe[src*="captcha"]',
                'form[action*="captcha"]',
                '[class*="captcha"]',
                '[id*="captcha"]',
                'img[src*="captcha"]',
            ]
            for selector in captcha_selectors:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True

            content = (await page.content()).lower()
            captcha_phrases = [
                "подтвердите, что вы не робот",
                "я не робот",
                "введите символы",
                "anti-bot",
                "antibot",
            ]
            return any(p in content for p in captcha_phrases)
        except Exception:
            return False

    async def _save_bot_protection_artifacts(self, page: Page, prefix: str) -> tuple[Path, Path]:
        debug_dir = Path("data") / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        screenshot_path = debug_dir / f"{prefix}_{ts}.png"
        html_path = debug_dir / f"{prefix}_{ts}.html"

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as e:
            logger.warning(f"Failed to save screenshot for bot protection page: {e}")

        try:
            html_path.write_text(await page.content(), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save HTML for bot protection page: {e}")

        return screenshot_path, html_path

    async def search(
        self,
        query: Optional[str] = None,
        page_num: int = 0,
        include_descriptions: bool = True
    ) -> list[dict]:
        """
        Поиск вакансий, соответствующих запросу.
        
        Аргументы:
            query: Текст запроса. По умолчанию используется значение из настроек.
            page_num: Номер страницы для пагинации (начиная с 0).
            
        Возвращает:
            Список словарей вакансий с заголовком, URL, работодателем и описанием.
            
        Исключения:
            RuntimeError: Если сработала защита от ботов.
            FileNotFoundError: Если файл сессии не найден.
        """
        query = query or self._settings.default_search_text
        
        logger.info(f"Searching vacancies: query='{query}', page={page_num}")

        async with browser_manager.get_page(use_session=True) as page:
            # Сборка URL для поиска
            url = (
                f"https://hh.ru/search/vacancy?"
                f"text={query}&area={self._settings.area_code}"
                f"&items_on_page=20&page={page_num}"
            )
            
            await page.goto(url, wait_until="domcontentloaded")
            
            if await self._check_bot_protection(page):
                screenshot_path, html_path = await self._save_bot_protection_artifacts(page, "bot_protection_search")
                raise RuntimeError(
                    f"Bot protection triggered (captcha detected). Saved: {screenshot_path} and {html_path}"
                )

            # Ожидание результатов
            await page.wait_for_selector("[data-qa='vacancy-serp__vacancy']", timeout=10000)
            
            # Сбор основных данных вакансий из результатов поиска
            vacancy_data: list[dict] = []
            cards = await page.locator("[data-qa='vacancy-serp__vacancy']").all()
            
            for i, card in enumerate(cards):
                try:
                    title_el = card.locator("[data-qa='serp-item__title']")
                    await title_el.wait_for(state="visible", timeout=5000)
                    
                    href = await title_el.get_attribute("href")
                    title = await title_el.inner_text()
                    
                    employer_el = card.locator("[data-qa='vacancy-serp__vacancy-employer']").first
                    employer = (
                        await employer_el.inner_text() 
                        if await employer_el.count() > 0 
                        else "Unknown"
                    )
                    
                    vacancy_data.append({
                        "title": title,
                        "url": href,
                        "employer": employer
                    })
                    
                except Exception as e:
                    logger.warning(f"Failed to parse vacancy card {i}: {e}")
                    continue

            vacancies: list[dict] = []
            if include_descriptions:
                # Получение полных описаний для каждой вакансии (требует переходов по страницам вакансий)
                for data in vacancy_data:
                    description = await self._get_vacancy_description(page, data["url"])
                    vacancy = Vacancy(
                        title=data["title"],
                        url=data["url"],
                        employer=data["employer"],
                        description=description
                    )
                    vacancies.append(vacancy.to_dict())
            else:
                # Быстрый режим: без переходов в каждую вакансию
                for data in vacancy_data:
                    vacancy = Vacancy(
                        title=data["title"],
                        url=data["url"],
                        employer=data["employer"],
                        description=""
                    )
                    vacancies.append(vacancy.to_dict())

            logger.info(f"Found {len(vacancies)} vacancies")
            return vacancies
