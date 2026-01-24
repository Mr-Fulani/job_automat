import asyncio
import json
import sys
import os
from typing import Optional

from ..config import get_settings
from ..services.browser import BrowserManager

async def login() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    manager = BrowserManager()
    
    is_headless = settings.browser_headless
    print(f"DEBUG: is_headless={is_headless}")
    
    async with manager.get_interactive_context(headless=is_headless) as (context, page):
        # Устанавливаем более реалистичный User-Agent
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

        # HH может долго грузиться и часто не доходит до networkidle из-за трекеров.
        # Для интерактивного логина используем более мягкие ожидания и увеличенные таймауты.
        page.set_default_navigation_timeout(120_000)

        print("\n" + "=" * 60)
        print("HH.ru Login")
        print("=" * 60)

        try:
            print("Opening HH.ru login page...")
            try:
                await page.goto("https://hh.ru/login", wait_until="domcontentloaded", timeout=120_000)
            except Exception as e:
                # Не валим логин из-за таймаута навигации: браузер мог открыть страницу частично.
                print(f"Warning: could not fully load login page: {e}")
                try:
                    await page.goto("https://hh.ru/login", wait_until="load", timeout=120_000)
                except Exception as e2:
                    print(f"Warning: second navigation attempt failed: {e2}")
            
            if not is_headless:
                print("\n1. Log in to HH.ru in the opened browser window")
                print("2. Wait until you see your personal profile")
                print("3. Come back here and press Enter")
                input("\nPress Enter after you have successfully logged in...")
            else:
                print("\n[DOCKER MODE] Starting automated login...")
                
                # Шаг 1: Убедимся что выбран тип аккаунта "Соискатель" (applicant)
                try:
                    print("Ensuring 'Applicant' account type is selected...")
                    applicant_radio = await page.wait_for_selector(
                        'input[data-qa="account-type-card-APPLICANT"]', 
                        timeout=5000
                    )
                    
                    is_checked = await applicant_radio.is_checked()
                    if not is_checked:
                        await applicant_radio.check()
                        print("✓ Selected 'Applicant' account type")
                    else:
                        print("✓ 'Applicant' account type already selected")
                    
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"Warning: Could not select applicant radio button: {e}")
                
                # Шаг 2: Кликаем на кнопку "Войти" чтобы перейти к форме логина
                try:
                    print("Clicking 'Login' button to proceed...")
                    submit_button = await page.wait_for_selector(
                        'button[data-qa="submit-button"]',
                        timeout=5000
                    )
                    await submit_button.click()
                    print("✓ Clicked 'Login' button")
                    
                    # Ждем загрузки страницы с формой логина
                    await asyncio.sleep(3)
                except Exception as e:
                    raise Exception(f"Could not click login button: {e}")
                
                # Шаг 3: Теперь ищем поле для ввода email/телефона
                user_input = input("\nEnter your HH.ru Email or Phone: ").strip()
                
                # DEBUG: Сохраняем HTML страницы для анализа
                html_content = await page.content()
                with open("data/login_form_page.html", "w", encoding="utf-8") as f:
                    f.write(html_content)
                print("DEBUG: Saved login form HTML to data/login_form_page.html")
                
                # Определяем, это телефон или email
                is_phone = user_input.startswith('+') or user_input.isdigit()
                
                if is_phone:
                    # Убираем +7 из начала номера, если есть
                    phone_number = user_input.replace('+', '').replace(' ', '').replace('-', '')
                    if phone_number.startswith('7'):
                        phone_number = phone_number[1:]  # Убираем первую цифру '7'
                    
                    print(f"Entering phone number (without country code): {phone_number}")
                    
                    # Ищем поле для ввода номера телефона (без кода страны)
                    phone_input_selector = 'input[data-qa="magritte-phone-input-national-number-input"]'
                    try:
                        phone_field = await page.wait_for_selector(phone_input_selector, timeout=5000)
                        await phone_field.fill(phone_number)
                        print("✓ Filled phone number field")
                    except Exception as e:
                        raise Exception(f"Could not find phone input field: {e}")
                        
                else:
                    # Это email, нужно переключиться на вкладку "Почта"
                    print("Switching to email tab...")
                    try:
                        email_tab = await page.wait_for_selector('input[data-qa="credential-type-EMAIL"]', timeout=5000)
                        await email_tab.click()
                        await asyncio.sleep(1)
                        print("✓ Switched to email tab")
                    except Exception as e:
                        print(f"Warning: Could not switch to email tab: {e}")
                    
                    # Ищем поле для ввода email
                    email_selectors = [
                        'input[type="email"]',
                        'input[name="login"]',
                        'input[data-qa*="email"]',
                    ]
                    
                    email_field = None
                    for selector in email_selectors:
                        try:
                            email_field = await page.wait_for_selector(selector, timeout=3000)
                            if email_field and await email_field.is_visible():
                                await email_field.fill(user_input)
                                print("✓ Filled email field")
                                break
                        except:
                            continue
                    
                    if not email_field:
                        raise Exception("Could not find email input field")
                
                # Кликаем на кнопку "Дальше" (Next)
                await page.click('button[data-qa="submit-button"]')
                print("✓ Clicked 'Next' button")
                
                # Ждем появления следующей страницы
                print("Waiting for OTP or next step...")
                await asyncio.sleep(3)
                
                # Сохраняем скриншот для отладки
                await page.screenshot(path="data/after_submit.png")
                print("DEBUG: Screenshot saved to data/after_submit.png")
                
                # Ждем поле для кода - пробуем разные селекторы
                print("Checking for OTP code request...")
                
                otp_selectors = [
                    'input[data-qa="otp-code-input"]',
                    'input[inputmode="numeric"]',
                    'input[type="text"][inputmode="numeric"]',
                    'input[name="code"]',
                    'input[placeholder*="код"]',
                ]
                
                otp_field = None
                for selector in otp_selectors:
                    try:
                        otp_field = await page.wait_for_selector(selector, timeout=5000)
                        if otp_field and await otp_field.is_visible():
                            print(f"✓ Found OTP field with selector: {selector}")
                            break
                    except:
                        continue
                
                if otp_field:
                    otp_code = input("\n🔐 Enter the OTP code sent to you: ").strip()
                    await otp_field.fill(otp_code)
                    print("✓ Entered OTP code")
                    
                    # Ищем кнопку подтверждения OTP
                    try:
                        # Ждем кнопку подтверждения
                        await asyncio.sleep(1)
                        confirm_button_selectors = [
                            'button[data-qa="submit-button"]',
                            'button[type="submit"]',
                        ]
                        
                        for btn_selector in confirm_button_selectors:
                            try:
                                confirm_btn = await page.wait_for_selector(btn_selector, timeout=3000)
                                if confirm_btn and await confirm_btn.is_visible():
                                    await confirm_btn.click()
                                    print("✓ Clicked OTP confirm button")
                                    break
                            except:
                                continue
                    except Exception as e:
                        print(f"Note: Could not find/click OTP confirm button: {e}")
                    
                    # После ввода кода HH обычно редиректит сам, подождем
                    print("Validating... please wait.")
                    await asyncio.sleep(5)
                else:
                    print("⚠️  OTP field not detected. Possible reasons:")
                    print("   - You might already be logged in")
                    print("   - The page might require password instead of OTP")
                    print("   - An error occurred")
                    print("   Check data/after_submit.png to see what happened")

                cookies = await context.cookies()
                if not any(c['name'] == 'hhtoken' for c in cookies):
                    print("⚠️  Warning: 'hhtoken' not found in cookies. Login might have failed.")
                else:
                    print("✓ Successfully authenticated!")

        except Exception as e:
            print(f"\n[ERROR] {e}")
            await page.screenshot(path="data/error_login.png")
            print("Screenshot saved to data/error_login.png. Check it to see what went wrong.")
            return

        # Сохраняем сессию
        await context.storage_state(path=str(settings.session_file))
        print(f"\n✓ Session saved to: {settings.session_file}")

def main() -> None:
    asyncio.run(login())

if __name__ == "__main__":
    main()
