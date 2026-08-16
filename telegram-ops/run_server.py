import asyncio
import sys

import uvicorn


def configure_event_loop_policy() -> None:
    # PySocks sockets used by Telethon are not reliable with Windows Proactor.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ == "__main__":
    configure_event_loop_policy()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
