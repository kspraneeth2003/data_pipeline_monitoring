import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import checks, connectors, ingest, monitoring, projects, tickets
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="DPM API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(ingest.router)
app.include_router(ingest.github_router)
app.include_router(connectors.router)
app.include_router(checks.router)
app.include_router(tickets.router)
app.include_router(monitoring.router)


@app.get("/api/health")
def health():
    return {"ok": True}
