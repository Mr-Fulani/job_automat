#!/usr/bin/env python3

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from job_automation.config import get_settings
from job_automation.services.search import VacancySearchService
from job_automation.services.apply import VacancyApplyService
from job_automation.services.webuse_apply import WebUseApplyService
from job_automation.services.browser import browser_manager


def _exc_message(e: Exception) -> str:
    return f"{type(e).__name__}: {e}".strip()


def _safe_save_processed(
    store: "ProcessedVacanciesStore",
    vacancy_url: str,
    status: str,
    message: str,
) -> None:
    try:
        store.save(vacancy_url, status=status, message=message)
    except Exception:
        pass


def _cleanup_logs(logs_dir: Path, keep_days: int, keep_files: int) -> tuple[int, int]:
    if not logs_dir.exists() or not logs_dir.is_dir():
        return 0, 0

    keep_days = max(0, int(keep_days))
    keep_files = max(0, int(keep_files))

    cutoff_ts: float | None = None
    if keep_days > 0:
        cutoff_ts = (datetime.now().timestamp() - (keep_days * 24 * 60 * 60))

    candidates: list[Path] = []
    for p in logs_dir.iterdir():
        if not p.is_file():
            continue
        if p.name == ".gitkeep":
            continue
        if p.suffix.lower() not in {".png", ".log"}:
            continue
        candidates.append(p)

    deleted = 0
    total_bytes = 0

    if cutoff_ts is not None:
        for p in list(candidates):
            try:
                st = p.stat()
                if st.st_mtime < cutoff_ts:
                    total_bytes += int(st.st_size)
                    p.unlink(missing_ok=True)
                    deleted += 1
                    candidates.remove(p)
            except Exception:
                continue

    if keep_files > 0 and len(candidates) > keep_files:
        def _mtime(path: Path) -> float:
            try:
                return float(path.stat().st_mtime)
            except Exception:
                return 0.0

        candidates_sorted = sorted(candidates, key=_mtime, reverse=True)
        for p in candidates_sorted[keep_files:]:
            try:
                st = p.stat()
                total_bytes += int(st.st_size)
                p.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                continue

    return deleted, total_bytes


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

        if "vacancyId=" in url:
            try:
                after = url.split("vacancyId=", 1)[1]
                vacancy_id = after.split("&")[0].split("#")[0]
                if vacancy_id:
                    return vacancy_id
            except Exception:
                pass
        return url


async def run(
    user_id: str,
    query: str,
    max_pages: int,
    max_vacancies: int,
    max_success: int,
    delay_seconds: float,
    message: str,
    keep_open_seconds: float,
    url: str,
    force: bool,
    manage_browser: bool = True,
) -> None:
    user_id = str(user_id or "").strip()
    if user_id:
        os.environ["SESSION_FILE"] = str(Path("data") / "users" / user_id / "hh_session.json")
        os.environ["PROCESSED_VACANCIES_FILE"] = str(Path("data") / "users" / user_id / "processed_vacancies.json")

        user_profile_path = Path("data") / "users" / user_id / "candidate_profile.json"
        if user_profile_path.exists():
            os.environ["CANDIDATE_PROFILE_FILE"] = str(user_profile_path)
        else:
            os.environ.setdefault("CANDIDATE_PROFILE_FILE", str(Path("data") / "candidate_profile.json"))
        try:
            get_settings.cache_clear()
        except Exception:
            pass

    settings = get_settings()

    search_service = VacancySearchService()

    webuse_service: Optional[WebUseApplyService] = None
    standard_service: Optional[VacancyApplyService] = None

    if settings.use_web_use:
        webuse_service = WebUseApplyService()
    else:
        standard_service = VacancyApplyService()

    processed_store = ProcessedVacanciesStore(Path(settings.processed_vacancies_file))

    applied_count = 0
    seen = 0

    if manage_browser:
        await browser_manager.start()
    try:
        page = None
        if settings.use_web_use and webuse_service is not None:
            ctx = await browser_manager.get_context(use_session=True)
            page = await ctx.new_page()
            page.set_default_timeout(settings.page_timeout)

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
                    else:
                        if prev_status == "error":
                            print("[retry] previously error, retrying")
                        seen += 1
                        try:
                            if webuse_service is not None:
                                if page is not None:
                                    result = await webuse_service.apply_with_page(page, url, message)
                                else:
                                    result = await webuse_service.apply(url, message)
                            else:
                                result = await standard_service.apply(url, message)  # type: ignore[union-attr]

                            status = str((result or {}).get("status", "unknown"))
                            msg = str((result or {}).get("message", ""))
                            print(f"[result] {status} - {msg}")
                            _safe_save_processed(processed_store, url, status=status, message=msg)
                            if status == "success":
                                applied_count += 1
                        except Exception as e:
                            _safe_save_processed(processed_store, url, status="error", message=_exc_message(e))
            else:
                print("[force] bypass processed checks")
                seen += 1
                try:
                    if webuse_service is not None:
                        if page is not None:
                            result = await webuse_service.apply_with_page(page, url, message)
                        else:
                            result = await webuse_service.apply(url, message)
                    else:
                        result = await standard_service.apply(url, message)  # type: ignore[union-attr]

                    status = str((result or {}).get("status", "unknown"))
                    msg = str((result or {}).get("message", ""))
                    print(f"[result] {status} - {msg}")
                    _safe_save_processed(processed_store, url, status=status, message=msg)
                    if status == "success":
                        applied_count += 1
                except Exception as e:
                    _safe_save_processed(processed_store, url, status="error", message=_exc_message(e))

            print(f"[done] processed={seen} success={applied_count}")
            if keep_open_seconds > 0:
                print(f"[keep-open] sleeping {keep_open_seconds} seconds")
                await asyncio.sleep(keep_open_seconds)
            return

        for page_num in range(max_pages):
            print(f"[search] page={page_num} query='{query}'")
            try:
                results = await search_service.search(query=query, page_num=page_num, include_descriptions=False)
            except Exception as e:
                print(f"[search-error] {type(e).__name__}: {e}")
                break
            if not results:
                print("[search] no results")
                break

            print(f"[search] found={len(results)}")

            for vacancy in results:
                if max_vacancies > 0 and seen >= max_vacancies:
                    break
                if max_success > 0 and applied_count >= max_success:
                    break

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
                        if page is not None:
                            result = await webuse_service.apply_with_page(page, url, message)
                        else:
                            result = await webuse_service.apply(url, message)
                    else:
                        print(f"[apply] {url}")
                        result = await standard_service.apply(url, message)  # type: ignore[union-attr]

                    status = str((result or {}).get("status", "unknown"))
                    msg = str((result or {}).get("message", ""))

                    print(f"[result] {status} - {msg}")

                    _safe_save_processed(processed_store, url, status=status, message=msg)

                    if status == "success":
                        applied_count += 1

                except Exception as e:
                    _safe_save_processed(processed_store, url, status="error", message=_exc_message(e))

                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

            if (max_vacancies > 0 and seen >= max_vacancies) or (max_success > 0 and applied_count >= max_success):
                break

        print(f"[done] processed={seen} success={applied_count}")
        if keep_open_seconds > 0:
            print(f"[keep-open] sleeping {keep_open_seconds} seconds")
            await asyncio.sleep(keep_open_seconds)
    finally:
        try:
            if page is not None:
                await page.close()
        except Exception:
            pass
        if manage_browser:
            await browser_manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", dest="user_id", default="")
    parser.add_argument("--query", default=None)
    parser.add_argument("--url", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--max", dest="max_vacancies", type=int, default=10)
    parser.add_argument("--max-success", dest="max_success", type=int, default=0)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--message", type=str, default="")
    parser.add_argument("--keep-open", dest="keep_open_seconds", type=float, default=0.0)
    parser.add_argument("--cleanup-logs", action="store_true")
    parser.add_argument("--logs-keep-days", type=int, default=7)
    parser.add_argument("--logs-keep-files", type=int, default=200)

    args = parser.parse_args()
    settings = get_settings()

    if args.cleanup_logs:
        deleted, total_bytes = _cleanup_logs(Path("logs"), keep_days=args.logs_keep_days, keep_files=args.logs_keep_files)
        if deleted:
            print(f"[logs-cleanup] deleted={deleted} freed_bytes={total_bytes}")

    query = args.query or settings.default_search_text

    asyncio.run(
        run(
            user_id=str(args.user_id or ""),
            query=query,
            max_pages=max(1, args.pages),
            max_vacancies=max(0, args.max_vacancies),
            max_success=max(0, args.max_success),
            delay_seconds=max(0.0, args.delay),
            message=args.message,
            keep_open_seconds=max(0.0, args.keep_open_seconds),
            url=str(args.url or ""),
            force=bool(args.force),
        )
    )


if __name__ == "__main__":
    main()
