"""Provider boundary for fake-live tests and a future explicitly authorized transport.

Importing this package never initializes a client, reads credentials, or opens a
network connection.  The existing scripted runner does not import this package.
"""

from .base import (
    FakeOutcome,
    Provider,
    ProviderRequest,
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    ProviderUsage,
    ResponseMode,
    ResponsesTransport,
    TextTurn,
    TransportFailure,
    TransportResponse,
    VERDICT_SCHEMA,
)
from .fake import FakeProvider
from .openai import OpenAIResponsesProvider, OpenAISDKTransport

__all__ = [
    "FakeOutcome",
    "FakeProvider",
    "OpenAIResponsesProvider",
    "OpenAISDKTransport",
    "Provider",
    "ProviderRequest",
    "ProviderResult",
    "ProviderStatus",
    "ProviderTiming",
    "ProviderUsage",
    "ResponseMode",
    "ResponsesTransport",
    "TextTurn",
    "TransportFailure",
    "TransportResponse",
    "VERDICT_SCHEMA",
]
