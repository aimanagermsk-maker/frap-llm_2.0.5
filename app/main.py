from contextlib import asynccontextmanager

import uvicorn

from fastapi import FastAPI

from app.config.app_config import get_app_config, log_app_config
from app.config.logging_config import setup_logging
from app.routers import hello_router
from app.services.kafka_worker import KafkaWorker

VERSION = "0.1.0"
HOST = "0.0.0.0"
PORT = 8000


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте и очистка при остановке."""
    config = get_app_config()
    setup_logging(config.logging)
    log_app_config()
    kafka_worker = None
    if config.kafka.enabled:
        kafka_worker = KafkaWorker(config.kafka)
        kafka_worker.start()
        app.state.kafka_worker = kafka_worker

    try:
        yield
    finally:
        if kafka_worker:
            kafka_worker.stop()


app = FastAPI(
    title="frap-llm-helper API",
    version=VERSION,
    docs_url="/docs",
    lifespan=lifespan,
)

app.include_router(hello_router.router)


if __name__ == "__main__":
    app_config = get_app_config()
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        log_config=setup_logging(app_config.logging),
    )
