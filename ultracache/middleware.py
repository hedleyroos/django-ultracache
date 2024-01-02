from ultracache import _thread_locals


def _cleanup():
    if hasattr(_thread_locals, "ultracache_recorder"):
        delattr(_thread_locals, "ultracache_recorder")
    if hasattr(_thread_locals, "ultracache_attr_marker"):
        delattr(_thread_locals, "ultracache_attr_marker")


class UltraCacheMiddleware(object):
    """Middleware to ensure thread locals is cleaned up."""

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = self.process_request(request)
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_exception(request, e)
            raise
        return self.process_response(request, response)

    def process_request(self, request):
        setattr(_thread_locals, "ultracache_recorder", [])

    def process_response(self, request, response):
        _cleanup()
        return response

    def process_exception(self, request, exception):
        _cleanup()
