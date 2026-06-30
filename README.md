# 🤖 Catálogo Audit AI Agent — Fase 0

Agente de IA que audita anúncios de e-commerce para marketplaces brasileiros
(Mercado Livre, Shopee, Amazon, Magalu, Temu, Shein, TikTok Shop) usando o
Google Gemini, com **structured outputs**. Para cada anúncio o agente sugere:

- Título e descrição otimizados por marketplace
- Atributos técnicos faltantes (marca, modelo, voltagem, etc.)
- Problemas nas imagens, com severidade (HIGH / MEDIUM / LOW)
- Um **SEO score** (0–100) do anúncio original
- Métricas de tokens, custo e latência por execução

## Arquitetura

```
PROJ.IA_ML/
├── backend/                 # API REST (FastAPI)
│   ├── app/
│   │   ├── main.py          # Endpoints e serviço de auditoria
│   │   ├── agent.py         # Integração com o Google Gemini (+ modo mock)
│   │   ├── database.py      # Modelos SQLAlchemy (SQLite / PostgreSQL)
│   │   ├── schemas.py       # Modelos Pydantic (validação de entrada/saída)
│   │   ├── config.py        # Configuração central (env vars)
│   │   └── constants.py     # Enums: marketplaces, status, severidades
│   ├── test_agent.py        # Testes manuais do agente
│   ├── test_endpoints.py    # Teste end-to-end dos endpoints
│   └── requirements.txt
├── dashboard/               # Frontend (Streamlit)
│   ├── app.py
│   └── requirements.txt
└── test_listings.json       # Anúncios de exemplo para importar
```

## Setup

Pré-requisito: Python 3.11+.

```bash
# 1. Ambiente virtual
python -m venv .venv
.venv\Scripts\activate        # Windows (PowerShell/CMD)
# source .venv/bin/activate    # Linux/macOS

# 2. Dependências
pip install -r backend/requirements.txt
pip install -r dashboard/requirements.txt

# 3. Variáveis de ambiente
copy backend\.env.example backend\.env   # Windows
# cp backend/.env.example backend/.env    # Linux/macOS
```

Edite `backend/.env`:

- `GEMINI_API_KEY` — sua chave do [Google AI Studio](https://aistudio.google.com/).
  Use `mock` para rodar com respostas simuladas, sem chave real.
- `GEMINI_MODEL` — um modelo válido (ex.: `gemini-2.5-flash`).
- `DATABASE_URL` — vazio usa SQLite local; ou uma connection string PostgreSQL.
- `CORS_ORIGINS` — origens permitidas (default: Streamlit local).

## Como rodar

```bash
# Backend (na raiz do projeto)
uvicorn backend.app.main:app --reload --port 8000

# Dashboard (em outro terminal)
streamlit run dashboard/app.py
```

- API + docs interativas: http://localhost:8000/docs
- Dashboard: http://localhost:8501

## Endpoints principais

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET`  | `/health` | Health check |
| `GET`  | `/products` | Lista produtos |
| `POST` | `/products` | Cria produto |
| `POST` | `/products/import-test-listings` | Importa `test_listings.json` |
| `POST` | `/products/{id}/audit` | Audita um produto |
| `POST` | `/products/audit-all` | Audita todos os pendentes |
| `POST` | `/suggestions/{id}/approve` | Aprova e aplica sugestão |
| `POST` | `/suggestions/{id}/reject` | Rejeita sugestão |
| `GET`  | `/logs` | Logs de tokens/custo/latência |

## Testes

```bash
# Com o backend rodando em http://localhost:8000:
python backend/test_endpoints.py
python backend/test_agent.py
```

> Roadmap: migrar os testes manuais para `pytest` com `fastapi.testclient` e
> introduzir migrações de schema com Alembic.

## Notas

- O modo `mock` permite demonstrar o fluxo completo sem consumir a API.
- `token_cost_usd` fica em `0.0` por usar o Free Tier do Google AI Studio.
