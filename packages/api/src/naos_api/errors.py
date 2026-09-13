class DomainError(Exception):
    """Base for every error the domain raises instead of an HTTP status."""


class NotFoundError(DomainError):
    """The addressed entity does not exist."""


class InvalidTransitionError(DomainError):
    """The requested lifecycle move is not allowed from the current status."""


class IdempotencyConflictError(DomainError):
    """The idempotency key was reused with a different request body."""


class PolicyError(DomainError):
    """The submitted policy is invalid or does not resolve to a policy."""


class LeaseError(DomainError):
    """The runner lease is missing, expired, or belongs to another runner."""


class ImageError(DomainError):
    """The image cannot be imported, verified or referenced."""


class ImageConflictError(DomainError):
    """The image id or digest is already registered with other values."""
