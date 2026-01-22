#!/usr/bin/env python3
"""Тест отклика с видимым браузером и заполнением формы"""
import asyncio
import json
from playwright.async_api import async_playwright

async def test_apply():
    # Загрузка профиля
    with open('data/candidate_profile.json', 'r') as f:
        profile = json.load(f)
    
    # Загрузка сессии
    with open('/Users/user/.n8n-files/hh_session.json', 'r') as f:
        session = json.load(f)
    
    async with async_playwright() as p:
        # Запускаем ВИДИМЫЙ браузер
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        
        # Добавляем cookies сессии
        for cookie in session['cookies']:
            await context.add_cookies([cookie])
        
        page = await context.new_page()
        
        print('🚀 Переходим на вакансию...')
        await page.goto('https://hh.ru/vacancy/129713934')
        await page.wait_for_load_state('networkidle')
        
        print('👀 Смотрите на браузер! Жду 5 секунд...')
        await asyncio.sleep(5)
        
        # Ищем кнопку отклика
        try:
            await page.click('button[data-qa="vacancy-response-submit"]')
            print('✅ Кликнули на "Откликнуться"')
            await asyncio.sleep(3)
            
            # Заполняем поля
            textareas = await page.query_selector_all('textarea')
            for textarea in textareas:
                await textarea.fill(f"Привет! У меня {profile['experience_years']} лет опыта в {profile['position']}.")
                print('✅ Заполнили сопроводительное письмо')
                break
            
            # Ждем 10 секунд для наблюдения
            print('👀 Наблюдайте за формой 10 секунд...')
            await asyncio.sleep(10)
            
            # Отправляем
            await page.click('button[data-qa="vacancy-response-submit-popup"]')
            print('✅ Отправили отклик!')
            
        except Exception as e:
            print(f'❌ Ошибка: {e}')
        
        # Ждем 5 секунд перед закрытием
        print('👀 Браузер закроется через 5 секунд...')
        await asyncio.sleep(5)
        
        await browser.close()
        print('✅ Тест завершен!')

if __name__ == "__main__":
    asyncio.run(test_apply())
