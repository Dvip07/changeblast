import asyncio
import json
import sys

from changeblast import store
from changeblast.indexer import ensure_index


async def main(repo: str, force: bool) -> None:
    await store.pool.open()
    await store.setup()
    idx = await ensure_index(repo, force=force)
    print(json.dumps({k: idx[k] for k in ("repo", "commit", "stats", "quality")}, indent=2))
    await store.pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--force" in sys.argv))
