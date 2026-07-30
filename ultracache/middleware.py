from asgiref.sync import iscoroutinefunction, markcoroutinefunction

from ultracache import clear_recorder


class UltraCacheMiddleware:
    """Clear the recorder state for the current context after each request.

    Installing this middleware is recommended. It is not strictly required:
    a ``request_finished`` receiver in ``ultracache.signals`` performs the
    same cleanup as a safety net for deployments that omit it.

    The middleware is both sync- and async-capable, so under ASGI Django
    does not have to thread-shift requests just to pass through it. It does
    no I/O; the async path only differs in awaiting ``get_response``.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response=None):
        self.get_response = get_response
        self.async_mode = iscoroutinefunction(get_response)
        if self.async_mode:
            # Mark the instance as a coroutine function so Django's
            # middleware chain treats __call__ as async.
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.async_mode:
            return self.__acall__(request)
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_exception(request, e)
            raise
        return self.process_response(request, response)

    async def __acall__(self, request):
        try:
            response = await self.get_response(request)
        except Exception as e:
            self.process_exception(request, e)
            raise
        return self.process_response(request, response)

    def process_response(self, request, response):
        clear_recorder()
        return response

    def process_exception(self, request, exception):
        clear_recorder()
