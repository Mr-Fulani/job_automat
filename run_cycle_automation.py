#!/usr/bin/env python3

import argparse
import asyncio
from datetime import datetime

from job_automation.services.browser import browser_manager
from run_local_automation import run


async def _cycle_loop(
    user_id: str,
    query: str,
    pages: int,
    max_vacancies: int,
    batch_success: int,
    pause_seconds: float,
    delay_seconds: float,
    message: str,
    force: bool,
    cycles: int,
    dry_run: bool,
) -> None:
    await browser_manager.start()
    try:
        cycle_idx = 0
        while True:
            cycle_idx += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[cycle] #{cycle_idx} started_at={ts}")

            await run(
                user_id=user_id,
                query=query,
                max_pages=max(1, pages),
                max_vacancies=max(0, max_vacancies),
                max_success=max(0, batch_success),
                delay_seconds=max(0.0, delay_seconds),
                message=message,
                keep_open_seconds=0.0,
                url="",
                force=force,
                dry_run=dry_run,
                manage_browser=False,
            )

            if cycles > 0 and cycle_idx >= cycles:
                print(f"[cycle] finished cycles={cycles}")
                return

            if pause_seconds > 0:
                print(f"[cycle] sleeping {pause_seconds} seconds")
                await asyncio.sleep(pause_seconds)

    finally:
        await browser_manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", dest="user_id", default="")
    parser.add_argument("--query", default="Python разработчик")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--max", dest="max_vacancies", type=int, default=20)
    parser.add_argument("--batch-success", dest="batch_success", type=int, default=3)
    parser.add_argument("--pause", dest="pause_seconds", type=float, default=120.0)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--message", type=str, default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--cycles", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    asyncio.run(
        _cycle_loop(
            user_id=str(args.user_id or ""),
            query=str(args.query or ""),
            pages=int(args.pages),
            max_vacancies=int(args.max_vacancies),
            batch_success=int(args.batch_success),
            pause_seconds=float(args.pause_seconds),
            delay_seconds=float(args.delay),
            message=str(args.message or ""),
            force=bool(args.force),
            cycles=int(args.cycles),
            dry_run=bool(args.dry_run),
        )
    )


if __name__ == "__main__":
    main()
