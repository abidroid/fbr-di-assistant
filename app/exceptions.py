"""Domain exceptions raised by services and pipeline modules.

Services never import FastAPI's ``HTTPException``; they raise one of these and
the single handler in `app.main` turns it into the standard error body
``{"error": {"code", "message", "request_id"}}``. Each class declares its own
HTTP status, so the mapping lives next to the error, not in the services.
"""

import uuid


class DomainError(Exception):
    """Base class for every expected, user-facing error.

    Attributes:
        status_code: HTTP status the API returns for this error.
        code: Stable machine-readable name the frontend can switch on.
        message: Human-readable explanation, safe to show to the user.
        headers: Extra response headers, e.g. ``WWW-Authenticate`` on 401.
    """

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, headers: dict[str, str] | None = None) -> None:
        """Create the error with an optional custom message and headers.

        Args:
            message: Overrides the class's default message when given.
            headers: Extra headers to send with the error response.
        """
        self.message = message or self.default_message
        self.headers = headers
        super().__init__(self.message)


class InvalidDocumentError(DomainError):
    """The file is empty, corrupt, encrypted or contains no extractable text."""

    status_code = 400
    code = "invalid_document"
    default_message = "The file is empty, corrupt or contains no readable text."


class GuardrailBlockedError(DomainError):
    """An input guardrail rejected the question (injection, off-topic, ...)."""

    status_code = 400
    code = "blocked_by_guardrail"
    default_message = "This question cannot be answered by this assistant."


class InvalidCredentialsError(DomainError):
    """Login failed, or the bearer token is missing, invalid or expired."""

    status_code = 401
    code = "invalid_credentials"
    default_message = "Could not validate credentials."

    def __init__(self, message: str | None = None) -> None:
        """Create the error with the ``WWW-Authenticate`` header OAuth2 requires.

        Args:
            message: Overrides the default message when given.
        """
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class DocumentNotFoundError(DomainError):
    """The document does not exist or belongs to another user.

    Both cases return 404 on purpose, so nobody can probe for other users' ids.
    """

    status_code = 404
    code = "document_not_found"
    default_message = "Document not found."


class EmailTakenError(DomainError):
    """Signup used an email address that already has an account."""

    status_code = 409
    code = "email_taken"
    default_message = "An account with this email already exists."


class DuplicateDocumentError(DomainError):
    """The user already uploaded a file with identical bytes (same sha256).

    Attributes:
        existing_id: Id of the document already stored, so the client can use it.
    """

    status_code = 409
    code = "duplicate_document"

    def __init__(self, existing_id: uuid.UUID) -> None:
        """Create the error pointing at the existing document.

        Args:
            existing_id: Id of the previously uploaded identical document.
        """
        self.existing_id = existing_id
        super().__init__(f"This file was already uploaded as document {existing_id}.")


class FileTooLargeError(DomainError):
    """The upload is bigger than ``settings.max_upload_mb``."""

    status_code = 413
    code = "file_too_large"
    default_message = "The file is larger than the upload limit."


class UnsupportedFileTypeError(DomainError):
    """The file is not PDF, DOCX, PPTX, TXT or MD (checked by content, not name)."""

    status_code = 415
    code = "unsupported_file_type"
    default_message = "Only PDF, DOCX, PPTX, TXT and MD files are supported."


class UpstreamUnavailableError(DomainError):
    """An external service (Anthropic, Qdrant, Neon) still failed after retries."""

    status_code = 503
    code = "upstream_unavailable"
    default_message = "A required service is temporarily unavailable. Please try again."
