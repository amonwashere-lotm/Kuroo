from kuroo.workflows.cache import load_cache
from kuroo.workflows.sync import load_storage


async def prepare_runtime():
    await load_storage()
    await load_cache()
