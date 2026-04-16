from abc import ABC, abstractmethod
from typing import Optional
from ...config import settings
from ...models.events import NormalizedEvent


class SignatureError(Exception):
    """Raised when HMAC signature validation fails."""
    pass


class BaseAdapter(ABC):
    """Base adapter for all e-commerce platform webhooks."""

    platform_name: str = "unknown"

    @abstractmethod
    def validate_signature(
        self,
        payload: bytes,
        headers: dict,
        secret: str,
    ) -> bool:
        """Validate the webhook HMAC signature from the platform."""
        pass

    @abstractmethod
    def normalize(
        self,
        payload: dict,
        client_id: str,
        headers: Optional[dict] = None,
    ) -> NormalizedEvent:
        """Normalize a platform-specific payload into a NormalizedEvent."""
        pass

    def process(
        self,
        payload: bytes,
        payload_dict: dict,
        headers: dict,
        client_id: str,
        secret: str,
    ) -> NormalizedEvent:
        """Full pipeline: validate signature, then normalize payload."""
        if not settings.DEBUG and not self.validate_signature(payload, headers, secret):
            raise SignatureError(
                f"Invalid HMAC signature for platform '{self.platform_name}'"
            )
        return self.normalize(payload_dict, client_id, headers)
