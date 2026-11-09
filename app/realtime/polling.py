import threading
import time

from .base import RealtimeBackend

TYPING_SECONDS = 6  # "typing..." disappears by itself if the person stops or disconnects


class PollingBackend(RealtimeBackend):
    """Typing is kept in memory (it is short-lived and fine to lose on a restart).

    With more than one server process this must move to a shared store such as Redis.
    PythonAnywhere Free runs one process, so memory works there.
    """

    name = "polling"

    def __init__(self):
        self._typing: dict[tuple[int, int], float] = {}
        self._lock = threading.Lock()

    def set_typing(self, conversation_id, user_id, typing):
        with self._lock:
            if typing:
                self._typing[(conversation_id, user_id)] = time.monotonic() + TYPING_SECONDS
            else:
                self._typing.pop((conversation_id, user_id), None)
            if len(self._typing) > 1000:  # tidy up old entries
                now = time.monotonic()
                self._typing = {k: v for k, v in self._typing.items() if v > now}

    def is_typing(self, conversation_id, user_id):
        with self._lock:
            return self._typing.get((conversation_id, user_id), 0) > time.monotonic()
