import logging
import os
import sys

from loguru import logger

from app.core.runtime.paths import log_dir

os.makedirs(log_dir, exist_ok=True)
use_multiprocessing_queue = not (getattr(sys, "frozen", False) and sys.platform == "darwin")
logger.add(
    os.path.join(log_dir, "streamget.log"),
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    filter=lambda i: i["level"].name != "STREAM",
    serialize=False,
    diagnose=False,
    enqueue=use_multiprocessing_queue,
    retention=3,
    rotation="3 MB",
    encoding="utf-8",
)

startup_logger = logger.bind(startup=True)
logger.add(
    os.path.join(log_dir, "startup.log"),
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    filter=lambda record: record["extra"].get("startup", False),
    diagnose=False,
    enqueue=False,
    retention=3,
    rotation="3 MB",
    encoding="utf-8",
)


class FletLogHandler(logging.Handler):
    def filter(self, record):
        # Keep connection diagnostics without logging control patches or form values.
        return record.levelno >= logging.INFO or record.module in {"app", "flet_socket_server"}

    def emit(self, record):
        startup_logger.opt(exception=record.exc_info).log(record.levelname, "[{}] {}", record.name, record.getMessage())


flet_log_handler = FletLogHandler()
for log_name, log_level in (
    ("flet", logging.DEBUG),
    ("flet_desktop", logging.INFO),
    ("asyncio", logging.WARNING),
    ("concurrent.futures", logging.WARNING),
):
    framework_logger = logging.getLogger(log_name)
    framework_logger.setLevel(log_level)
    framework_logger.addHandler(flet_log_handler)
    framework_logger.propagate = False

logger.level("STREAM", no=22, color="<blue>")
logger.add(
    os.path.join(log_dir, "play_url.log"),
    level="STREAM",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
    filter=lambda i: i["level"].name == "STREAM",
    serialize=False,
    enqueue=use_multiprocessing_queue,
    retention=1,
    rotation="500 KB",
    encoding="utf-8",
)
