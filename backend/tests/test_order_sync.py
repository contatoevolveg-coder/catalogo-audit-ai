import os
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, Tenant, Order, OrderItem, Product, Credential
from backend.app.services.order_service import sync_ml_orders_for_credential

TEST_DB_PATH = "test_temp_ordersync.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    # tenant padrão (id=1) para os registros de teste
    db.add(Tenant(name="Tenant Teste"))
    db.commit()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass


@pytest.fixture
def test_product():
    """Produto (ainda não persistido) no tenant padrão."""
    return Product(
        tenant_id=1,
        title="Produto Teste",
        marketplace="mercado_livre",
        status="pending",
        price=10.0,
    )


@pytest.fixture
def mock_ml_fetch_orders():
    with patch("backend.app.services.order_service.fetch_orders") as mock:
        mock.return_value = [
            {
                "id": 12345678,
                "status": "paid",
                "total_amount": 100.5,
                "buyer": {"nickname": "BUYER_TEST"},
                "order_items": [
                    {
                        "item": {
                            "id": "MLB123",
                            "seller_sku": "SKU-TEST-1",
                            "title": "Produto Teste 1"
                        },
                        "quantity": 2,
                        "unit_price": 50.25
                    }
                ]
            }
        ]
        yield mock


@pytest.fixture
def mock_ml_seller_id():
    with patch("backend.app.services.order_service.get_seller_id") as mock:
        mock.return_value = "12345"
        yield mock


@pytest.fixture
def mock_decrypt_secret():
    with patch("backend.app.services.order_service.decrypt_secret") as mock:
        mock.return_value = {"access_token": "test_token"}
        yield mock


def test_sync_new_order_and_deduct_stock(db_session, test_product, mock_ml_fetch_orders, mock_ml_seller_id, mock_decrypt_secret):
    cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", label="ML Test", encrypted_secret="test", masked_preview="test", scopes=[], status="valid")
    db_session.add(cred)

    test_product.erp_sku = "SKU-TEST-1"
    test_product.available_quantity = 10
    test_product.external_listing_id = "MLB123"
    db_session.add(test_product)
    db_session.commit()

    synced, deductions = sync_ml_orders_for_credential(db_session, cred, tenant_id=1)

    assert synced == 1
    assert deductions == 1

    order = db_session.query(Order).filter_by(external_order_id="12345678").first()
    assert order is not None
    assert order.status == "paid"
    assert order.stock_deducted is True
    assert order.total_amount == 100.5

    items = db_session.query(OrderItem).filter_by(order_id=order.id).all()
    assert len(items) == 1
    assert items[0].sku == "SKU-TEST-1"
    assert items[0].quantity == 2

    db_session.refresh(test_product)
    assert test_product.available_quantity == 8


def test_sync_existing_order_no_duplicate_deduction(db_session, test_product, mock_ml_fetch_orders, mock_ml_seller_id, mock_decrypt_secret):
    cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", label="ML Test", encrypted_secret="test", masked_preview="test", scopes=[], status="valid")
    db_session.add(cred)

    test_product.erp_sku = "SKU-TEST-1"
    test_product.available_quantity = 10
    db_session.add(test_product)
    db_session.commit()

    existing_order = Order(
        tenant_id=1,
        marketplace="mercado_livre",
        external_order_id="12345678",
        credential_id=cred.id,
        total_amount=100.5,
        status="paid",
        stock_deducted=True,
    )
    db_session.add(existing_order)
    db_session.commit()

    synced, deductions = sync_ml_orders_for_credential(db_session, cred, tenant_id=1)

    assert synced == 1
    assert deductions == 0  # Nenhuma nova dedução

    db_session.refresh(test_product)
    assert test_product.available_quantity == 10  # Permaneceu igual
