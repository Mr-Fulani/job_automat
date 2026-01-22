#!/usr/bin/env python3
"""Создание файла сессии из cookies."""
import json

# Вставьте сюда cookies из консоли браузера
cookies_string = """
hhtoken=your_token_here
hhuid=your_uid_here
other_cookie=value
"""

def create_session_from_cookies():
    cookies = []
    for line in cookies_string.strip().split('\n'):
        if '=' in line:
            name, value = line.split('=', 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".hh.ru",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None"
            })
    
    session_data = {
        "cookies": cookies,
        "origins": []
    }
    
    with open("data/hh_session.json", "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2, ensure_ascii=False)
    
    print("✅ Сессия создана в data/hh_session.json")

if __name__ == "__main__":
    create_session_from_cookies()
