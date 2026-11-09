"""The real-time layer is a small interface, so the hosting can change later.

Today:    PollingBackend  -> the browser asks the server every few seconds
          (works on PythonAnywhere Free, which has no WebSocket support).
Later:    a Socket.IO backend can implement the same methods and push events instead.
          Nothing else in the app has to change: services only talk to this interface.
"""


class RealtimeBackend:
    name = "base"

    def publish(self, event: str, conversation_id: int, payload: dict) -> None:
        """Tell connected clients something happened. Polling needs no push, so it is a no-op."""

    def set_typing(self, conversation_id: int, user_id: int, typing: bool) -> None:
        raise NotImplementedError

    def is_typing(self, conversation_id: int, user_id: int) -> bool:
        raise NotImplementedError
