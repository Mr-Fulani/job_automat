# HH.ru Automation v3.0

Асинхронная автоматизация поиска и откликов на вакансии HH.ru через Python + n8n + Google Gemini AI + **Web-Use + GPT-4o**.

### Ссылка на видео с гайдом: https://www.youtube.com/watch?v=EakL7eoSL9U

## Особенности v3.0

- ⚡ **Async FastAPI**
- 🎭 **Async Playwright**
- 📖 **Swagger UI** — автодокументация API на `/docs`
- 🤖 **Web-Use + GPT-4o** — полностью автономное заполнение форм
- ❓ **Обработка вопросов работодателя** — автоматические ответы на дополнительные вопросы
- ✅ **Автоматическое подтверждение данных** — чекбоксы достоверности по ТК РФ
- 📝 **Умные профили** — JSON-профиль кандидата с валидацией
- 🖼️ **Скриншоты и логи** — детальное логирование каждого шага
- 🔍 **Интеллектуальный анализ форм** — определение типа полей и их заполнение

## Установка

### 1. Создание виртуального окружения

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# или
.venv\Scripts\activate     # Windows
```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Настройка окружения

Создайте файл `.env` в корне проекта на основе `.env.example`:

```bash
cp .env.example .env
```

```env
# Путь к директории для хранения сессии
N8N_FILES_DIR=/Users/your_username/.n8n-files

# Настройки сервера
SERVER_HOST=127.0.0.1
SERVER_PORT=8000

# Настройки поиска
DEFAULT_SEARCH_TEXT=Frontend
AREA_CODE=113

# Настройки браузера (опционально)
BROWSER_HEADLESS=true
BROWSER_SLOW_MO=0
PAGE_TIMEOUT=30000

# Настройки Web-Use + GPT-4o
USE_WEB_USE=false                    # Включить Web-Use режим
OPENAI_API_KEY=your_openai_api_key   # Ключ от OpenAI
WEBUSE_TIMEOUT=90                     # Таймаут в секундах
WEBUSE_MAX_STEPS=15                   # Максимальное шагов
```

**Важно:** 
- Замените `/Users/your_username/.n8n-files` на реальный путь
- Для Web-Use режима нужен `OPENAI_API_KEY` от OpenAI

### 4. Профиль кандидата

Создайте профиль кандидата в `data/candidate_profile.json`:

```json
{
  "full_name": "Иван Иванов",
  "email": "ivan@example.com",
  "phone": "+7 900 123-45-67",
  "city": "Москва",
  "birth_date": "1990-01-01",
  "experience_years": 5,
  "position": "Python разработчик",
  "skills": ["Python", "Django", "FastAPI", "PostgreSQL", "Docker"],
  "work_format": ["удалёнка", "гибрид"],
  "salary_expectations": "200 000 – 300 000 ₽",
  "cover_letter_template": "Привет! У меня {experience_years} лет опыта в {position}. Стек: {skills}.",
  "answers": {
    "готов к командировкам": "Да",
    "возраст": "34",
    "гражданство рф": "Да",
    "образование": "Высшее техническое",
    "уровень английского": "B1"
  }
}
```

**Новые возможности профиля:**
- **skills** — массив навыков для автоматического определения опыта (Python, Java и т.д.)
- **answers** — готовые ответы на типичные вопросы работодателя
- **salary_expectations** — зарплатные ожидания для автоматических ответов

## Режимы работы

### Стандартный режим (v2.0)
- Базовое заполнение форм
- Поддержка сопроводительных писем
- Надежность и проверка временем

### Web-Use режим (v3.0)
- **Полностью автономное заполнение** любых полей формы
- **GPT-4o анализирует** страницу и выбирает правильные ответы
- **Умные ответы** на вопросы работодателя из профиля
- **Автоматическое определение** типа полей (text, select, radio, checkbox)
- **Обработка дополнительных вопросов** работодателя (опыт, зарплата, Java и т.д.)
- **Автоматическое подтверждение** достоверности данных (ТК РФ)
- **Пропуск капчи** без зависания
- **Детальное логирование** с скриншотами каждого шага

**Переключение режимов:**
```env
USE_WEB_USE=false  # Стандартный режим
USE_WEB_USE=true   # Web-Use режим
```

## Запуск

### Вариант 1: Запуск через Docker (рекомендуется)

Docker позволяет запустить приложение без установки Python и зависимостей на вашу систему.

#### Требования

- Docker Desktop (для Windows/Mac) или Docker Engine (для Linux)
- docker-compose (обычно идет в комплекте с Docker Desktop)

#### 1. Сборка и запуск контейнеров

В корневой директории проекта выполните:

```bash
docker-compose up -d
```

Эта команда:
- Соберет Docker образ приложения
- Запустит два контейнера: `hh-automation` (API сервер) и `n8n` (workflow система)
- Сервер HH Automation будет доступен на `http://localhost:8000`
- n8n будет доступен на `http://localhost:5678`

#### 2. Авторизация на HH.ru

После запуска контейнеров необходимо авторизоваться на HH.ru **один раз**:

```bash
docker exec -it hh-automation python -m hh_automation.cli.login
```

Откроется браузер. Войдите в аккаунт HH.ru, затем нажмите Enter в терминале. Сессия сохранится в директории `./data/hh_session.json`.

#### 3. Настройка n8n

1. Откройте `http://localhost:5678` в браузере
2. Создайте учетную запись n8n (при первом запуске)
3. Импортируйте workflow из файла `HH.ru Flow (With AI and Pagination).json`
4. Добавьте Google Gemini API credentials в n8n (Settings → Credentials)
5. Запустите workflow

P.S. Если не запускается workflow с сервером, поменяйте в узлах адрес сервера на `http://hh-automation:8000`
#### 4. Управление контейнерами

**Просмотр логов:**
```bash
# Все сервисы
docker-compose logs -f

# Только HH Automation
docker-compose logs -f hh-automation

# Только n8n
docker-compose logs -f n8n
```

**Остановка:**
```bash
docker-compose down
```

**Перезапуск после изменения кода:**
```bash
docker-compose restart hh-automation
```

**Полная пересборка образа:**
```bash
docker-compose up -d --build
```

#### 5. Проверка работоспособности

```bash
# Проверка API
curl http://localhost:8000/health

# Swagger документация
# Откройте в браузере: http://localhost:8000/docs
```

### Вариант 2: Запуск локально (без Docker)

#### 1. Авторизация на HH.ru

Перед первым использованием сохраните сессию:

```bash
python -m hh_automation.cli.login
```

Откроется браузер. Войдите в аккаунт HH.ru, затем нажмите Enter в терминале.

#### 2. Запуск сервера

```bash
python -m hh_automation.server
```

Сервер запустится на `http://127.0.0.1:8000`.

#### 3. Настройка n8n

1. Импортируйте workflow из файла `HH.ru Flow (With AI and Pagination).json`
2. Добавьте Google Gemini API credentials в n8n (Settings → Credentials)
3. Запустите workflow

## API Endpoints

### GET /search

Поиск вакансий.

**Параметры:**
- `text` — поисковый запрос (по умолчанию: "Frontend")
- `page` — номер страницы, начиная с 0 (по умолчанию: 0)

**Пример:**
```bash
curl "http://127.0.0.1:8000/search?text=Python&page=0"
```

### POST /apply

Отклик на вакансию.

**Body:**
```json
{
  "url": "https://hh.ru/vacancy/123456",
  "message": "Текст сопроводительного письма"
}
```

**Пример:**
```bash
curl -X POST http://127.0.0.1:8000/apply \
  -H "Content-Type: application/json" \
  -d '{"url": "https://hh.ru/vacancy/123456", "message": "Здравствуйте..."}'
```

### GET /health

Проверка состояния сервера.

**Пример:**
```bash
curl http://127.0.0.1:8000/health
```

### GET /docs

Swagger UI с интерактивной документацией API.

## Структура проекта

```
.
├── job_automation/
│   ├── __init__.py
│   ├── config.py                    # Централизованная конфигурация
│   ├── server.py                    # FastAPI сервер
│   ├── models/
│   │   ├── __init__.py
│   │   └── candidate.py             # Pydantic модель профиля
│   ├── services/
│   │   ├── __init__.py
│   │   ├── browser.py              # Async Playwright менеджер
│   │   ├── search.py               # Сервис поиска вакансий
│   │   ├── apply.py                # Стандартный сервис откликов
│   │   ├── apply_interface.py      # Интерфейс для сервисов
│   │   └── webuse_apply.py        # Web-Use + GPT-4o сервис
│   └── cli/
│       ├── __init__.py
│       └── login.py                # CLI для авторизации
├── data/
│   └── candidate_profile.json      # Профиль кандидата
├── logs/                           # Логи и скриншоты
├── requirements.txt
├── .env.example                    # Пример конфигурации
├── .env                            # Конфигурация (создать вручную)
└── HH.ru Flow (With AI and Pagination).json  # n8n workflow
```

## Миграция с v2.0

Для перехода на v3.0:

1. **Обновите зависимости:**
```bash
pip install -r requirements.txt
```

2. **Создайте профиль кандидата:**
```bash
cp data/candidate_profile.json.example data/candidate_profile.json
# Отредактируйте под свои данные
```

3. **Обновите .env:**
```bash
cp .env.example .env
# Добавьте OPENAI_API_KEY для Web-Use режима
```

4. **Проверьте работу:**
```bash
# Стандартный режим
curl http://127.0.0.1:8000/health

# Web-Use режим (требуется OPENAI_API_KEY)
# Установите USE_WEB_USE=true в .env
```

**Изменения API:**
- Эндпоинты остались теми же (`/search`, `/apply`)
- Добавлен новый статус `skipped` для капчи в Web-Use режиме
- `/health` теперь показывает `web_use_enabled` статус

## Troubleshooting v3.0

### Session file not found

```bash
python -m job_automation.cli.login
```

### Playwright browser not found

```bash
playwright install chromium
```

### candidate_profile.json not found

```bash
cp data/candidate_profile.json.example data/candidate_profile.json
# Или создайте вручную по примеру из README
```

### OpenAI API key error

```bash
# Добавьте в .env
OPENAI_API_KEY=sk-...
```

### Web-Use не работает

1. Проверьте `USE_WEB_USE=true` в `.env`
2. Убедитесь что `OPENAI_API_KEY` валидный
3. Проверьте логи в `logs/webuse_*.log`
4. Убедитесь что профиль кандидата заполнен корректно
5. Проверьте скриншоты в `logs/webuse_*.png` для анализа проблем

### Вопросы работодателя не заполняются

1. Проверьте поле `skills` в профиле — оно используется для определения опыта
2. Проверьте поле `salary_expectations` — оно используется для ответа на вопросы о зарплате
3. Добавьте готовые ответы в поле `answers` профиля
4. Проверьте логи на наличие сообщений о найденных вопросах и ответах

### ModuleNotFoundError

Убедитесь, что виртуальное окружение активировано:
```bash
source .venv/bin/activate
```

### Ошибка импорта pydantic-settings

```bash
pip install pydantic-settings
```

### Ошибка импорта web-use

```bash
pip install -e git+https://github.com/CursorTouch/Web-Use.git@main
```

## Ограничения

### Стандартный режим
- Нет обработки rate limiting от HH.ru
- Требуется периодическое обновление сессии
- Captcha не обрабатывается автоматически

### Web-Use режим
- **Капча пропускается** (статус `skipped`)
- **Максимальное время отклика** - 90 секунд
- **Автоматическая обработка вопросов** работодателя
- **Автоматическое подтверждение данных** по ТК РФ
- **Rate limiting** - максимум 3 вакансии подряд, затем пауза 30 сек
- **Стоимость** - GPT-4o стоит ~$0.05 за отклик

## API Ключи

### Google Gemini API (для n8n workflow)
Получите API ключ: [Google AI Studio](https://makersuite.google.com/app/apikey)

Бесплатный tier: 60 запросов/минуту (достаточно для автоматизации).

### OpenAI API (для Web-Use режима)
Получите API ключ: [OpenAI Platform](https://platform.openai.com/api-keys)

Стоимость GPT-4o: ~$0.005/1K токенов (~$0.05 за отклик).

## Рекомендации

### Для всех режимов
1. Не превышайте 3-5 страниц за один запуск (60-100 вакансий)
2. Используйте задержки между откликами (минимум 5 секунд)
3. Обновляйте сессию раз в неделю через `python -m job_automation.cli.login`
4. Мониторьте статистику откликов в личном кабинете HH.ru

### Для Web-Use режима
5. **Начните с тестового режима** - попробуйте на 1-2 вакансиях
6. **Заполните профиль кандидата** - добавьте навыки, зарплатные ожидания и готовые ответы
7. **Следите за расходами** - проверяйте баланс OpenAI
8. **Анализируйте логи** - `logs/webuse_*.log` покажет все шаги и найденные вопросы
9. **Проверьте скриншоты** - в случае ошибок смотрите `logs/webuse_*.png`

## Производительность

### Стандартный режим
- **Скорость:** ~5-10 секунд на отклик
- **Надежность:** 95%+
- **Затраты:** только сервер

### Web-Use режим  
- **Скорость:** ~30-90 секунд на отклик
- **Надежность:** 85%+ (зависит от сложности форм)
- **Затраты:** ~$0.05 за отклик

---

# HH.ru Automation v3.0

**Полностью автономный поиск и отклик на вакансии HH.ru**
