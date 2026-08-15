"""
routers/benchmarking.py — /benchmarking/*

Ports: atp-dev/src/manager/app/Controller/BenchmarkingController.php

Routes:
  GET  /benchmarking         → benchmarking page
  POST /benchmarking/start   → AJAX: run benchmark → plain-text results
  GET  /benchmarking/status  → AJAX: current status → JSON {running, results}
"""

import asyncio
import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

import atp_client
from session import require_session
from logging_config import logger

router = APIRouter(prefix="/benchmarking")

PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "public"

# Simple in-process state — single-server assumption (one benchmark at a time)
_benchmark_running = False
_benchmark_results: str = ""


@router.get("")
async def benchmarking_get():
    return FileResponse(PUBLIC_DIR / "index.html")


@router.post("/start", response_class=PlainTextResponse)
async def benchmarking_start(
    session: dict = Depends(require_session),
):
    global _benchmark_running, _benchmark_results

    if _benchmark_running:
        return PlainTextResponse("Benchmark already running. Please wait.", status_code=409)

    _benchmark_running = True
    _benchmark_results = ""
    logger.info("Benchmark started by %r", session.get("username"))

    output_lines = []
    try:
        # Trigger the benchmark run
        await atp_client.benchmark_run()

        # Poll for results — up to 20 attempts, 1 s apart (mirrors PHP behaviour)
        for _ in range(20):
            await asyncio.sleep(1)
            try:
                result = await atp_client.benchmark_results()
                if result:
                    output_lines.append(result if isinstance(result, str) else str(result))
                    break
            except atp_client.AtpBackendError:
                pass

        _benchmark_results = "\n".join(output_lines) if output_lines else "No results returned."
        logger.info("Benchmark complete for %r", session.get("username"))

    except atp_client.AtpBackendError as exc:
        _benchmark_results = f"Benchmark failed: {exc}"
        logger.error("Benchmark error: %s", exc)
    finally:
        _benchmark_running = False

    return PlainTextResponse(_benchmark_results)


@router.get("/status", response_class=JSONResponse)
async def benchmarking_status(
    session: dict = Depends(require_session),
):
    return {"running": _benchmark_running, "results": _benchmark_results}
