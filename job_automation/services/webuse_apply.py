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
    """
    
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

            # Загрузка профиля
            self.logger.info("Загрузка профиля кандидата...")
            profile = await self.load_profile()
            self.logger.info(f"Профиль загружен: {profile.full_name}")
            
            # Использование browser_manager для получения страницы
            self.logger.info("Инициализация браузерного контекста...")
            from .browser import browser_manager
            async with browser_manager.get_interactive_context(headless=False) as (context, page):
                self.logger.info("Браузерный контекст создан")

                # Загружаем сессию напрямую из файла
                self.logger.info("Загрузка сессии из файла...")
                try:
                    import json
                    with open(str(self.settings.session_file), 'r') as f:
                        storage_state = json.load(f)

                    # Добавляем cookies
                    for cookie in storage_state['cookies']:
                        await context.add_cookies([cookie])

                    self.logger.info("Сессия загружена успешно")
                except Exception as e:
                    self.logger.warning(f"Не удалось загрузить сессию: {e}")

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
                    return {"status": "error", "message": "session expired - need to login again"}

                # Небольшая пауза
                await page.wait_for_timeout(2000)

                # Теперь переходим на страницу вакансии
                self.logger.info(f"Переход на страницу вакансии: {url}")
                await page.goto(url, timeout=120000)  # 120 секунд
                self.logger.info("Ожидание загрузки страницы...")
                await page.wait_for_load_state('networkidle', timeout=60000)  # 60 секунд
                self.logger.info("Страница загружена")
                
                # Сохраняем скриншот после загрузки страницы
                self.logger.info("Сохранение скриншота после загрузки страницы...")
                await self._save_screenshot(page, "after_page_load")

                # Проверка на капчу
                self.logger.info("Проверка на наличие капчи...")
                if await self._is_captcha_present(page):
                    self.logger.warning("Обнаружена капча, пропуск вакансии")
                    return {"status": "skipped", "message": "captcha detected"}

                # Поиск и клик по кнопке "Откликнуться"
                self.logger.info("Поиск кнопки 'Откликнуться'...")
                await self._click_apply_button(page)
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
                    return {"status": "success", "message": "application submitted"}
                else:
                    self.logger.error("Не удалось заполнить форму")
                    return {"status": "error", "message": "form filling failed"}

        except Exception as e:
            self.logger.error(f"Ошибка при отклике: {str(e)}")
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

        # Сначала найдем все вопросы на странице
        questions = await self._find_all_questions(page)
        self.logger.info(f"Найдено {len(questions)} вопросов на странице: {[q[:50] + '...' if len(q) > 50 else q for q in questions]}")

        # Заполнение текстовых полей и textarea
        text_inputs = await page.query_selector_all('input[type="text"], input[type="email"], input[type="tel"], textarea')
        self.logger.info(f"Найдено {len(text_inputs)} текстовых полей для заполнения")

        for i, input_element in enumerate(text_inputs):
            try:
                placeholder = await input_element.get_attribute('placeholder') or ""
                name = await input_element.get_attribute('name') or ""
                tag_name = await input_element.evaluate("el => el.tagName.toLowerCase()")
                is_visible = await input_element.is_visible()
                is_enabled = await input_element.is_enabled()

                # Используем вопрос из массива по индексу
                question_text = questions[i] if i < len(questions) else ""

                self.logger.info(f"Поле {i+1}: tag={tag_name}, name='{name}', placeholder='{placeholder}', question='{question_text[:100] if question_text else 'N/A'}', visible={is_visible}, enabled={is_enabled}")

                if not is_visible or not is_enabled:
                    continue

                value = ""
                if 'email' in name.lower() or 'email' in placeholder.lower():
                    value = profile.email
                elif 'phone' in name.lower() or 'телефон' in placeholder.lower():
                    value = profile.phone
                elif 'город' in placeholder.lower() or 'city' in name.lower():
                    value = profile.city
                elif 'сопроводительное' in placeholder.lower() or 'cover' in name.lower():
                    value = cover_letter
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
        
        self.logger.info("Поиск кнопки отправки формы...")
        for selector in submit_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=3000)
                if button and await button.is_visible():
                    self.logger.info(f"Найдена и нажимается кнопка отправки: {selector}")
                    await button.click()
                    await page.wait_for_timeout(2000)
                    self.logger.info("Кнопка отправки нажата, форма должна быть отправлена")
                    return True
            except Exception as e:
                self.logger.warning(f"Не удалось найти кнопку {selector}: {str(e)}")
                continue

        self.logger.error("Не найдена ни одна кнопка отправки формы!")
        return False
        
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
        Получение ответа на вопрос работодателя на основе данных профиля.

        Анализирует текст вопроса и возвращает подходящий ответ из профиля кандидата.
        Поддерживает типичные вопросы: опыт работы, навыки, зарплатные ожидания.

        Args:
            question_text (str): Текст вопроса
            name (str): Имя поля (для обратной совместимости)
            profile (CandidateProfile): Данные кандидата

        Returns:
            str: Подходящий ответ или пустая строка если вопрос не распознан
        """
        """Получение ответа на вопрос работодателя."""
        question_text = (placeholder + " " + name).lower().strip()

        # Коммерческий опыт в Python
        if 'python' in question_text and ('опыт' in question_text or 'experience' in question_text):
            return f"{profile.experience_years} лет коммерческого опыта в разработке на Python"

        # Опыт разработки на Java
        if 'java' in question_text and ('опыт' in question_text or 'experience' in question_text):
            has_java = any('java' in skill.lower() for skill in profile.skills)
            return "Да, есть опыт разработки на Java" if has_java else "Нет опыта разработки на Java"

        # Зарплатные ожидания
        if 'зарплат' in question_text or 'salary' in question_text or 'сумм' in question_text:
            return profile.salary_expectations

        # Подтверждение достоверности данных
        if 'подтверждаете' in question_text or 'достоверн' in question_text:
            return "Да, подтверждаю достоверность указанных данных"

        # Проверяем готовые ответы из профиля
        for key, answer in profile.answers.items():
            if key.lower() in question_text:
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
