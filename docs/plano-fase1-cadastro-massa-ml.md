# Plano de Implementação — Fase 1: Cadastro em Massa via Planilha (Mercado Livre)

> Escopo escolhido com o usuário a partir do documento de visão de produto.
> **Sem publicação automática**: o módulo importa, valida, gera preview e cria
> produtos no catálogo interno com `status=pending`. O envio ao Mercado Livre é
> uma fase posterior.

## 1. Objetivo e escopo

**Entra nesta fase:**
- Upload de planilha (CSV/XLSX) com vários produtos.
- Mapeamento das colunas da planilha → campos canônicos do sistema.
- Validação automática por linha (regras determinísticas do Mercado Livre),
  separando **erros** (bloqueiam) de **avisos** (não bloqueiam).
- Upload de imagens/vídeos vinculados a cada linha.
- Preview do resultado antes da importação.
- Confirmação que cria `Product` (status `pending`) **apenas das linhas válidas**.
- Template de planilha para download.

**NÃO entra (fases futuras):**
- Publicação via API do Mercado Livre.
- Integração com ERP (Bling).
- Análise qualitativa / competitiva.
- Liberação de autonomia sem aprovação humana.

## 2. Benchmark (resumo)

Padrões consolidados em Bling, Olist, Tray, Anymarket e Linx para cadastro em massa:

| Funcionalidade | Replicar? | Justificativa |
|---|---|---|
| Template de planilha por canal | **Sim** | Reduz erro de formato na origem; baixo custo. |
| Wizard de mapeamento de colunas ("de-para") | **Sim** | Cada vendedor exporta a planilha com nomes diferentes. |
| Validação linha a linha + relatório de erros | **Sim** | É o coração do "bloquear produto incompleto" pedido. |
| Área de rascunho/staging antes de publicar | **Sim** | Alinhado à restrição "nada de escrita sem aprovação". |
| De-para completo multi-ERP / multi-warehouse | **Não (ainda)** | Excesso para o volume atual; só campos canônicos → ML. |
| Fila assíncrona/worker dedicado (Celery) | **Não (ainda)** | Volume atual roda síncrono; revisitar se planilhas ficarem grandes. |

> Benchmark mais profundo e com fontes pode ser feito sob demanda (WebSearch).

## 3. Fluxo do usuário

```
1. Baixa template (opcional)         GET  /imports/template?marketplace=mercado_livre
2. Sobe a planilha                   POST /imports                (multipart)
3. Confere/ajusta o mapeamento       POST /imports/{id}/mapping
4. Sobe imagens/vídeos               POST /imports/{id}/media     (multipart)
5. Roda a validação                  POST /imports/{id}/validate
6. Revisa o preview (erros/avisos)   GET  /imports/{id}/rows
7. Confirma a importação             POST /imports/{id}/confirm   -> cria Products pending
8. (Fase 0 existente) audita/aprova cada produto
```

## 4. Modelo de dados (SQLAlchemy)

Novas tabelas em `backend/app/database.py`:

### `ImportBatch`
| Coluna | Tipo | Notas |
|---|---|---|
| id | Integer PK | |
| filename | String | nome do arquivo enviado |
| marketplace | String, index | `mercado_livre` nesta fase |
| status | String, index | `uploaded`, `mapped`, `validated`, `imported`, `discarded` |
| column_mapping | JSON | `{ "coluna_planilha": "campo_canonico" }` |
| total_rows | Integer | |
| valid_rows | Integer | preenchido após validação |
| invalid_rows | Integer | |
| created_at | DateTime (UTC) | |

### `ImportRow`
| Coluna | Tipo | Notas |
|---|---|---|
| id | Integer PK | |
| batch_id | FK ImportBatch, index, cascade | |
| row_number | Integer | linha na planilha (1-based) |
| raw_data | JSON | linha original |
| mapped_data | JSON | após aplicar o de-para |
| validation_status | String, index | `pending`, `valid`, `invalid` |
| validation_errors | JSON | `[{field, code, message, severity}]` |
| product_id | FK Product, nullable | preenchido no confirm |

### `MediaAsset`
| Coluna | Tipo | Notas |
|---|---|---|
| id | Integer PK | |
| batch_id | FK ImportBatch, index, cascade | |
| row_ref | String | chave que liga à linha (ex.: SKU ou nome do arquivo) |
| file_path | String | caminho relativo em `uploads/imports/{batch_id}/` |
| media_type | String | `image` / `video` |
| validation_status | String | `valid` / `invalid` |
| issues | JSON | problemas detectados |

> `uploads/` entra no `.gitignore`. Em produção, migrar para object storage
> (Supabase Storage / S3) — fora do escopo desta fase.

## 5. Schemas Pydantic (`backend/app/schemas.py`)

- `ImportBatchResponse` — espelha `ImportBatch`.
- `UploadResponse` — `{ batch_id, detected_columns: [str], sample_rows: [dict], suggested_mapping: dict }`.
- `ColumnMappingRequest` — `{ mapping: Dict[str, str] }` (valida que campos obrigatórios estão cobertos).
- `ValidationError` — `{ field, code, message, severity: Literal["error","warning"] }`.
- `ImportRowResponse` — `{ row_number, mapped_data, validation_status, validation_errors, media: [MediaAssetResponse] }`.
- `ValidationSummary` — `{ total, valid, invalid, with_warnings }`.
- `ConfirmImportResponse` — `{ imported: int, skipped_invalid: int, created_product_ids: [int] }`.

## 6. Campos e regras de validação — Mercado Livre

Definir em um novo módulo `backend/app/marketplace_fields.py` (config declarativa por marketplace).

**Campos canônicos (Mercado Livre):**
| Campo | Obrigatório | Regra |
|---|---|---|
| title | sim | 1–60 chars; sem termos promocionais |
| category | sim | texto livre nesta fase (mapeamento p/ category_id é fase de publicação) |
| price | sim | > 0 |
| available_quantity | sim | inteiro ≥ 1 |
| condition | sim | `new` ou `used` |
| images | sim | ≥ 1 imagem válida |
| brand | recomendado | ausência = **aviso** |
| model | recomendado | ausência = **aviso** |
| description | recomendado | sem HTML/links/telefone/email (penalizado) |
| gtin/ean | opcional | se presente, formato numérico 8–14 dígitos |

**Erros (bloqueiam a importação da linha):**
- Campo obrigatório ausente/vazio.
- Título > 60 chars ou com termo proibido (reusar lista do agente: "frete grátis",
  "promoção", "imperdível", "original", "barato", "desconto", etc.).
- `price <= 0`; `available_quantity < 1`.
- `condition` fora de {new, used}.
- Nenhuma imagem válida.
- Imagem: URL inacessível, content-type não-imagem, ou dimensão < 500×500.

**Avisos (não bloqueiam):**
- Falta de `brand`/`model`.
- Descrição curta (< 50 chars).
- Primeira imagem possivelmente sem fundo branco (heurística; confirmação fica
  para a auditoria Gemini existente).

> **Reuso:** extrair a lista de termos proibidos e os limites de título (hoje
> embutidos no `SYSTEM_INSTRUCTION` de `agent.py` e em `MARKETPLACE_TITLE_LIMITS`
> de `constants.py`) para um único ponto compartilhado entre validador e agente.

## 7. Mapeamento de colunas

- `marketplace_fields.py` expõe, por marketplace, os campos canônicos + sinônimos
  comuns de cabeçalho (ex.: "titulo", "nome", "name" → `title`).
- No upload, gerar `suggested_mapping` por correspondência *case-insensitive* e
  fuzzy (difflib) entre cabeçalhos da planilha e sinônimos.
- O usuário ajusta no dashboard; o mapeamento final é persistido em
  `ImportBatch.column_mapping`.

## 8. Endpoints (`backend/app/main.py` ou novo `routers/imports.py`)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/imports/template` | Baixa template CSV/XLSX do marketplace |
| POST | `/imports` | Upload da planilha (multipart) → cria batch, detecta colunas |
| POST | `/imports/{id}/mapping` | Define o de-para de colunas |
| POST | `/imports/{id}/media` | Upload de imagens/vídeos (multipart, múltiplos) |
| POST | `/imports/{id}/validate` | Valida todas as linhas; grava status/erros |
| GET | `/imports` | Lista batches |
| GET | `/imports/{id}` | Status + resumo do batch |
| GET | `/imports/{id}/rows` | Linhas com status (filtro: valid/invalid/warning) |
| GET | `/imports/{id}/rows/{row_id}` | Detalhe de uma linha |
| POST | `/imports/{id}/confirm` | Cria Products (pending) só das linhas válidas |
| DELETE | `/imports/{id}` | Descarta o batch e a mídia |

> `confirm` é a única operação de escrita — e escreve apenas no **catálogo
> interno**, nunca no marketplace. Mantém a restrição de "sem escrita externa
> sem aprovação".

## 9. Parsing, mídia e dependências novas

`backend/requirements.txt` (adicionar):
- `pandas` + `openpyxl` — leitura de CSV/XLSX (alternativa leve: `openpyxl` + `csv` nativo).
- `Pillow` — validação de imagem (formato e dimensão).
- `python-multipart` — **obrigatório** para uploads no FastAPI.
- `httpx` (ou `requests`) — checar acessibilidade de URLs de imagem (com timeout).

Validação de mídia:
- Imagens: formato em {jpg, png, webp}, tamanho ≤ ~10 MB, dimensão ≥ 500×500.
- Vídeos: formato em {mp4, mov}, tamanho ≤ limite; sem inspeção de conteúdo nesta fase.
- Ligação linha↔mídia: por coluna de SKU/nome de arquivo na planilha, ou URLs diretas.

## 10. Dashboard (Streamlit) — nova aba "📦 Cadastro em Massa"

1. Uploader da planilha → mostra colunas detectadas + `suggested_mapping` editável.
2. Uploader de mídia (drag-and-drop múltiplo).
3. Botão "Validar" → relatório: contadores (válidas/inválidas/avisos) + tabela de
   linhas com erros destacados e tooltip por erro.
4. Download de relatório de erros (CSV).
5. Preview em cards das linhas válidas.
6. Botão "Confirmar importação" (habilitado só se houver ≥1 linha válida).

## 11. Sub-fases incrementais (testar antes de avançar)

- **1A** — Upload + detecção de colunas + mapeamento + validação determinística
  (imagens por URL) + preview + confirm. *Núcleo entregável e testável.*
- **1B** — Upload de arquivos de mídia (multipart) + casamento por nome/SKU +
  validação de imagem com Pillow.
- **1C** — Template download + auto-sugestão de mapeamento (fuzzy) + relatório de
  erros exportável.

## 12. Plano de teste

**Fixtures** (`backend/tests/fixtures/`):
- `import_valido.csv` — 3 linhas 100% válidas.
- `import_com_erros.csv` — linhas com: título > 60, termo proibido, preço ≤ 0,
  sem imagem, campo obrigatório faltando, condição inválida.
- `import_xlsx_valido.xlsx` — equivalente em XLSX.

**Testes unitários (pytest):**
- Validador de título: limite de chars e termos proibidos.
- Validador de preço/quantidade/condição.
- Validador de imagem (mock de URL: 200 imagem / 404 / content-type errado / dimensão pequena).
- Auto-sugestão de mapeamento (sinônimos e fuzzy).

**Testes de endpoint (FastAPI TestClient):**
1. `POST /imports` com CSV → 200, retorna colunas detectadas.
2. `POST /imports/{id}/mapping` cobrindo obrigatórios → 200; faltando obrigatório → 422.
3. `POST /imports/{id}/validate` no arquivo com erros → resumo com `invalid > 0` e
   erros corretos por linha.
4. `POST /imports/{id}/confirm` → cria Products **somente** das linhas válidas
   (assert: produto incompleto **não** é criado); novos produtos com `status=pending`.
5. `GET /imports/{id}/rows?status=invalid` → só as linhas inválidas.
6. `GET /imports/template` → arquivo com cabeçalhos esperados.
7. (1B) `POST /imports/{id}/media` com imagem válida/ inválida → status correto.

**Critério de aceite:** nenhuma linha inválida vira produto; toda checagem de
URL/imagem é logada; nada é enviado a marketplace.

## 13. Restrições do documento atendidas

- ✅ Nenhuma escrita externa: `confirm` cria só no catálogo interno (pending).
- ✅ Validação bloqueia produto incompleto antes de qualquer envio.
- ✅ Chamadas externas (fetch de imagem) logadas com latência (reusar `AuditLog`
  ou criar `ExternalCallLog` genérico — decisão na seção 14).
- ✅ Credenciais fora do código (esta fase não usa credenciais de marketplace).

## 14. Decisões em aberto (confirmar antes de codar)

1. **Parser**: `pandas` (robusto, +peso) vs `openpyxl`+`csv` (leve)? → sugestão: `pandas`.
2. **Mídia**: aceitar **URLs na planilha** (mais simples, alinhado ao
   `test_listings.json`) já na 1A, e deixar **upload de arquivos** para a 1B? → sugestão: sim.
3. **Log de chamadas externas**: reaproveitar `AuditLog` ou criar tabela
   `ExternalCallLog` genérica? → sugestão: criar `ExternalCallLog` (mais limpo).
4. **Estrutura**: manter tudo em `main.py` ou criar `backend/app/routers/imports.py`
   e `services/import_service.py`? → sugestão: separar em router + service (o main já
   está crescendo).
