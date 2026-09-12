class DomainError(Exception):
    pass


class NotFoundError(DomainError):
    pass


class InvalidTransitionError(DomainError):
    pass


class IdempotencyConflictError(DomainError):
    pass


class PolicyError(DomainError):
    pass


class LeaseError(DomainError):
    pass
