from ultracache import clear_recorder


class UltraCacheMiddleware:
    """Clear the recorder state for the current context after each request.

    Installing this middleware is recommended. It is not strictly required:
    a ``request_finished`` receiver in ``ultracache.signals`` performs the
    same cleanup as a safety net for deployments that omit it.
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_exception(request, e)
            raise
        return self.process_response(request, response)

    def process_response(self, request, response):
        clear_recorder()
        return response

    def process_exception(self, request, exception):
        clear_recorder()
