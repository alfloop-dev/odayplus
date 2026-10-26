"""identity schema SQL 存取的連線生命週期輔助。

Task: ODP-WEB-LOCAL-AUTH-API-TRUST-001

``SqlIdentityStore`` 與 ``SqlSessionRepository`` 都透過 ``connection_factory``
取得連線。工廠可以是三種形式：

1. 回傳 context manager 的 callable（例如
   ``psycopg_pool.ConnectionPool.connection``）——連線由 pool 擁有，離開
   ``with`` 區塊時**必須**歸還，否則 pool 會耗盡。
2. 回傳裸連線的 callable——呼叫端擁有連線，這裡不關閉。
3. 直接是一個連線物件——同上。

``open_connection`` 把三種形式收斂成同一個 context manager，讓 repository 不必
各自判斷歸還責任。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def open_connection(factory: Any) -> Iterator[Any]:
    """取得一條連線；若工廠交出 context manager 則負責歸還。

    連線不可用時 yield ``None``，呼叫端據此 fail closed。
    """

    acquired = factory() if callable(factory) else factory
    if acquired is None:
        yield None
        return

    # Pool-owned connection: entering/exiting the context manager is what
    # returns it to the pool (the missing putconn half).
    if hasattr(acquired, "__enter__") and hasattr(acquired, "__exit__"):
        with acquired as connection:
            yield connection
        return

    # Caller-owned connection: never closed here.
    yield acquired
