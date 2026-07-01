"""Entrypoint serverless da Vercel para o backend FastAPI.

A Vercel faz uma checagem ESTÁTICA procurando uma variável `app` no nível
superior do módulo, por isso `app = _build_app()` fica no topo. Garantimos que a
raiz do repositório esteja no sys.path para o pacote `backend` ser importável.
"""
import os
import sys
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _build_app():
    try:
        from backend.app.main import app as real_app
        return real_app
    except Exception:  # pragma: no cover - surge só em falha de import no serverless
        tb = traceback.format_exc()
        from fastapi import FastAPI
        from fastapi.responses import PlainTextResponse

        fallback = FastAPI()

        @fallback.get("/{full_path:path}")
        def _import_error(full_path: str):
            return PlainTextResponse(tb, status_code=500)

        return fallback


app = _build_app()

__all__ = ["app"]
