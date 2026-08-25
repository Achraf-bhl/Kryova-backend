from typing import Any, Protocol

from httpx import Cookies, Headers

from app.media import LocalMediaStore, MediaService


class AuthenticatedTestClient(Protocol):
    media: MediaService
    store: LocalMediaStore
    cookies: Cookies
    headers: Headers

    def request(self, *args: Any, **kwargs: Any) -> Any: ...

    def get(self, *args: Any, **kwargs: Any) -> Any: ...

    def post(self, *args: Any, **kwargs: Any) -> Any: ...

    def put(self, *args: Any, **kwargs: Any) -> Any: ...

    def patch(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete(self, *args: Any, **kwargs: Any) -> Any: ...
