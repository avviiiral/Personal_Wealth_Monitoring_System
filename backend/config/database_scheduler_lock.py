"""Shared in-process lock for database-heavy background schedulers.

SQLite permits concurrent readers but serializes writes. PWMS starts several
background schedulers from Django's AppConfig.ready(), so without one shared
lock their startup runs can overlap and produce SQLITE_BUSY / "database is
locked" errors. RLock is intentional because the daily refresh command can
invoke work that is also protected by this lock.
"""

import threading


DATABASE_SCHEDULER_LOCK = threading.RLock()
