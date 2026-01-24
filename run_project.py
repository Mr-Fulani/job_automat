#!/usr/bin/env python3

import argparse
import asyncio
from datetime import datetime
from pathlib import Path


def _default_python_bin() -> str:
    venv_python = Path(".venv") / "bin" / "python"
    if venv_python.exists() and venv_python.is_file():
        return str(venv_python)
    return "python3"


async def _stream_subprocess(prefix: str, stream: asyncio.StreamReader) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        try:
            text = line.decode("utf-8", errors="replace").rstrip("\n")
        except Exception:
            text = str(line)
        print(f"{prefix}{text}")


async def _tail_file(prefix: str, file_path: Path, poll_seconds: float) -> None:
    file_obj = None
    last_inode = None

    while True:
        try:
            if file_obj is None:
                if not file_path.exists():
                    await asyncio.sleep(poll_seconds)
                    continue

                file_obj = file_path.open("r", encoding="utf-8", errors="replace")
                try:
                    st = file_path.stat()
                    last_inode = getattr(st, "st_ino", None)
                except Exception:
                    last_inode = None

                file_obj.seek(0, 2)

            line = file_obj.readline()
            if line:
                print(f"{prefix}{line.rstrip()}" )
                continue

            await asyncio.sleep(poll_seconds)

            try:
                st = file_path.stat()
                inode_now = getattr(st, "st_ino", None)
            except Exception:
                inode_now = None

            if last_inode is not None and inode_now is not None and inode_now != last_inode:
                try:
                    file_obj.close()
                except Exception:
                    pass
                file_obj = None
                last_inode = None

        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(poll_seconds)


async def main_async(server_cmd: list[str], tail_webuse: bool, poll_seconds: float) -> int:
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    webuse_log = logs_dir / f"webuse_{datetime.now().strftime('%Y-%m-%d')}.log"

    proc = await asyncio.create_subprocess_exec(
        *server_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    tasks: list[asyncio.Task] = []

    assert proc.stdout is not None
    tasks.append(asyncio.create_task(_stream_subprocess("[server] ", proc.stdout)))

    if tail_webuse:
        tasks.append(asyncio.create_task(_tail_file("[webuse] ", webuse_log, poll_seconds=poll_seconds)))

    try:
        return_code = await proc.wait()
        return return_code
    except KeyboardInterrupt:
        return 130
    finally:
        for t in tasks:
            t.cancel()

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass

        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tail-webuse", action="store_true")
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--python", dest="python_bin", type=str, default="")
    args = parser.parse_args()

    python_bin = str(args.python_bin or "").strip() or _default_python_bin()
    server_cmd = [python_bin, "-u", "-m", "job_automation.server"]

    if args.visible:
        print("[info] --visible: установи BROWSER_HEADLESS=false в .env (или экспортируй переменную окружения) для видимого браузера")

    code = asyncio.run(
        main_async(
            server_cmd=server_cmd,
            tail_webuse=not args.no_tail_webuse,
            poll_seconds=max(0.1, float(args.poll)),
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
