"""
SQLModel persistence model for webhook / callback delivery audit log.

Each row represents one delivery attempt for an evaluation completion callback.
Multiple attempts may exist per ``evaluation_run_id`` (retries).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Column, Field, SQLModel, String, Text


class WebhookDeliveryRow(SQLModel, table=True):
    """Audit row for each outbound webhook delivery attempt."""

    __tablename__ = "webhook_deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Unique per logical delivery (UUID); retries share the same delivery_id
    delivery_id: str = Field(
        sa_column=Column(String(64), nullable=False, index=True)
    )

    evaluation_run_id: int = Field(index=True)
    startup_id: str = Field(default="")

    # The URL we attempted to POST to
    callback_url: str = Field(sa_column=Column(Text, nullable=False))

    # Which attempt number (1-based)
    attempt: int = Field(default=1)

    # HTTP status code returned (0 if network error)
    response_status: int = Field(default=0)

    # "pending" | "success" | "failed" | "error"
    outcome: str = Field(default="pending")

    # Truncated response body or error message
    response_body: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # Serialised webhook payload that was sent
    payload_json: str = Field(sa_column=Column(Text, nullable=False))

    created_at: datetime = Field(default_factory=datetime.utcnow)
