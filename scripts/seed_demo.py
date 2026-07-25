"""Idempotent demo data seed (§08 §8.5) - safe to re-run.

Usage: python -m scripts.seed_demo
(equivalent to POST /internal/seed, which is the usual way to run this
against a deployed environment where there's no shell access.)
"""

import asyncio

from app.db.session import async_session
from app.services.seed import seed_demo_data


async def main() -> None:
    async with async_session() as session:
        result = await seed_demo_data(session)
        await session.commit()
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
