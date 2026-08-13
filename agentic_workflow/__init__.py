"""Standalone Python replacement for the real-time n8n workflow."""

from .config import WorkflowConfig
from .runner import WorkflowRunner

__all__ = ["WorkflowConfig", "WorkflowRunner"]
