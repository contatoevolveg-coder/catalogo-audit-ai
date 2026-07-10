import os
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, Product, Credential, StockReconciliationLog
from backend.app.services.stock_reconciliation_service import reconcile_stock

TEST_DB_PATH = "test_temp_stock_reconciliation.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass


@patch("backend.app.services.stock_reconciliation_service.find_product_by_sku")
@patch("backend.app.services.stock_reconciliation_service.get_stock_quantity")
@patch("backend.app.services.stock_reconciliation_service.update_product")
def test_reconcile_stock_vendendo_fantasma(mock_update_product, mock_get_stock_quantity, mock_find_by_sku):
    db = TestingSessionLocal()

    # Mock credenciais
    bling_cred = Credential(tenant_id=1, provider="bling", provider_type="erp", status="valid", label="Bling", encrypted_secret="test", masked_preview="test", scopes={})
    ml_cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", status="valid", label="ML", encrypted_secret="test", masked_preview="test", scopes={})
    db.add_all([bling_cred, ml_cred])
    db.commit()

    # Criacao do produto "Fantasma" (Bling 0, Marketplace 10)
    p = Product(tenant_id=1, title="Test", marketplace="mercado_livre", status="audited", available_quantity=10, erp_sku="SKU123", external_listing_id="MLB123")
    db.add(p)
    db.commit()

    # Mock das funcoes externas: resolve SKU -> ID interno do Bling, depois consulta saldo
    mock_find_by_sku.return_value = ("success", {"id": 999})
    mock_get_stock_quantity.return_value = ("success", 0)  # Bling retorna 0

    with patch("backend.app.services.stock_reconciliation_service.decrypt_secret", return_value={"access_token": "token"}):
        reconcile_stock(1, db)

    # Verifica Log
    log = db.query(StockReconciliationLog).filter_by(product_id=p.id).first()
    assert log is not None
    assert log.category == "vendendo_fantasma"
    assert log.bling_quantity == 0
    assert log.marketplace_quantity == 10

    # Verifica se a correcao foi chamada
    mock_update_product.assert_called_once_with(
        product_id=p.id,
        tenant_id=1,
        db=db,
        changes={"available_quantity": 0},
        credential_id=ml_cred.id,
        sync_to_ml=True
    )

    db.close()
