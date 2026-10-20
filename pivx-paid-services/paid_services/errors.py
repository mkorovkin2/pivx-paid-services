class ServiceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def require(condition, status, code, message):
    if not condition:
        raise ServiceError(status, code, message)
