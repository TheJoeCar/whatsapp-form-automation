"""OpenAI-compatible LLM client (works with OpenRouter and, later, Ollama)."""

from .client import LLMClient, extract_json, list_free_models

__all__ = ["LLMClient", "extract_json", "list_free_models"]