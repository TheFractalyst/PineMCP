import sys, asyncio, re, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_collection as _get_collection, build_name_index as _build_name_index
from core.embeddings import get_model as _get_model, _embedding_model_ready
from core.hot_cache import build_hot_cache
from tools.lookup import get_function
from tools.search import search_docs

_get_collection(); _get_model(); _embedding_model_ready.set(); _build_name_index()
loop = asyncio.new_event_loop()
loop.run_until_complete(build_hot_cache())

print("=== get_function('strategy.closedtrades.profit') ===")
r1 = loop.run_until_complete(get_function(name='strategy.closedtrades.profit'))
print(r1[:800])

print()
print("=== search_docs('strategy.closedtrades.profit', category_filter='function') ===")
r2 = loop.run_until_complete(search_docs(query='strategy.closedtrades.profit', category_filter='function'))
names = re.findall(r'\[\d+\] ([^\n]+)\n', r2)
print('\n'.join(names[:6]))

loop.close()
