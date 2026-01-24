#!/usr/bin/env python3

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from job_automation.config import get_settings
from job_automation.services.search import VacancySearchService
from job_automation.services.apply import VacancyApplyService
from job_automation.services.webuse_apply import WebUseApplyService


class ProcessedVacanciesStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.vacancies: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.vacancies = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.vacancies = data.get("vacancies", {}) or {}
        except Exception:
            self.vacancies = {}

    def is_processed(self, vacancy_url: str) -> tuple[bool, dict[str, Any]]:
        vacancy_id = self._extract_vacancy_id(vacancy_url)
        if vacancy_id in self.vacancies:
            return True, self.vacancies[vacancy_id]
        return False, {}

    def save(self, vacancy_url: str, status: str, message: str, profile_used: str = "") -> None:
        vacancy_id = self._extract_vacancy_id(vacancy_url)
        self.vacancies[vacancy_id] = {
            "url": vacancy_url,
            "vacancy_id": vacancy_id,
            "status": status,
            "message": message,
            "processed_at": datetime.now().isoformat(),
            "profile_used": profile_used or "default",
        }

        payload = {
            "vacancies": self.vacancies,
            "metadata": {
                "version": "1.0",
                "description": "Отслеживание обработанных вакансий для избежания дублирования",
                "created": datetime.now().isoformat().split("T")[0],
                "total_processed": len(self.vacancies),
                "last_updated": datetime.now().isoformat(),
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _extract_vacancy_id(url: str) -> str:
        if "/vacancy/" in url:
            parts = url.split("/vacancy/")
            if len(parts) > 1:
                return parts[1].split("?")[0].split("/")[0]
        return url


async def run(
    query: str,
    max_pages: int,
    max_vacancies: int,
    delay_seconds: float,
    message: str,
    keep_open_seconds: float,
    url: str,
    force: bool,
) -> None:
    settings = get_settings()

    search_service = VacancySearchService()

    webuse_service: Optional[WebUseApplyService] = None
    standard_service: Optional[VacancyApplyService] = None

    if settings.use_web_use:
        webuse_service = WebUseApplyService()
    else:
        standard_service = VacancyApplyService()

    processed_store = ProcessedVacanciesStore(Path("data/processed_vacancies.json"))

    applied_count = 0
    seen = 0

    if url:
        url = url.strip()
        if force:
            # Не ломаем сигнатуры сервисов: используем query param для режима форсированного прогона
            if "force=1" not in url:
                url = url + ("&" if "?" in url else "?") + "force=1"
        print(f"[apply-direct] {url}")
        if not force:
            is_done, done_data = processed_store.is_processed(url)
            if is_done:
                prev_status = str(done_data.get("status", ""))
                if prev_status and prev_status != "error":
                    print(f"[skip] already processed ({prev_status})")
                    return
                if prev_status == "error":
                    print("[retry] previously error, retrying")
        else:
            print("[force] bypass processed checks")

        try:
            if webuse_service is not None:
                result = await webuse_service.apply(url, message)
            else:
                result = await standard_service.apply(url, message)  # type: ignore[union-attr]

            status = str(result.get("status", "unknown"))
            msg = str(result.get("message", ""))
            print(f"[result] {status} - {msg}")
            processed_store.save(url, status=status, message=msg)
            if status == "success":
                applied_count += 1
        except Exception as e:
            processed_store.save(url, status="error", message=str(e))

        print(f"[done] processed=1 success={applied_count}")
        if keep_open_seconds > 0:
            print(f"[keep-open] sleeping {keep_open_seconds} seconds")
            await asyncio.sleep(keep_open_seconds)
        return

    for page_num in range(max_pages):
        print(f"[search] page={page_num} query='{query}'")
        results = await search_service.search(query=query, page_num=page_num, include_descriptions=False)
        if not results:
            print("[search] no results")
            break

        print(f"[search] found={len(results)}")

        for vacancy in results:
            if max_vacancies > 0 and seen >= max_vacancies:
                return

            url = (vacancy.get("url") or "").strip()
            if not url:
                continue

            is_done, done_data = processed_store.is_processed(url)
            if is_done:
                prev_status = str(done_data.get("status", ""))
                # Даём шанс повторить вакансии с прошлой ошибкой после фиксов
                if prev_status and prev_status != "error":
                    print(f"[skip] already processed ({prev_status})")
                    continue
                if prev_status == "error":
                    print("[retry] previously error, retrying")

            # Считаем лимит только для вакансий, по которым реально делаем попытку
            seen += 1

            try:
                if webuse_service is not None:
                    print(f"[apply] {url}")
                    result = await webuse_service.apply(url, message)
                else:
                    print(f"[apply] {url}")
                    result = await standard_service.apply(url, message)  # type: ignore[union-attr]

                status = str(result.get("status", "unknown"))
                msg = str(result.get("message", ""))

                print(f"[result] {status} - {msg}")

                processed_store.save(url, status=status, message=msg)

                if status == "success":
                    applied_count += 1

            except Exception as e:
                processed_store.save(url, status="error", message=str(e))

            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    print(f"[done] processed={seen} success={applied_count}")
    if keep_open_seconds > 0:
        print(f"[keep-open] sleeping {keep_open_seconds} seconds")
        await asyncio.sleep(keep_open_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=None)
    parser.add_argument("--url", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--max", dest="max_vacancies", type=int, default=10)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--message", type=str, default="")
    parser.add_argument("--keep-open", dest="keep_open_seconds", type=float, default=0.0)

    args = parser.parse_args()
    settings = get_settings()

    query = args.query or settings.default_search_text

    asyncio.run(
        run(
            query=query,
            max_pages=max(1, args.pages),
            max_vacancies=max(0, args.max_vacancies),
            delay_seconds=max(0.0, args.delay),
            message=args.message,
            keep_open_seconds=max(0.0, args.keep_open_seconds),
            url=str(args.url or ""),
            force=bool(args.force),
        )
    )


if __name__ == "__main__":
    main()
