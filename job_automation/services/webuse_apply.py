"""
Сервис для автоматического заполнения форм отклика на HH.ru через Web-Use + GPT-4o.

Этот модуль предоставляет функциональность для автоматического отклика на вакансии,
включая обработку дополнительных вопросов работодателя, заполнение чекбоксов
подтверждения данных и интеграцию с ИИ для генерации ответов.

Основные возможности:
- Автоматическое заполнение форм отклика с вопросами работодателя
- Обработка чекбоксов и radio buttons подтверждения достоверности данных
- Интеллектуальный анализ вопросов и генерация подходящих ответов
- Сохранение скриншотов для отладки и контроля процесса
- Подробное логирование всех действий

Пример использования:
    service = WebUseApplyService()
    result = await service.apply("https://hh.ru/vacancy/123456", "Сопроводительное письмо")
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from openai import AsyncOpenAI
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from job_automation.config import get_settings
from job_automation.models.candidate import CandidateProfile
from .apply_interface import ApplyServiceInterface


class WebUseApplyService(ApplyServiceInterface):
    """
    Сервис для автономного заполнения форм отклика на HH.ru с поддержкой ИИ.

    Обрабатывает сложные формы с дополнительными вопросами работодателя,
    автоматически отвечает на типичные вопросы о опыте работы, зарплатных ожиданиях
    и подтверждает достоверность предоставленных данных.

    Использует систему готовых ответов для экономии токенов AI.
    """

    def __init__(self):
        self.settings = get_settings()
        self.openai_client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        self._setup_logging()
        self._load_common_questions()
        self._load_processed_vacancies()
    
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

    def _load_common_questions(self):
        """
        Загрузка файла с часто задаваемыми вопросами и готовыми ответами.

        Этот файл позволяет экономить токены AI, используя готовые ответы
        вместо генерации через GPT для типичных вопросов.
        """
        self.common_questions = {}
        questions_file = Path("data/common_questions.json")

        try:
            if questions_file.exists():
                with open(questions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.common_questions = data.get('questions', {})
                    metadata = data.get('metadata', {})
                    self.logger.info(f"Загружено {len(self.common_questions)} готовых ответов на вопросы (v{metadata.get('version', 'N/A')})")
            else:
                self.logger.warning("Файл common_questions.json не найден, будут использоваться только базовые ответы")
        except Exception as e:
            self.logger.error(f"Ошибка загрузки common_questions.json: {str(e)}")
            self.common_questions = {}

    def _load_processed_vacancies(self):
        """
        Загрузка файла с обработанными вакансиями.

        Этот файл предотвращает повторную обработку одних и тех же вакансий,
        экономя время и ресурсы.
        """
        self.processed_vacancies = {}
        processed_file = Path("data/processed_vacancies.json")

        try:
            if processed_file.exists():
                with open(processed_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.processed_vacancies = data.get('vacancies', {})
                    metadata = data.get('metadata', {})
                    total_processed = len(self.processed_vacancies)
                    self.logger.info(f"Загружено {total_processed} обработанных вакансий")
            else:
                self.logger.info("Файл processed_vacancies.json не найден, будет создан новый")
        except Exception as e:
            self.logger.error(f"Ошибка загрузки processed_vacancies.json: {str(e)}")
            self.processed_vacancies = {}
    
    async def load_profile(self) -> CandidateProfile:
        """
        Загрузка профиля кандидата из JSON файла.

        Читает данные кандидата из файла data/candidate_profile.json,
        включая личную информацию, навыки, опыт работы и готовые ответы
        на типичные вопросы работодателей.

        Returns:
            CandidateProfile: Объект с данными кандидата

        Raises:
            FileNotFoundError: Если файл профиля не найден
            json.JSONDecodeError: Если файл содержит некорректный JSON
        """
        profile_path = Path("data/candidate_profile.json")

        if not profile_path.exists():
            raise FileNotFoundError("candidate_profile.json not found")

        with open(profile_path, 'r', encoding='utf-8') as f:
            profile_data = json.load(f)

        return CandidateProfile(**profile_data)
    
    async def apply(self, url: str, message: str = "") -> dict:
        """
        Основной метод отклика на вакансию через Web-Use + GPT-4o.

        Выполняет полный цикл отклика: от загрузки профиля до отправки формы.
        Обрабатывает дополнительные вопросы работодателя, заполняет все поля
        и подтверждает достоверность данных.

        Args:
            url (str): URL вакансии на HH.ru
            message (str, optional): Пользовательское сопроводительное письмо.
                                   Если не указано, используется шаблон из профиля.

        Returns:
            dict: Результат операции с ключами:
                - 'status': 'success', 'error', 'skipped'
                - 'message': Описание результата

        Raises:
            Exception: При критических ошибках (сетевые проблемы, отсутствие сессии)
        """
        try:
            self.logger.info(f"Начало отклика на вакансию: {url}")

            force_apply = False
            try:
                if "force=1" in url:
                    force_apply = True
            except Exception:
                force_apply = False

            # Проверка, была ли вакансия уже обработана
            if not force_apply:
                is_processed, processed_data = self._is_vacancy_processed(url)
                if is_processed:
                    status = processed_data.get('status', 'unknown')
                    processed_at = processed_data.get('processed_at', 'unknown')
                    # Разрешаем повторить попытку, если прошлый раз была ошибка
                    if str(status).lower() != "error":
                        self.logger.info(f"Вакансия уже была обработана ранее (статус: {status}, время: {processed_at})")
                        return {
                            "status": "already_processed",
                            "message": f"Vacancy was already processed on {processed_at} with status: {status}",
                            "previous_result": processed_data
                        }
                    self.logger.info(f"Повторная попытка для вакансии после ошибки (время: {processed_at})")
            else:
                self.logger.info("force=1: пропускаем проверку processed_vacancies")

            # Загрузка профиля
            self.logger.info("Загрузка профиля кандидата...")
            profile = await self.load_profile()
            self.logger.info(f"Профиль загружен: {profile.full_name}")
            
            # Использование browser_manager для получения страницы
            self.logger.info("Инициализация браузерного контекста...")
            from .browser import browser_manager
            async with browser_manager.get_page(use_session=True) as page:
                self.logger.info("Браузерная страница создана")

                # Сначала переходим на главную страницу HH.ru
                self.logger.info("Переход на главную страницу HH.ru...")
                try:
                    await page.goto("https://hh.ru", timeout=30000)
                    await page.wait_for_load_state('networkidle', timeout=15000)
                except Exception as e:
                    self.logger.warning(f"Не удалось загрузить главную страницу, пробуем без ожидания: {str(e)}")
                    # Продолжаем без полной загрузки главной страницы

                # Проверяем авторизацию
                if not await self._is_logged_in(page):
                    self.logger.error("Пользователь не авторизован. Нужно обновить сессию.")
                    self._save_processed_vacancy(url, "error", "session expired - need to login again")
                    return {"status": "error", "message": "session expired - need to login again"}

                # Небольшая пауза
                await page.wait_for_timeout(2000)

                # Теперь переходим на страницу вакансии
                self.logger.info(f"Переход на страницу вакансии: {url}")
                await page.goto(url, timeout=120000)  # 120 секунд
                self.logger.info("Ожидание загрузки страницы...")
                # networkidle на hh.ru часто не наступает из-за фоновых запросов, поэтому ждём более устойчиво
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=self.settings.page_timeout)
                except Exception as e:
                    self.logger.warning(f"domcontentloaded не дождались: {str(e)}")
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                except Exception:
                    pass
                self.logger.info("Страница загружена")
                
                # Сохраняем скриншот после загрузки страницы
                self.logger.info("Сохранение скриншота после загрузки страницы...")
                await self._save_screenshot(page, "after_page_load")

                # Проверка на капчу
                self.logger.info("Проверка на наличие капчи...")
                if await self._is_captcha_present(page):
                    self.logger.warning("Обнаружена капча, пропуск вакансии")
                    self._save_processed_vacancy(url, "skipped", "captcha detected", profile.full_name)
                    return {"status": "skipped", "message": "captcha detected"}

                # Если это прямая ссылка на форму отклика, не ищем кнопку "Откликнуться"
                if "/applicant/vacancy_response" in url:
                    self.logger.info("Обнаружена страница формы отклика (vacancy_response), переходим к заполнению формы")

                    self.logger.info("Сохранение скриншота после загрузки формы отклика...")
                    await self._save_screenshot(page, "response_form_loaded")

                    self.logger.info("Заполнение формы отклика...")
                    success = await self._fill_application_form(page, profile, message)
                    if success:
                        self._save_processed_vacancy(url, "success", "application submitted", profile.full_name)
                        return {"status": "success", "message": "application submitted"}

                    self._save_processed_vacancy(url, "error", "failed to submit application", profile.full_name)
                    return {"status": "error", "message": "failed to submit application"}

                # Быстрые проверки, почему кнопки может не быть
                try:
                    if await page.locator("text=Вы откликнулись").count() > 0:
                        self.logger.info("Вы уже откликнулись на эту вакансию")
                        self._save_processed_vacancy(url, "skipped", "already applied", profile.full_name)
                        return {"status": "skipped", "message": "already applied"}
                except Exception:
                    pass

                # Поиск и клик по кнопке "Откликнуться"
                self.logger.info("Поиск кнопки 'Откликнуться'...")
                await self._click_apply_button(page)

                # Проверяем, открылось ли модальное окно для простого отклика
                modal_opened = await self._handle_modal_response(page, profile, message)
                if modal_opened:
                    self.logger.info("Обработка модального окна завершена")
                    # Сохраняем результат обработки
                    self._save_processed_vacancy(url, "success", "application submitted via modal", profile.full_name)
                    return {"status": "success", "message": "application submitted via modal"}
                self.logger.info("Кнопка 'Откликнуться' нажата")

                # Сохраняем скриншот после клика на отклик
                self.logger.info("Сохранение скриншота после клика на отклик...")
                await self._save_screenshot(page, "after_apply_click")

                # Заполнение формы через Web-Use
                self.logger.info("Заполнение формы отклика...")
                success = await self._fill_application_form(page, profile, message)
                
                if success:
                    self.logger.info("Форма успешно заполнена и отправлена")
                    # Ждем 5 секунд для наблюдения результата
                    await asyncio.sleep(5)

                    # Сохраняем результат обработки
                    self._save_processed_vacancy(url, "success", "application submitted", profile.full_name)
                    return {"status": "success", "message": "application submitted"}
                else:
                    self.logger.error("Не удалось заполнить форму")
                    self._save_processed_vacancy(url, "error", "form filling failed", profile.full_name)
                    return {"status": "error", "message": "form filling failed"}

        except Exception as e:
            self.logger.error(f"Ошибка при отклике: {str(e)}")
            self._save_processed_vacancy(url, "error", str(e))
            return {"status": "error", "message": str(e)}
    
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
            '[data-qa="vacancy-response-link-top"]',
            '[data-qa="vacancy-response-link-bottom"]',
            '[data-qa="vacancy-response-link"]',
            'button[data-qa="vacancy-response-link"]',
            'button[data-qa="vacancy-response-submit"]',
            'button:has-text("Откликнуться")',
            'a:has-text("Откликнуться")',
        ]

        for selector in apply_selectors:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                continue

            try:
                self.logger.info(f"Селектор кнопки отклика: {selector} (найдено: {count})")
            except Exception:
                pass

            if count == 0:
                continue

            for i in range(count):
                el = locator.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    try:
                        await el.scroll_into_view_if_needed()
                    except Exception:
                        pass

                    # Для <a> is_enabled может падать, поэтому в try
                    try:
                        if not await el.is_enabled():
                            continue
                    except Exception:
                        pass

                    await el.click()
                    await page.wait_for_timeout(1500)
                    return
                except Exception:
                    continue

        try:
            await self._save_screenshot(page, "apply_button_not_found")
        except Exception:
            pass
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
            return await self._basic_form_fill(page, profile, cover_letter)
            
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
        """
        Базовое заполнение формы отклика с поддержкой вопросов работодателя.

        Автоматически находит и заполняет:
        - Стандартные поля (email, телефон, город)
        - Поле сопроводительного письма
        - Дополнительные вопросы работодателя
        - Чекбоксы подтверждения достоверности данных

        Args:
            page (Page): Экземпляр страницы Playwright
            profile (CandidateProfile): Данные кандидата
            cover_letter (str): Текст сопроводительного письма
        """

        # На некоторых формах ответы спрятаны, пока не выбран вариант "Свой вариант"
        # Сначала раскрываем такие поля, чтобы они попали в общий список input/textarea
        try:
            own_option_selectors = [
                'label:has-text("Свой вариант")',
                'button:has-text("Свой вариант")',
                'a:has-text("Свой вариант")',
                'div[role="radio"]:has-text("Свой вариант")',
                'span[role="radio"]:has-text("Свой вариант")',
                'div:has-text("Свой вариант")',
                'span:has-text("Свой вариант")',
            ]
            clicked_any = False
            for sel in own_option_selectors:
                try:
                    loc = page.locator(sel)
                    cnt = await loc.count()
                    if cnt == 0:
                        continue
                    for i in range(min(cnt, 5)):
                        try:
                            el = loc.nth(i)
                            if not await el.is_visible():
                                continue
                            try:
                                await el.scroll_into_view_if_needed()
                            except Exception:
                                pass
                            await el.click()
                            clicked_any = True
                            await page.wait_for_timeout(200)
                        except Exception:
                            continue
                except Exception:
                    continue
            if clicked_any:
                self.logger.info("✅ Выбраны варианты 'Свой вариант' для раскрытия скрытых полей")
        except Exception:
            pass

        # Пытаемся раскрыть блок сопроводительного письма (на HH он часто скрыт за тогглом)
        await self._try_open_cover_letter_block(page)

        # Сначала найдем все вопросы на странице
        questions = await self._find_all_questions(page)
        self.logger.info(f"Найдено {len(questions)} вопросов на странице: {[q[:50] + '...' if len(q) > 50 else q for q in questions]}")

        # Заполнение текстовых полей и textarea
        text_inputs = await page.query_selector_all('input[type="text"], input[type="email"], input[type="tel"], textarea')
        self.logger.info(f"Найдено {len(text_inputs)} текстовых полей для заполнения")

        # Пытаемся заполнить сопроводительное письмо прицельно (часто это отдельное поле без placeholder)
        cover_letter_filled = False
        cover_letter_selectors = [
            'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
            'textarea[data-qa="vacancy-response-popup-letter"]',
            'textarea[data-qa*="letter"]',
            'textarea[name*="letter"]',
            'textarea[placeholder="Сопроводительное письмо"]',
            'textarea[placeholder*="сопровод"]',
            'xpath=//*[normalize-space()="Сопроводительное письмо"]/following::textarea[1]',
            'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/following::textarea[1]',
            'xpath=//*[normalize-space()="Сопроводительное письмо"]/following::*[@contenteditable="true"][1]',
            'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/following::*[@contenteditable="true"][1]',
        ]
        for sel in cover_letter_selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    # contenteditable элементы не поддерживают fill()
                    try:
                        await loc.first.fill(cover_letter)
                    except Exception:
                        await loc.first.click()
                        await page.keyboard.press("Meta+A")
                        await page.keyboard.type(cover_letter)
                    await page.wait_for_timeout(300)
                    cover_letter_filled = True
                    self.logger.info(f"✅ Сопроводительное письмо заполнено: {sel}")
                    break
            except Exception:
                continue

        # Иногда textarea появляется только после клика по тогглу; если не нашли — попробуем ещё раз
        if not cover_letter_filled:
            try:
                await self._try_open_cover_letter_block(page)
            except Exception:
                pass
            for sel in cover_letter_selectors:
                try:
                    loc = page.locator(sel)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        try:
                            await loc.first.fill(cover_letter)
                        except Exception:
                            await loc.first.click()
                            await page.keyboard.press("Meta+A")
                            await page.keyboard.type(cover_letter)
                        await page.wait_for_timeout(300)
                        cover_letter_filled = True
                        self.logger.info(f"✅ Сопроводительное письмо заполнено (2-я попытка): {sel}")
                        break
                except Exception:
                    continue

        # Если на форме есть обязательное поле письма, но мы его не заполнили — дальше нет смысла жать отправку
        if not cover_letter_filled:
            required_letter_locators = [
                'textarea[required]',
                'textarea[aria-required="true"]',
                'textarea[data-qa*="letter"][required]',
                'textarea[data-qa*="letter"][aria-required="true"]',
            ]
            for sel in required_letter_locators:
                try:
                    loc = page.locator(sel)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        self.logger.error("❌ Сопроводительное письмо обязательно, но не заполнено")
                        try:
                            await self._save_screenshot(page, "required_cover_letter_missing")
                        except Exception:
                            pass
                        return False
                except Exception:
                    continue

            # На части форм HH обязательность не выражена атрибутом required,
            # но блок "Сопроводительное письмо" остаётся с кнопкой "Добавить".
            # Если после попыток заполнения всё ещё видим "Добавить" в этом блоке — считаем, что письмо не добавлено.
            try:
                add_btn = page.locator('xpath=//*[normalize-space()="Сопроводительное письмо"]/following::*[(self::button or self::a or @role="button" or self::div or self::span) and normalize-space()="Добавить"][1]')
                if await add_btn.count() > 0 and await add_btn.first.is_visible():
                    self.logger.error("❌ Сопроводительное письмо не добавлено (кнопка 'Добавить' всё ещё видима)")
                    try:
                        await self._save_screenshot(page, "cover_letter_not_added")
                    except Exception:
                        pass
                    return False
            except Exception:
                pass

        for i, input_element in enumerate(text_inputs):
            try:
                placeholder = await input_element.get_attribute('placeholder') or ""
                name = await input_element.get_attribute('name') or ""
                data_qa = await input_element.get_attribute('data-qa') or ""
                tag_name = await input_element.evaluate("el => el.tagName.toLowerCase()")
                is_visible = await input_element.is_visible()
                is_enabled = await input_element.is_enabled()

                # Определяем вопрос, связанный с конкретным полем
                question_text = ""
                try:
                    question_text = await self._find_question_text(page, input_element)
                except Exception:
                    question_text = ""

                self.logger.info(f"Поле {i+1}: tag={tag_name}, name='{name}', data-qa='{data_qa}', placeholder='{placeholder}', question='{question_text[:100] if question_text else 'N/A'}', visible={is_visible}, enabled={is_enabled}")

                if not is_visible or not is_enabled:
                    continue

                # Если это поле сопроводительного, но мы его уже заполнили прицельно выше — пропускаем
                is_cover_letter_field = (
                    ('letter' in (name or '').lower()) or
                    ('cover' in (name or '').lower()) or
                    ('letter' in (data_qa or '').lower()) or
                    ('сопровод' in (placeholder or '').lower()) or
                    ('сопровод' in (question_text or '').lower())
                )
                if cover_letter_filled and is_cover_letter_field:
                    continue

                value = ""
                if 'email' in name.lower() or 'email' in placeholder.lower():
                    value = profile.email
                elif 'phone' in name.lower() or 'телефон' in placeholder.lower():
                    value = profile.phone
                elif 'город' in placeholder.lower() or 'city' in name.lower():
                    value = profile.city
                elif is_cover_letter_field:
                    value = cover_letter
                    cover_letter_filled = True
                    self.logger.info(f"Найдено поле сопроводительного письма: '{placeholder or name}'")
                else:
                    # Обработка специфических вопросов работодателя
                    if question_text:
                        value = self._get_answer_for_question(question_text, "", profile)
                        self.logger.info(f"Найден ответ для вопроса '{question_text[:50]}...': '{value}'")
                    else:
                        # Если вопрос не найден, попробуем определить по номеру поля
                        value = self._get_answer_by_field_index(i, profile)
                        self.logger.info(f"Ответ по индексу поля {i}: '{value}'")

                if value:
                    self.logger.info(f"Заполняем поле: '{placeholder or name}' значением: '{value}'")

                    # Используем fill() для всех типов полей
                    await input_element.fill(value)
                    await page.wait_for_timeout(300)

                    # Проверяем, что значение установлено
                    try:
                        current_value = await input_element.input_value()
                        if current_value == value:
                            self.logger.info(f"✅ Успешно заполнено поле '{placeholder or name}'")
                        else:
                            self.logger.warning(f"❌ Поле '{placeholder or name}' не заполнилось. Ожидалось: '{value}', получено: '{current_value}'")
                    except:
                        # Для некоторых элементов input_value может не работать
                        self.logger.info(f"✓ Поле '{placeholder or name}' заполнено (проверка невозможна)")
                else:
                    self.logger.info(f"Пропускаем поле '{placeholder or name}' - нет подходящего значения")

            except Exception as e:
                self.logger.warning(f"Ошибка при обработке поля {i+1}: {str(e)}")
                continue

        # Обработка radio buttons и checkboxes
        await self._handle_radio_buttons(page, profile)

        # Небольшая пауза перед финальными действиями
        await page.wait_for_timeout(1000)
        
        # Нажатие кнопки отправки
        submit_selectors = [
            'button[data-qa="vacancy-response-submit-popup"]',
            'button:has-text("Откликнуться")',
            'button:has-text("Откликнуться без текста")',
            'button:has-text("Отправить")',
            'button[type="submit"]'
        ]
        
        self.logger.info("Поиск кнопки отправки формы...")
        for selector in submit_selectors:
            try:
                loc = page.locator(selector)
                cnt = await loc.count()
                if cnt == 0:
                    continue
                for i in range(cnt):
                    try:
                        button = loc.nth(i)
                        if not await button.is_visible():
                            continue
                        try:
                            if not await button.is_enabled():
                                continue
                        except Exception:
                            pass
                        try:
                            await button.scroll_into_view_if_needed()
                        except Exception:
                            pass

                        self.logger.info(f"Найдена и нажимается кнопка отправки: {selector}")
                        await button.click()
                        await page.wait_for_timeout(2500)

                        if await self._is_application_success(page):
                            self.logger.info("✅ Подтверждение отклика найдено")
                            return True

                        errors = await self._get_validation_errors(page)
                        if errors:
                            self.logger.error(f"❌ Ошибки валидации после отправки: {errors[:3]}")
                            try:
                                await self._save_screenshot(page, "validation_errors")
                            except Exception:
                                pass
                            return False

                        # Если нет подтверждения и нет ошибок — считаем неуспехом
                        self.logger.error("❌ Нет подтверждения отправки отклика")
                        try:
                            await self._save_screenshot(page, "no_success_confirmation")
                        except Exception:
                            pass
                        return False
                    except Exception:
                        continue
            except Exception as e:
                self.logger.warning(f"Не удалось найти кнопку {selector}: {str(e)}")
                continue

        self.logger.error("Не найдена ни одна кнопка отправки формы!")
        return False


    async def _try_open_cover_letter_block(self, page: Page) -> bool:
        """Пытается раскрыть блок/поле сопроводительного письма (если оно скрыто за кнопкой/ссылкой)."""
        toggle_selectors = [
            # Приоритет: ссылка/кнопка "Добавить" рядом с заголовком "Сопроводительное письмо"
            'xpath=//*[normalize-space()="Сопроводительное письмо"]/following-sibling::*[normalize-space()="Добавить"][1]',
            'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/following-sibling::*[normalize-space()="Добавить"][1]',
            'xpath=//*[normalize-space()="Сопроводительное письмо"]/ancestor::*[self::div or self::section or self::fieldset][1]//*[normalize-space()="Добавить" and (self::button or self::a or @role="button")][1]',
            'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/ancestor::*[self::div or self::section or self::fieldset][1]//*[normalize-space()="Добавить" and (self::button or self::a or @role="button")][1]',
            'button[data-qa="vacancy-response-letter-toggle"]',
            'button:has-text("Сопроводительное письмо")',
            'a:has-text("Сопроводительное письмо")',
            'div:has-text("Сопроводительное письмо")',
            'span:has-text("Сопроводительное письмо")',
            # На некоторых формах рядом с заголовком есть отдельная кнопка "Добавить"
            'xpath=//*[normalize-space()="Сопроводительное письмо"]/following::*[(self::button or self::a or self::div or self::span) and normalize-space()="Добавить"][1]',
            'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/following::*[(self::button or self::a or self::div or self::span) and normalize-space()="Добавить"][1]',
            'xpath=//*[normalize-space()="Сопроводительное письмо"]/following::*[@role="button" and normalize-space()="Добавить"][1]',
            'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/following::*[@role="button" and normalize-space()="Добавить"][1]',
            'button:has-text("Добавить сопроводительное")',
            'a:has-text("Добавить сопроводительное")',
            'div[role="button"]:has-text("Добавить")',
            'span[role="button"]:has-text("Добавить")',
        ]

        for sel in toggle_selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() == 0:
                    continue
                el = loc.first
                if not await el.is_visible():
                    continue
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    await el.click()
                    await page.wait_for_timeout(400)
                    try:
                        self.logger.info(f"✅ Раскрыт блок сопроводительного письма: {sel}")
                    except Exception:
                        pass
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        return False

    async def _is_logged_in(self, page: Page) -> bool:
        """Проверка авторизации пользователя на HH.ru."""
        try:
            # Ищем элементы, которые есть только у авторизованного пользователя
            logged_in_selectors = [
                '[data-qa="mainmenu_applicantProfile"]',  # Профиль соискателя
                '.HH-Supernova-MainMenu-Item[data-qa="mainmenu_applicantProfile"]',
                'a[href*="applicant"]',  # Ссылка на профиль
                '.bloko-link[href*="resume"]'  # Ссылка на резюме
            ]

            for selector in logged_in_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element and await element.is_visible():
                        self.logger.info("Пользователь авторизован")
                        return True
                except:
                    continue

            # Проверяем отсутствие элементов входа
            login_selectors = [
                '[data-qa="login"]',
                'a[href*="login"]',
                'button:has-text("Войти")'
            ]

            for selector in login_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element and await element.is_visible():
                        self.logger.warning("Найдена кнопка входа - пользователь не авторизован")
                        return False
                except:
                    continue

            # Если не нашли ни авторизации, ни входа - считаем авторизованным
            self.logger.info("Статус авторизации неясен, продолжаем")
            return True

        except Exception as e:
            self.logger.warning(f"Ошибка проверки авторизации: {str(e)}")
            return True  # В случае ошибки продолжаем

    async def _find_all_questions(self, page: Page) -> List[str]:
        """
        Поиск всех вопросов работодателя на странице формы отклика.

        Ищет элементы, содержащие текст вопросов (заканчивающийся на '?'),
        и возвращает их в порядке появления на странице.

        Args:
            page (Page): Экземпляр страницы Playwright

        Returns:
            List[str]: Список найденных вопросов (максимум 3)
        """
        questions = []
        try:
            # Ищем элементы, содержащие вопросы (текст заканчивается на ?)
            question_selectors = [
                'div:has-text("?")',
                'label:has-text("?")',
                'span:has-text("?")',
                'p:has-text("?")',
                '[class*="question"]',
                '[class*="task"]'
            ]

            found_texts = set()  # Чтобы избежать дубликатов

            for selector in question_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for elem in elements:
                        if await elem.is_visible():
                            text = await elem.inner_text()
                            text = text.strip()
                            if text.endswith('?') and len(text) > 10 and text not in found_texts:
                                questions.append(text)
                                found_texts.add(text)
                except:
                    continue

            # Сортируем по порядку появления на странице
            return questions[:3]  # Берем максимум 3 вопроса

        except Exception as e:
            self.logger.warning(f"Ошибка поиска вопросов: {str(e)}")
            return []

    async def _find_question_text(self, page: Page, input_element) -> str:
        """Поиск текста вопроса, связанного с полем ввода."""
        try:
            # Получаем name поля для поиска соответствующего вопроса
            field_name = await input_element.get_attribute('name') or ""
            if field_name:
                # Ищем элемент с вопросом по data-qa или другим атрибутам
                question_selectors = [
                    f'[data-qa*="question"][data-field="{field_name}"]',
                    f'[class*="question"][data-field="{field_name}"]',
                    f'div:has-text("?")',
                    '.task__question',
                    '.vacancy-question'
                ]

                for selector in question_selectors:
                    try:
                        question_elem = await page.query_selector(selector)
                        if question_elem and await question_elem.is_visible():
                            text = await question_elem.inner_text()
                            if text and text.strip().endswith('?'):
                                return text.strip()
                    except:
                        continue

            # Ищем вопрос в ближайшем родительском контейнере
            parent_selectors = [
                'xpath=ancestor::div[contains(@class, "task")][1]',
                'xpath=ancestor::div[contains(@class, "question")][1]',
                'xpath=ancestor::fieldset[1]',
                'xpath=ancestor::div[1]'
            ]

            for parent_sel in parent_selectors:
                try:
                    parent = await input_element.query_selector(parent_sel)
                    if parent:
                        text_content = await parent.inner_text()
                        # Ищем текст, заканчивающийся на ?
                        lines = text_content.split('\n')
                        for line in lines:
                            line = line.strip()
                            if line.endswith('?') and len(line) > 10:
                                return line
                except:
                    continue

            return ""
        except Exception as e:
            self.logger.warning(f"Ошибка поиска текста вопроса: {str(e)}")
            return ""

    def _get_answer_for_question(self, question_text: str, name: str, profile: CandidateProfile) -> str:
        """
        Получение ответа на вопрос работодателя с использованием готовых ответов.

        Сначала проверяет готовые ответы из файла common_questions.json,
        затем использует данные профиля кандидата для персонализации.
        Это позволяет экономить токены AI на типичных вопросах.

        Args:
            question_text (str): Текст вопроса
            name (str): Имя поля (для обратной совместимости)
            profile (CandidateProfile): Данные кандидата

        Returns:
            str: Подходящий ответ или пустая строка если вопрос не распознан
        """
        if not question_text:
            return ""

        question_text_lower = question_text.lower()

        # Сначала проверяем готовые ответы из файла
        best_match = self._find_best_question_match(question_text_lower)
        if best_match:
            question_data = self.common_questions[best_match]
            answer_template = question_data['answer']

            # Подставляем данные из профиля в шаблон
            answer = self._format_answer_template(answer_template, profile)
            self.logger.info(f"✅ Найден готовый ответ для вопроса: '{question_text[:50]}...' -> '{answer}'")
            return answer

        # Если готовый ответ не найден, используем старую логику для обратной совместимости
        self.logger.info(f"🔍 Готовый ответ не найден, анализирую вопрос: '{question_text[:50]}...'")

        # Коммерческий опыт в Python
        if 'python' in question_text_lower and ('опыт' in question_text_lower or 'experience' in question_text_lower):
            return f"{profile.experience_years} лет коммерческого опыта в разработке на Python"

        # Опыт разработки на Java
        if 'java' in question_text_lower and ('опыт' in question_text_lower or 'experience' in question_text_lower):
            has_java = any('java' in skill.lower() for skill in profile.skills)
            return "Да, есть опыт разработки на Java" if has_java else "Нет опыта разработки на Java"

        # Зарплатные ожидания
        if 'зарплат' in question_text_lower or 'salary' in question_text_lower or 'сумм' in question_text_lower:
            return profile.salary_expectations

        # Подтверждение достоверности данных
        if 'подтверждаете' in question_text_lower or 'достоверн' in question_text_lower:
            return "Да, подтверждаю достоверность указанных данных"

        # Проверяем готовые ответы из профиля
        for key, answer in profile.answers.items():
            if key.lower() in question_text_lower:
                return answer

        return ""

    def _get_answer_by_field_index(self, index: int, profile: CandidateProfile) -> str:
        """Получение ответа по порядковому номеру поля (если текст вопроса не найден)."""
        # На основе типичных вопросов работодателей
        if index == 0:  # Первый вопрос - обычно про опыт Python
            return f"{profile.experience_years} лет коммерческого опыта в разработке на Python"
        elif index == 1:  # Второй вопрос - опыт Java
            has_java = any('java' in skill.lower() for skill in profile.skills)
            return "Да, есть опыт разработки на Java" if has_java else "Нет опыта разработки на Java"
        elif index == 2:  # Третий вопрос - зарплата
            return profile.salary_expectations

        return ""

    def _find_best_question_match(self, question_text: str) -> str:
        """
        Поиск лучшего совпадения вопроса с готовыми ответами.

        Args:
            question_text (str): Текст вопроса в нижнем регистре

        Returns:
            str: Ключ лучшего совпадения или пустая строка
        """
        best_match = ""
        best_score = 0

        for key, question_data in self.common_questions.items():
            patterns = question_data.get('patterns', [])
            priority = question_data.get('priority', 5)

            for pattern in patterns:
                try:
                    p = (pattern or "").strip()
                    if not p:
                        continue

                    matched = False
                    # Если похоже на regex (используются метасимволы), пробуем re.search
                    if any(ch in p for ch in (".*", "^", "$", "[", "]", "(", ")", "\\")):
                        try:
                            if re.search(p, question_text, flags=re.IGNORECASE):
                                matched = True
                        except re.error:
                            matched = False
                    else:
                        if p.lower() in question_text:
                            matched = True

                    if matched:
                        score = len(p) * priority  # Чем длиннее паттерн и выше приоритет, тем лучше
                        if score > best_score:
                            best_score = score
                            best_match = key
                except Exception:
                    continue

        return best_match

    def _format_answer_template(self, template: str, profile: CandidateProfile) -> str:
        """
        Форматирование шаблона ответа с данными из профиля кандидата.

        Args:
            template (str): Шаблон ответа с плейсхолдерами
            profile (CandidateProfile): Данные кандидата

        Returns:
            str: Отформатированный ответ
        """
        # Создаем словарь с данными профиля для подстановки
        profile_data = {
            'experience_years': profile.experience_years,
            'salary_expectations': profile.salary_expectations,
            'position': profile.position,
            'skills': ', '.join(profile.skills[:3]),  # Первые 3 навыка
            'full_name': profile.full_name,
            'email': profile.email,
            'phone': profile.phone,
            'city': profile.city
        }

        # Добавляем данные из answers профиля
        profile_data.update(profile.answers)

        try:
            return template.format(**profile_data)
        except KeyError as e:
            self.logger.warning(f"Не удалось подставить данные в шаблон: {e}, шаблон: {template}")
            return template

    def _is_vacancy_processed(self, url: str) -> tuple[bool, dict]:
        """
        Проверка, была ли вакансия уже обработана.

        Args:
            url (str): URL вакансии

        Returns:
            tuple[bool, dict]: (обработана ли, данные обработки)
        """
        vacancy_id = self._extract_vacancy_id(url)
        if vacancy_id in self.processed_vacancies:
            return True, self.processed_vacancies[vacancy_id]
        return False, {}

    def _save_processed_vacancy(self, url: str, status: str, message: str = "", profile_name: str = ""):
        """
        Сохранение результата обработки вакансии.

        Args:
            url (str): URL вакансии
            status (str): Статус обработки (success, error, skipped, already_processed)
            message (str): Сообщение с результатом
            profile_name (str): Имя профиля, использованного для отклика
        """
        vacancy_id = self._extract_vacancy_id(url)

        vacancy_data = {
            "url": url,
            "vacancy_id": vacancy_id,
            "status": status,
            "message": message,
            "processed_at": datetime.now().isoformat(),
            "profile_used": profile_name or "default"
        }

        self.processed_vacancies[vacancy_id] = vacancy_data

        # Сохраняем в файл
        try:
            processed_file = Path("data/processed_vacancies.json")
            data = {
                "vacancies": self.processed_vacancies,
                "metadata": {
                    "version": "1.0",
                    "description": "Отслеживание обработанных вакансий для избежания дублирования",
                    "created": "2026-01-22",
                    "total_processed": len(self.processed_vacancies),
                    "last_updated": datetime.now().isoformat()
                }
            }

            with open(processed_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.logger.info(f"Сохранен статус вакансии {vacancy_id}: {status}")
        except Exception as e:
            self.logger.error(f"Ошибка сохранения processed_vacancies.json: {str(e)}")

    def _extract_vacancy_id(self, url: str) -> str:
        """
        Извлечение ID вакансии из URL.

        Args:
            url (str): URL вакансии

        Returns:
            str: ID вакансии
        """
        # Пример URL: https://hh.ru/vacancy/129713934
        if '/vacancy/' in url:
            parts = url.split('/vacancy/')
            if len(parts) > 1:
                vacancy_id = parts[1].split('?')[0].split('/')[0]  # Убираем параметры и слэши
                return vacancy_id

        # Пример URL: https://hh.ru/applicant/vacancy_response?vacancyId=129815486&...
        if 'vacancyId=' in url:
            try:
                after = url.split('vacancyId=', 1)[1]
                vacancy_id = after.split('&')[0].split('#')[0]
                if vacancy_id:
                    return vacancy_id
            except Exception:
                pass
        return url  # Если не удалось извлечь, возвращаем полный URL

    async def _handle_radio_buttons(self, page: Page, profile: CandidateProfile):
        """
        Обработка radio buttons и checkboxes подтверждения данных.

        Автоматически находит и отмечает элементы подтверждения
        достоверности предоставленной информации (требование ТК РФ).

        Args:
            page (Page): Экземпляр страницы Playwright
            profile (CandidateProfile): Данные кандидата (не используется в этой функции)
        """
        self.logger.info("🔍 Начинаем обработку radio buttons и checkboxes...")
        try:
            # Ищем radio buttons и checkboxes связанные с подтверждением данных
            radio_selectors = [
                'input[type="radio"][value*="подтверждаю"]',
                'input[type="radio"][value*="Да"]',
                'input[type="radio"]',
                'input[type="checkbox"][value*="подтверждаю"]',
                'input[type="checkbox"]'
            ]

            found_any = False
            for selector in radio_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    self.logger.info(f"Найдено {len(elements)} элементов по селектору: {selector}")

                    for i, radio in enumerate(elements):
                        if await radio.is_visible():
                            is_checked = await radio.is_checked()
                            radio_value = await radio.get_attribute('value') or ""
                            radio_text = await self._get_radio_label_text(page, radio)

                            self.logger.info(f"Radio/Checkbox {i+1}: value='{radio_value}', text='{radio_text[:50]}...', checked={is_checked}")

                            # Выбираем подходящий вариант
                            if (radio_value and 'подтверждаю' in radio_value.lower()) or \
                               (radio_text and 'подтверждаю' in radio_text.lower()):
                                if not is_checked:
                                    await radio.check()
                                    self.logger.info(f"✅ Отмечен чекбокс/radio: '{radio_text[:50]}...'")
                                    found_any = True
                                break

                except Exception as e:
                    self.logger.warning(f"Ошибка с селектором {selector}: {str(e)}")
                    continue

            if not found_any:
                self.logger.warning("❌ Не найдено подходящих чекбоксов/radio buttons для подтверждения данных")

            # Отдельно: отвечаем на группы radio (Да/Нет/Свой вариант) по тексту вопроса
            try:
                radios = await page.query_selector_all('input[type="radio"]')
                by_name: dict[str, list] = {}
                for r in radios:
                    try:
                        if not await r.is_visible():
                            continue
                        name = await r.get_attribute('name') or ""
                        if not name:
                            continue
                        by_name.setdefault(name, []).append(r)
                    except Exception:
                        continue

                for name, group in by_name.items():
                    try:
                        if not group:
                            continue
                        # если уже выбран вариант, но он не совпадает с желаемым — переопределяем
                        checked_label = ""
                        for r in group:
                            try:
                                if await r.is_checked():
                                    checked_label = (await self._get_radio_label_text(page, r) or "").strip()
                                    break
                            except Exception:
                                continue

                        # Пытаемся восстановить текст вопроса по одному из элементов группы
                        question_text = ""
                        try:
                            question_text = await self._find_question_text(page, group[0])
                        except Exception:
                            question_text = ""

                        desired = self._get_answer_for_question(question_text, name, profile)
                        desired_lower = (desired or "").strip().lower()

                        picked = False
                        # 1) пытаемся выбрать по точному совпадению текста label
                        if desired_lower:
                            for r in group:
                                try:
                                    label_text = (await self._get_radio_label_text(page, r) or "").strip()
                                    if label_text and desired_lower in label_text.lower():
                                        # Если уже выбран правильный вариант — ок
                                        if checked_label and desired_lower in checked_label.lower():
                                            picked = True
                                            break
                                        await r.check()
                                        self.logger.info(f"✅ Выбран radio-ответ: вопрос='{question_text[:60]}...', вариант='{label_text[:60]}...'")
                                        picked = True
                                        break
                                except Exception:
                                    continue

                            # Частый кейс: желаемое "готов", а варианты "готов"/"не хотелось бы"
                            if not picked and 'готов' in desired_lower:
                                for r in group:
                                    try:
                                        label_text = (await self._get_radio_label_text(page, r) or "").strip().lower()
                                        if label_text and 'готов' in label_text:
                                            await r.check()
                                            self.logger.info(f"✅ Выбран radio-ответ (fallback готов): вопрос='{question_text[:60]}...', вариант='{label_text[:60]}...'")
                                            picked = True
                                            break
                                    except Exception:
                                        continue

                        # 2) фолбэк: выбираем первый вариант, содержащий "да"
                        if not picked:
                            # Если уже выбран "да" — не трогаем
                            if checked_label and 'да' in checked_label.lower():
                                picked = True
                            
                            for r in group:
                                try:
                                    label_text = (await self._get_radio_label_text(page, r) or "").strip().lower()
                                    value_text = (await r.get_attribute('value') or "").strip().lower()
                                    if not picked and ('да' in label_text or value_text == 'да'):
                                        await r.check()
                                        self.logger.info(f"✅ Выбран radio-ответ (fallback Да): вопрос='{question_text[:60]}...'")
                                        picked = True
                                        break
                                except Exception:
                                    continue

                        # 3) доп. фолбэк: выбираем вариант "готов" если он есть среди вариантов
                        if not picked:
                            for r in group:
                                try:
                                    label_text = (await self._get_radio_label_text(page, r) or "").strip().lower()
                                    if 'готов' in label_text:
                                        await r.check()
                                        self.logger.info(f"✅ Выбран radio-ответ (fallback готов): вопрос='{question_text[:60]}...'")
                                        picked = True
                                        break
                                except Exception:
                                    continue

                    except Exception:
                        continue

                # Отдельно: подтверждающие чекбоксы ("прочел описание", "понятно", "согласен" и т.п.)
                try:
                    checkboxes = await page.query_selector_all('input[type="checkbox"]')
                    for cb in checkboxes:
                        try:
                            if not await cb.is_visible():
                                continue
                            try:
                                if not await cb.is_enabled():
                                    continue
                            except Exception:
                                pass

                            label_text = (await self._get_radio_label_text(page, cb) or "").strip()
                            label_lower = label_text.lower()

                            should_check = (
                                'подтвержда' in label_lower or
                                'достоверн' in label_lower or
                                'проч' in label_lower or
                                'ознаком' in label_lower or
                                'мне понятно' in label_lower or
                                ('понятно' in label_lower and 'ожидан' in label_lower) or
                                'соглас' in label_lower
                            )

                            if should_check and not await cb.is_checked():
                                checked = False
                                try:
                                    await cb.check()
                                    checked = await cb.is_checked()
                                except Exception:
                                    checked = False

                                if not checked:
                                    try:
                                        cb_id = await cb.get_attribute('id') or ""
                                        if cb_id:
                                            label_loc = page.locator(f'label[for="{cb_id}"]')
                                            if await label_loc.count() > 0 and await label_loc.first.is_visible():
                                                await label_loc.first.click()
                                                checked = await cb.is_checked()
                                    except Exception:
                                        pass

                                if not checked:
                                    try:
                                        parent_label = await cb.query_selector('xpath=ancestor::label[1]')
                                        if parent_label and await parent_label.is_visible():
                                            await parent_label.click()
                                            checked = await cb.is_checked()
                                    except Exception:
                                        pass

                                if checked:
                                    self.logger.info(f"✅ Отмечен чекбокс: '{label_text[:80]}...'")
                                else:
                                    self.logger.warning(f"❌ Не удалось отметить чекбокс: '{label_text[:80]}...'")
                        except Exception:
                            continue
                except Exception:
                    pass
            except Exception as e:
                self.logger.warning(f"Ошибка обработки radio-групп: {str(e)}")

        except Exception as e:
            self.logger.warning(f"Ошибка обработки radio buttons: {str(e)}")

    async def _get_radio_label_text(self, page: Page, radio_element) -> str:
        """Получение текста label для radio button."""
        try:
            # Ищем label по for атрибуту
            radio_id = await radio_element.get_attribute('id')
            if radio_id:
                label = await page.query_selector(f'label[for="{radio_id}"]')
                if label:
                    return await label.inner_text()

            # Ищем родительский label
            parent_label = await radio_element.query_selector('xpath=ancestor::label[1]')
            if parent_label:
                return await parent_label.inner_text()

            # Ищем следующий текстовый элемент
            next_text = await page.evaluate("""
                (el) => {
                    let sibling = el.nextSibling;
                    while (sibling) {
                        if (sibling.nodeType === 3 && sibling.textContent.trim()) {
                            return sibling.textContent.trim();
                        }
                        if (sibling.tagName === 'SPAN' || sibling.tagName === 'DIV') {
                            return sibling.textContent.trim();
                        }
                        sibling = sibling.nextSibling;
                    }
                    return '';
                }
            """, radio_element)

            return next_text or ""
        except:
            return ""

    async def _handle_modal_response(self, page: Page, profile: CandidateProfile, custom_message: str) -> bool:
        """
        Обработка модального окна для простых откликов без дополнительных вопросов.

        Args:
            page (Page): Экземпляр страницы Playwright
            profile (CandidateProfile): Данные кандидата
            custom_message (str): Текст сопроводительного письма

        Returns:
            bool: True если модальное окно было обработано, False если его нет
        """
        try:
            # Ждем немного для открытия модального окна
            await page.wait_for_timeout(1000)

            # Ищем селекторы модального окна
            modal_selectors = [
                '[data-qa="vacancy-response-popup"]',
                '.bloko-modal',
                '.HH-VacancyResponsePopup-Content',
                '[class*="modal"][class*="response"]',
                '[class*="popup"][class*="response"]'
            ]

            modal_found = False
            for selector in modal_selectors:
                try:
                    modal = await page.query_selector(selector)
                    if modal and await modal.is_visible():
                        self.logger.info(f"Найдено модальное окно отклика: {selector}")
                        modal_found = True
                        break
                except:
                    continue

            if not modal_found:
                self.logger.info("Модальное окно не найдено, продолжаем с обычной формой")
                return False

            # Обрабатываем модальное окно
            self.logger.info("Обрабатываем модальное окно простого отклика...")

            # Всегда пытаемся добавить сопроводительное письмо
            cover_letter_added = False

            # Иногда textarea уже раскрыта без клика по "Добавить"
            letter_selectors = [
                'textarea[data-qa="vacancy-response-popup-letter"]',
                'textarea[placeholder="Сопроводительное письмо"]',
                'textarea[class*="letter"]',
                'textarea[placeholder*="сопровод"]',
                'textarea'
            ]

            for letter_sel in letter_selectors:
                try:
                    textarea = await page.query_selector(letter_sel)
                    if textarea and await textarea.is_visible():
                        if custom_message:
                            cover_letter = custom_message
                        else:
                            cover_letter = profile.cover_letter_template.format(
                                experience_years=profile.experience_years,
                                position=profile.position,
                                skills=", ".join(profile.skills[:3])
                            )
                        await textarea.fill(cover_letter)
                        self.logger.info(f"✅ Сопроводительное письмо добавлено в модальном окне: '{cover_letter[:50]}...'")
                        cover_letter_added = True
                        break
                except Exception:
                    continue

            # Проверяем, есть ли кнопка "Добавить сопроводительное"
            cover_button_selectors = [
                'button[data-qa="vacancy-response-letter-toggle"]',
                'button:has-text("Добавить сопроводительное")',
                'button:has-text("Сопроводительное письмо")',
                '[class*="letter"][class*="toggle"]',
                'button[class*="letter"]',
                'a:has-text("Добавить")',
                'button:has-text("Добавить")',
                'div[role="button"]:has-text("Добавить")',
                'span[role="button"]:has-text("Добавить")',
                'xpath=//*[normalize-space()="Сопроводительное письмо"]/following::*[(self::button or self::a or self::div or self::span) and normalize-space()="Добавить"][1]',
                'xpath=//*[contains(normalize-space(), "Сопроводительное письмо")]/following::*[(self::button or self::a or self::div or self::span) and normalize-space()="Добавить"][1]',
            ]

            for selector in cover_button_selectors:
                try:
                    if cover_letter_added:
                        break
                    button = await page.query_selector(selector)
                    if button and await button.is_visible():
                        self.logger.info("Найдена кнопка 'Добавить сопроводительное', нажимаем")
                        try:
                            await button.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        await button.click()
                        await page.wait_for_timeout(500)

                        # Дожидаемся появления textarea после клика
                        textarea = None
                        for letter_sel in letter_selectors:
                            try:
                                textarea = await page.wait_for_selector(letter_sel, timeout=2000)
                                if textarea and await textarea.is_visible():
                                    break
                            except Exception:
                                continue

                        if textarea and await textarea.is_visible():
                            if custom_message:
                                cover_letter = custom_message
                            else:
                                cover_letter = profile.cover_letter_template.format(
                                    experience_years=profile.experience_years,
                                    position=profile.position,
                                    skills=", ".join(profile.skills[:3])
                                )
                            await textarea.fill(cover_letter)
                            self.logger.info(f"✅ Сопроводительное письмо добавлено в модальном окне: '{cover_letter[:50]}...'")
                            cover_letter_added = True
                except Exception as e:
                    self.logger.warning(f"Ошибка с кнопкой сопроводительного письма: {str(e)}")

            if not cover_letter_added:
                self.logger.info("Кнопка 'Добавить сопроводительное' не найдена или письмо уже добавлено")

            # Ищем и нажимаем кнопку "Откликнуться" в модальном окне
            submit_selectors = [
                'button[data-qa="vacancy-response-submit-popup"]',
                'button:has-text("Откликнуться")',
                'button[type="submit"]',
                '.bloko-button_kind-primary'
            ]

            for selector in submit_selectors:
                try:
                    submit_button = await page.query_selector(selector)
                    if submit_button and await submit_button.is_visible():
                        self.logger.info(f"Найдена и нажимается кнопка отправки в модальном окне: {selector}")
                        await submit_button.click()
                        await page.wait_for_timeout(2000)
                        if await self._is_application_success(page):
                            self.logger.info("✅ Отклик через модальное окно подтвержден")
                            return True
                        errors = await self._get_validation_errors(page)
                        if errors:
                            self.logger.error(f"❌ Ошибки валидации в модальном окне: {errors[:3]}")
                            try:
                                await self._save_screenshot(page, "modal_validation_errors")
                            except Exception:
                                pass
                        return False
                except Exception as e:
                    self.logger.warning(f"Ошибка с кнопкой отправки {selector}: {str(e)}")

            self.logger.warning("Не удалось найти кнопку отправки в модальном окне")
            return False

        except Exception as e:
            self.logger.error(f"Ошибка обработки модального окна: {str(e)}")
            return False

    async def _is_application_success(self, page: Page) -> bool:
        success_selectors = [
            'text=Отклик отправлен',
            'text=Вы откликнулись',
            'text=Резюме доставлено',
            'text=Откликнуться ещё раз',
        ]
        for sel in success_selectors:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _get_validation_errors(self, page: Page) -> list[str]:
        error_texts: list[str] = []
        selectors = [
            '[data-qa*="vacancy-response-error"]',
            '[data-qa*="error"]',
            '.bloko-form-error',
            '[class*="error"][class*="message"]',
            '[aria-invalid="true"]',
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                if cnt == 0:
                    continue
                for i in range(min(cnt, 5)):
                    try:
                        t = (await loc.nth(i).inner_text()).strip()
                        if t and t not in error_texts:
                            error_texts.append(t)
                    except Exception:
                        continue
            except Exception:
                continue
        return error_texts

    async def _save_screenshot(self, page: Page, suffix: str = ""):
        """
        Сохранение скриншота страницы для отладки и контроля.

        Создает скриншот текущего состояния страницы с временной меткой.
        Полезно для анализа проблем и подтверждения правильности заполнения форм.

        Args:
            page (Page): Экземпляр страницы Playwright
            suffix (str, optional): Суффикс для имени файла скриншота
        """
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
            if suffix:
                screenshot_path = Path(f"logs/webuse_{timestamp}_{suffix}.png")
            else:
                screenshot_path = Path(f"logs/webuse_{timestamp}.png")

            await page.screenshot(path=screenshot_path, full_page=True)
            self.logger.info(f"Скриншот сохранен: {screenshot_path}")
        except Exception as e:
            self.logger.error(f"Ошибка сохранения скриншота: {str(e)}")
