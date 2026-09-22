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


class ImageConflictError(DomainError):
    """The image id or digest is already registered with other values."""


class SecretConflictError(DomainError):
    """A secret with this name already exists."""


class MergeError(DomainError):
    """The merge selection does not fit the collected diff."""


class ProfileConflictError(DomainError):
    """A profile with this name already exists with another spec."""


class ProfileBusyError(DomainError):
    """The profile cannot change while a Run copied from it is active."""


class ConsoleFullError(DomainError):
    """The run's console log reached its size limit."""


class SizeError(DomainError):
    """A console size outside what a terminal can be."""
