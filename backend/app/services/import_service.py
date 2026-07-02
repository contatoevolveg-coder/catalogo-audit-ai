import io
import os
import difflib
import pandas as pd
from typing import Dict, List, Any, Tuple
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.database import ImportBatch, ImportRow, Product
from backend.app.marketplace_fields import get_canonical_fields
from backend.app.validators import validate_row_ml
from backend.app.schemas import UploadResponse, ValidationSummary, ConfirmImportResponse

def read_planilha(file_content: bytes, filename: str) -> pd.DataFrame:
    """Lê o arquivo de planilha (Excel ou CSV) utilizando pandas e retorna um DataFrame.

    Detecta delimitadores comuns de CSV (, ou ;) e trata encodings (utf-8 ou latin-1).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(io.BytesIO(file_content), engine="openpyxl")
    
    # Tratamento para CSV
    try:
        content_str = file_content.decode("utf-8")
    except UnicodeDecodeError:
        content_str = file_content.decode("latin-1")

    # Detecta o delimitador analisando a primeira linha
    first_line = content_str.split("\n")[0] if content_str else ""
    delimiter = ";" if ";" in first_line else ","

    return pd.read_csv(io.StringIO(content_str), sep=delimiter)

def generate_suggested_mapping(detected_columns: List[str], marketplace: str) -> Dict[str, str]:
    """Gera sugestões de mapeamento baseadas em correspondência exata, sinônimos e busca fuzzy."""
    canonical_fields = get_canonical_fields(marketplace)
    mapping = {}

    cleaned_detected = {col.strip().lower(): col for col in detected_columns}

    for canon_name, canon_info in canonical_fields.items():
        synonyms = [s.lower() for s in canon_info.get("synonyms", [])]
        matched_col = None

        # 1. Correspondência exata ou sinônimo
        for clean_col, orig_col in cleaned_detected.items():
            if clean_col == canon_name.lower() or clean_col in synonyms:
                matched_col = orig_col
                break

        # 2. Se não encontrar, tenta busca fuzzy (similaridade de strings)
        if not matched_col:
            lookup_terms = [canon_name.lower()] + synonyms
            best_match = None
            highest_score = 0.0

            for clean_col, orig_col in cleaned_detected.items():
                for term in lookup_terms:
                    score = difflib.SequenceMatcher(None, clean_col, term).ratio()
                    if score > 0.75 and score > highest_score:
                        highest_score = score
                        best_match = orig_col
            
            if best_match:
                matched_col = best_match

        if matched_col:
            # Mapeamento invertido: a chave é a coluna física do arquivo e o valor é o campo canônico do sistema
            mapping[matched_col] = canon_name

    return mapping

def parse_and_create_batch(file_content: bytes, filename: str, marketplace: str, db: Session) -> UploadResponse:
    """Processa o upload do arquivo, cria o lote de importação (ImportBatch) e salva as linhas brutas (ImportRow)."""
    try:
        df = read_planilha(file_content, filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar arquivo: {str(e)}")

    # Limpa nomes de colunas
    df.columns = [str(col).strip() for col in df.columns]
    detected_columns = df.columns.tolist()

    # Preenche NaNs com None para serialização JSON correta no banco
    df_clean = df.where(pd.notnull(df), None)

    # Cria o lote de importação
    batch = ImportBatch(
        filename=filename,
        marketplace=marketplace,
        status="uploaded",
        total_rows=len(df_clean),
        valid_rows=0,
        invalid_rows=0
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    # Cria as linhas do lote
    for idx, row in df_clean.iterrows():
        import_row = ImportRow(
            batch_id=batch.id,
            row_number=int(idx) + 1,  # 1-based index
            raw_data=row.to_dict(),
            validation_status="pending"
        )
        db.add(import_row)
    
    db.commit()

    # Gera a sugestão de de-para das colunas
    suggested_mapping = generate_suggested_mapping(detected_columns, marketplace)

    # Retorna uma amostra de até 3 linhas
    sample_rows = df_clean.head(3).to_dict(orient="records")

    return UploadResponse(
        batch_id=batch.id,
        detected_columns=detected_columns,
        sample_rows=sample_rows,
        suggested_mapping=suggested_mapping
    )

def save_column_mapping(batch_id: int, mapping: Dict[str, str], db: Session) -> ImportBatch:
    """Verifica e salva o de-para de colunas. Lança erro 422 se faltar campos obrigatórios."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote de importação não encontrado.")

    canonical_fields = get_canonical_fields(batch.marketplace)
    required_canonicals = [name for name, info in canonical_fields.items() if info.get("required")]

    # Inverte o mapeamento enviado para validar quais campos canônicos foram cobertos
    mapped_canonicals = list(mapping.values())

    missing_fields = [f for f in required_canonicals if f not in mapped_canonicals]
    if missing_fields:
        # Erro 422 se faltar campos obrigatórios
        raise HTTPException(
            status_code=422,
            detail=f"Falta mapear campos obrigatórios para o Mercado Livre: {', '.join(missing_fields)}"
        )

    batch.column_mapping = mapping
    batch.status = "mapped"
    db.commit()
    db.refresh(batch)
    return batch

def validate_batch(batch_id: int, db: Session) -> ValidationSummary:
    """Executa a validação determinística linha a linha de um lote mapeado."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    if not batch.column_mapping:
        raise HTTPException(status_code=400, detail="É necessário configurar o mapeamento antes de validar.")

    rows = db.query(ImportRow).filter(ImportRow.batch_id == batch_id).all()
    mapping = batch.column_mapping

    total = len(rows)
    valid = 0
    invalid = 0
    with_warnings = 0

    for row in rows:
        raw = row.raw_data
        mapped = {}

        # Aplica o de-para
        for file_col, canon_col in mapping.items():
            val = raw.get(file_col)
            # Remove espaços de strings se houver
            if isinstance(val, str):
                val = val.strip()
            
            # Formata imagens em lista se for o campo correspondente
            if canon_col == "images" and val:
                if isinstance(val, str):
                    val = [img.strip() for img in val.split(",") if img.strip()]
                elif not isinstance(val, list):
                    val = [str(val)]
            
            mapped[canon_col] = val

        # Roda o validador específico do Mercado Livre
        errors = validate_row_ml(mapped, db)

        # Determina status da linha
        has_errors = any(e["severity"] == "error" for e in errors)
        has_warnings = any(e["severity"] == "warning" for e in errors)

        if has_errors:
            row.validation_status = "invalid"
            invalid += 1
        else:
            row.validation_status = "valid"
            valid += 1
            if has_warnings:
                with_warnings += 1

        row.mapped_data = mapped
        row.validation_errors = errors

    # Atualiza status do lote
    batch.status = "validated"
    batch.total_rows = total
    batch.valid_rows = valid
    batch.invalid_rows = invalid
    db.commit()

    return ValidationSummary(
        total=total,
        valid=valid,
        invalid=invalid,
        with_warnings=with_warnings
    )

def _normalize_condition(value: Any) -> Any:
    """Normaliza a condição para o padrão canônico (new/used), aceitando PT-BR."""
    if value is None:
        return None
    v = str(value).strip().lower()
    return {"novo": "new", "new": "new", "usado": "used", "used": "used"}.get(v, v)


def _clean_gtin(value: Any) -> Any:
    """Limpa o GTIN/EAN removendo o sufixo '.0' que o pandas costuma gerar."""
    if value is None:
        return None
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s or None


def confirm_import(batch_id: int, db: Session) -> ConfirmImportResponse:
    """Confirma a importação criando produtos pendentes somente para as linhas válidas."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    # Evita reimportação: confirmar duas vezes criaria produtos duplicados.
    if batch.status == "imported":
        raise HTTPException(status_code=400, detail="Este lote já foi importado.")

    if batch.status != "validated":
        # Força validação se ainda não foi feita
        validate_batch(batch_id, db)
        db.refresh(batch)

    valid_rows = db.query(ImportRow).filter(
        ImportRow.batch_id == batch_id,
        ImportRow.validation_status == "valid"
    ).all()

    created_ids = []

    for row in valid_rows:
        data = row.mapped_data

        # Formata imagens garantindo lista de strings
        imgs = data.get("images", [])
        if isinstance(imgs, str):
            imgs = [img.strip() for img in imgs.split(",") if img.strip()]

        # Converte estoque para inteiro quando possível
        qty_raw = data.get("available_quantity")
        try:
            qty = int(float(qty_raw)) if qty_raw is not None and str(qty_raw).strip() != "" else None
        except (ValueError, TypeError):
            qty = None

        # Cria produto preservando TODOS os campos validados
        product = Product(
            title=data["title"],
            description=data.get("description"),
            category=data.get("category"),
            price=float(data["price"]) if data.get("price") is not None else 0.0,
            marketplace=batch.marketplace,
            status="pending",
            images=imgs,
            available_quantity=qty,
            condition=_normalize_condition(data.get("condition")),
            attributes={
                "brand": data.get("brand"),
                "model": data.get("model"),
                "gtin_ean": _clean_gtin(data.get("gtin_ean")),
            },
        )
        db.add(product)
        db.flush()  # obtém o id sem encerrar a transação

        row.product_id = product.id
        created_ids.append(product.id)

    # Marca o lote como importado e comita tudo de uma vez (atômico)
    batch.status = "imported"
    db.commit()

    skipped = batch.total_rows - len(created_ids)

    return ConfirmImportResponse(
        imported=len(created_ids),
        skipped_invalid=skipped,
        created_product_ids=created_ids
    )

def discard_batch(batch_id: int, db: Session) -> None:
    """Descarta o lote e todas as linhas vinculadas via cascade."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    db.delete(batch)
    db.commit()
