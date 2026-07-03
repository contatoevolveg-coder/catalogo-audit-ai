import io
from typing import List, Optional
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException, Response
from sqlalchemy.orm import Session

from backend.app.database import get_db, ImportBatch, ImportRow, User
from backend.app.security.dependencies import get_current_user
from backend.app.schemas import (
    UploadResponse, ColumnMappingRequest, ImportRowResponse, 
    ValidationSummary, ImportBatchResponse, ConfirmImportResponse
)
from backend.app.services import import_service

router = APIRouter(prefix="/imports", tags=["Imports"])

@router.get("/template")
def download_template(marketplace: str = "mercado_livre", current_user: User = Depends(get_current_user)):
    """Retorna um modelo de planilha CSV pré-formatado com os cabeçalhos padrão do marketplace."""
    headers = [
        "title", "category", "price", "available_quantity", "condition", 
        "images", "brand", "model", "description", "gtin_ean"
    ]
    output = io.StringIO()
    output.write(";".join(headers) + "\n")
    
    # Linha de amostra/exemplo
    sample = [
        "Fone de Ouvido Bluetooth JBL Tune 510", 
        "Eletrônicos > Áudio", 
        "199.90", 
        "15", 
        "new", 
        "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=800", 
        "JBL", 
        "Tune 510BT", 
        "Descrição de exemplo do fone de ouvido JBL.", 
        "7891234567890"
    ]
    output.write(";".join(sample) + "\n")
    
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=template_import_{marketplace}.csv"}
    )

@router.post("", response_model=UploadResponse)
def upload_planilha(
    file: UploadFile = File(...),
    marketplace: str = Form("mercado_livre"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Recebe o arquivo de planilha via multipart, cria o lote de importação e retorna a amostra."""
    content = file.file.read()
    return import_service.parse_and_create_batch(content, file.filename, marketplace, db)

@router.post("/{batch_id}/mapping", response_model=ImportBatchResponse)
def define_mapping(
    batch_id: int,
    req: ColumnMappingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Define o de-para de colunas para o lote. Valida se os campos obrigatórios foram mapeados."""
    return import_service.save_column_mapping(batch_id, req.mapping, db)

@router.post("/{batch_id}/validate", response_model=ValidationSummary)
def validate_batch(batch_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Dispara a validação determinística de todas as linhas de um lote mapeado."""
    return import_service.validate_batch(batch_id, db)

@router.get("", response_model=List[ImportBatchResponse])
def list_batches(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Lista todos os lotes de importação cadastrados."""
    return db.query(ImportBatch).order_by(ImportBatch.id.desc()).all()

@router.get("/{batch_id}", response_model=ImportBatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Retorna detalhes e status de um lote de importação."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote de importação não encontrado.")
    return batch

@router.get("/{batch_id}/rows", response_model=List[ImportRowResponse])
def get_batch_rows(
    batch_id: int,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retorna as linhas de dados de um lote, permitindo filtro opcional por status de validação."""
    query = db.query(ImportRow).filter(ImportRow.batch_id == batch_id)
    
    if status:
        if status == "warning":
            # Filtra em Python para maior compatibilidade de banco de dados (JSON fields no SQLite/Postgres)
            rows = query.all()
            filtered_rows = []
            for r in rows:
                if r.validation_status == "valid" and r.validation_errors:
                    if any(e.get("severity") == "warning" for e in r.validation_errors):
                        filtered_rows.append(r)
            return filtered_rows
        else:
            query = query.filter(ImportRow.validation_status == status)
            
    return query.order_by(ImportRow.row_number.asc()).all()

@router.get("/{batch_id}/rows/{row_id}", response_model=ImportRowResponse)
def get_row(batch_id: int, row_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Retorna o detalhe e erros de validação de uma linha específica."""
    row = db.query(ImportRow).filter(ImportRow.batch_id == batch_id, ImportRow.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Linha de importação não encontrada.")
    return row

@router.post("/{batch_id}/confirm", response_model=ConfirmImportResponse)
def confirm_import(
    batch_id: int,
    auto_audit: bool = False,
    max_audit: int = 15,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Confirma a importação do lote, criando Products somente a partir de linhas válidas.
    Se auto_audit for True, inicia a auditoria automática via Gemini respeitando o limite max_audit.
    """
    return import_service.confirm_import(batch_id, db, auto_audit=auto_audit, max_audit=max_audit)


@router.delete("/{batch_id}")
def delete_batch(batch_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Descarta o lote de importação e limpa todas as suas linhas."""
    import_service.discard_batch(batch_id, db)
    return {"message": "Lote de importação descartado com sucesso."}
