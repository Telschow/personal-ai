"""Durable storage for personal knowledge."""

from personal_ai.storage.documents import DocumentStore, connect_database

__all__ = [
    "DocumentStore",
    "connect_database",
]
