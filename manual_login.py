#!/usr/bin/env python3
"""Ручная авторизация на HH.ru с сохранением сессии."""
import asyncio
import os
from playwright.async_api import async_playwright

async def manual_login():
    """Открывает браузер для ручной авторизации и сохраняет сессию."""
    
    # Создаем директорию для данных
    os.makedirs("data", exist_ok=True)
    
    async with async_playwright() as p:
        # Запускаем браузер в графическом режиме
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        print("=" * 60)
        print("РУЧНАЯ АВТОРИЗАЦИЯ HH.RU")
        print("=" * 60)
        print("\n1. В открывшемся окне браузера войдите в аккаунт HH.ru")
        print("2. Используйте почту или телефон + код из письма")
        print("3. Убедитесь, что вы на странице профиля соискателя")
        print("4. Вернитесь в эту консоль и нажмите Enter")
        print("\nОжидаю авторизации...")
        
        # Открываем главную страницу HH.ru
        await page.goto("https://hh.ru")
        
        # Ждем пока пользователь авторизуется
        print("\nНажмите Enter после завершения авторизации...")
        input()  # Простое ожидание Enter без таймаута
        
        # Проверяем, что пользователь авторизован
        try:
            # Проверяем наличие элемента профиля
            await page.wait_for_selector('[data-qa="main-menu-applicant-profile"]', timeout=5000)
            print("✅ Авторизация успешна!")
        except:
            print("⚠️  Не удалось подтвердить авторизацию, но сохраняю сессию...")
        
        # Сохраняем состояние сессии
        session_file = "data/hh_session.json"
        await context.storage_state(path=session_file)
        print(f"✅ Сессия сохранена в: {session_file}")
        
        # Закрываем браузер
        await browser.close()
        
        print("\n" + "=" * 60)
        print("ГОТОВО! Теперь можно использовать сессию в Docker")
        print("Скопируйте файл в Docker контейнер:")
        print("docker cp data/hh_session.json hh-automation:/app/data/")
        print("=" * 60)

if __name__ == "__main__":
    asyncio.run(manual_login())
