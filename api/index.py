"""Entrypoint serverless da Vercel para o backend FastAPI.

A Vercel detecta a variável `app` (aplicação ASGI) e a serve automaticamente.
Garantimos que a raiz do repositório esteja no sys.path para que o pacote
`backend` (namespace package, sem __init__.py na raiz) seja importável.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.app.main import app  # noqa: E402

__all__ = ["app"]
