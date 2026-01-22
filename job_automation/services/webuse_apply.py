"""Сервис для автоматического заполнения форм через Web-Use + GPT-4o."""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from job_automation.config import get_settings
from job_automation.models.candidate import CandidateProfile
from .apply_interface import ApplyServiceInterface


class WebUseApplyService(ApplyServiceInterface):
    """Сервис для автономного заполнения форм отклика на HH.ru."""
    
    def __init__(self):
        self.settings = get_settings()
        self.openai_client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        self._setup_logging()
    
    def _setup_logging(self):
        """Настройка логирования с ротацией."""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        log_file = log_dir / f"webuse_{datetime.now().strftime('%Y-%m-%d')}.log"
        
        # Создаем новый логгер для Web-Use
        self.logger = logging.getLogger("webuse_apply")
        self.logger.setLevel(logging.INFO)
        
        # Формат логов
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # File handler с ротацией
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=10, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
    
    async def load_profile(self) -> CandidateProfile:
        """Загрузка профиля кандидата из файла."""
        profile_path = Path("data/candidate_profile.json")
        
        if not profile_path.exists():
            raise FileNotFoundError("candidate_profile.json not found")
        
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile_data = json.load(f)
        
        return CandidateProfile(**profile_data)
    
    async def apply(self, url: str, message: str = "") -> dict:
        """Основной метод отклика на вакансию через Web-Use."""
        try:
            self.logger.info(f"Начало отклика на вакансию: {url}")
            
            # Загрузка профиля
            profile = await self.load_profile()
            
            # Использование browser_manager для получения страницы
            from .browser import browser_manager
            async with browser_manager.get_page(use_session=True) as page:
                # Переход на страницу вакансии
                await page.goto(url)
                await page.wait_for_load_state('networkidle')
                
                # Проверка на капчу
                if await self._is_captcha_present(page):
                    self.logger.warning("Обнаружена капча, пропуск вакансии")
                    return {"status": "skipped", "message": "captcha detected"}
                
                # Поиск и клик по кнопке "Откликнуться"
                await self._click_apply_button(page)
                
                # Заполнение формы через Web-Use
                success = await self._fill_application_form(page, profile, message)
                
                if success:
                    self.logger.info("Форма успешно заполнена и отправлена")
                    return {"status": "success", "message": "application submitted"}
                else:
                    self.logger.error("Не удалось заполнить форму")
                    return {"status": "error", "message": "form filling failed"}
                    
        except Exception as e:
            self.logger.error(f"Ошибка при отклике: {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            # Сохранение скриншота
            try:
                from .browser import browser_manager
                async with browser_manager.get_page(use_session=True) as page:
                    await self._save_screenshot(page)
            except:
                pass
    
    async def _is_captcha_present(self, page: Page) -> bool:
        """Проверка наличия капчи на странице."""
        try:
            # Поиск常见 капча элементов
            captcha_selectors = [
                'iframe[src*="captcha"]',
                '.captcha',
                '[class*="captcha"]',
                'img[src*="captcha"]'
            ]
            
            for selector in captcha_selectors:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    return True
            return False
        except:
            return False
    
    async def _click_apply_button(self, page: Page):
        """Поиск и клик по кнопке отклика."""
        apply_selectors = [
            'button[data-qa="vacancy-response-submit"]',
            'button:has-text("Откликнуться")',
            'a:has-text("Откликнуться")',
            '[data-qa="vacancy-response-link"]'
        ]
        
        for selector in apply_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=5000)
                if button and await button.is_visible():
                    await button.click()
                    await page.wait_for_load_state('networkidle')
                    return
            except:
                continue
        
        raise Exception("Кнопка отклика не найдена")
    
    async def _fill_application_form(self, page: Page, profile: CandidateProfile, custom_message: str) -> bool:
        """Заполнение формы отклика с помощью GPT-4o."""
        
        # Формирование сопроводительного письма
        if custom_message:
            cover_letter = custom_message
        else:
            cover_letter = profile.cover_letter_template.format(
                experience_years=profile.experience_years,
                position=profile.position,
                skills=", ".join(profile.skills)
            )
        
        # Создание промпта для GPT-4o
        prompt = self._create_form_filling_prompt(profile, cover_letter)
        
        try:
            # Здесь будет интеграция с Web-Use
            # Временно используем базовую логику заполнения
            await self._basic_form_fill(page, profile, cover_letter)
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка заполнения формы: {str(e)}")
            return False
    
    def _create_form_filling_prompt(self, profile: CandidateProfile, cover_letter: str) -> str:
        """Создание промпта для GPT-4o."""
        return f"""
Ты - ассистент для заполнения формы отклика на вакансию HH.ru.

Данные кандидата:
- Имя: {profile.full_name}
- Email: {profile.email}
- Телефон: {profile.phone}
- Город: {profile.city}
- Опыт: {profile.experience_years} лет
- Должность: {profile.position}
- Навыки: {', '.join(profile.skills)}
- Формат работы: {', '.join(profile.work_format)}
- Зарплатные ожидания: {profile.salary_expectations}

Сопроводительное письмо:
{cover_letter}

Ответы на вопросы:
{json.dumps(profile.answers, ensure_ascii=False, indent=2)}

Твоя задача:
1. Найти все поля формы на странице
2. Заполнить их используя данные кандидата
3. Выбрать подходящие значения в select/radio/checkbox
4. Ответить на вопросы используя заготовленные ответы
5. Нажать кнопку отправки

Заполняй только поля, которые найдешь на странице. Не придумывай данные.
""".strip()
    
    async def _basic_form_fill(self, page: Page, profile: CandidateProfile, cover_letter: str):
        """Базовое заполнение формы (временная реализация)."""
        
        # Заполнение текстовых полей
        text_inputs = await page.query_selector_all('input[type="text"], input[type="email"], input[type="tel"], textarea')
        
        for input_element in text_inputs:
            try:
                placeholder = await input_element.get_attribute('placeholder') or ""
                name = await input_element.get_attribute('name') or ""
                
                value = ""
                if 'email' in name.lower() or 'email' in placeholder.lower():
                    value = profile.email
                elif 'phone' in name.lower() or 'телефон' in placeholder.lower():
                    value = profile.phone
                elif 'город' in placeholder.lower() or 'city' in name.lower():
                    value = profile.city
                elif 'сопроводительное' in placeholder.lower() or 'cover' in name.lower():
                    value = cover_letter
                
                if value:
                    await input_element.fill(value)
                    await page.wait_for_timeout(500)
                    
            except Exception as e:
                self.logger.warning(f"Не удалось заполнить поле: {str(e)}")
                continue
        
        # Поиск и заполнение поля сопроводительного письма
        cover_selectors = [
            'textarea[placeholder*="сопроводительное"]',
            'textarea[name*="letter"]',
            'textarea[data-qa="vacancy-response-letter"]'
        ]
        
        for selector in cover_selectors:
            try:
                textarea = await page.wait_for_selector(selector, timeout=3000)
                if textarea:
                    await textarea.fill(cover_letter)
                    break
            except:
                continue
        
        # Нажатие кнопки отправки
        submit_selectors = [
            'button[data-qa="vacancy-response-submit-popup"]',
            'button:has-text("Откликнуться")',
            'button:has-text("Отправить")',
            'button[type="submit"]'
        ]
        
        for selector in submit_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=3000)
                if button and await button.is_visible():
                    await button.click()
                    await page.wait_for_timeout(2000)
                    return True
            except:
                continue
        
        return False
    
    async def _save_screenshot(self, page: Page):
        """Сохранение скриншота страницы."""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
            screenshot_path = Path(f"logs/webuse_{timestamp}.png")
            
            await page.screenshot(path=screenshot_path, full_page=True)
            self.logger.info(f"Скриншот сохранен: {screenshot_path}")
        except Exception as e:
            self.logger.error(f"Ошибка сохранения скриншота: {str(e)}")
