"""
db.py — async PostgreSQL connection pool for the recording server.

Database: ifs (host=localhost, port=5432, user=ifs)
Used by: routers/recordings.py, routers/audit.py

Not used for the main ATP backend — that runs over Unix socket (atp_client.py).
"""

import os
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "ifs"),
            user=os.environ.get("DB_USER", "ifs"),
            password=os.environ.get("DB_PASSWORD", "ifs"),
            min_size=2,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    """Close pool on app shutdown (call from lifespan)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
