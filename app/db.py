"""PostgreSQL connection pool (psycopg 3, async). Rows are returned as dicts."""
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

pool = AsyncConnectionPool(
    settings.database_url,
    open=False,
    min_size=1,
    max_size=10,
    kwargs={"row_factory": dict_row},
)


async def open_pool() -> None:
    await pool.open()
    await pool.wait()


async def close_pool() -> None:
    await pool.close()
