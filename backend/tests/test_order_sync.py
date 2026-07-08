import pytest
from unittest.mock import patch, MagicMock
from backend.app.services.order_service import sync_ml_orders_for_credential
from backend.app.database import Order, OrderItem, Product

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
    # Prepare credential
    from backend.app.database import Credential
    cred = Credential(provider="mercado_livre", provider_type="marketplace", label="ML Test", encrypted_secret="test", masked_preview="test", scopes=[], status="valid")
    db_session.add(cred)
    
    # Prepare product matching the SKU
    test_product.erp_sku = "SKU-TEST-1"
    test_product.available_quantity = 10
    test_product.external_listing_id = "MLB123"
    db_session.add(test_product)
    db_session.commit()

    synced, deductions = sync_ml_orders_for_credential(db_session, cred)

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
    from backend.app.database import Credential
    cred = Credential(provider="mercado_livre", provider_type="marketplace", label="ML Test", encrypted_secret="test", masked_preview="test", scopes=[], status="valid")
    db_session.add(cred)
    
    test_product.erp_sku = "SKU-TEST-1"
    test_product.available_quantity = 10
    db_session.add(test_product)
    
    # Add an existing order that was already deducted
    existing_order = Order(
        marketplace="mercado_livre",
        external_order_id="12345678",
        credential_id=1,
        total_amount=100.5,
        status="paid",
        stock_deducted=True
    )
    db_session.add(existing_order)
    db_session.commit()

    synced, deductions = sync_ml_orders_for_credential(db_session, cred)

    assert synced == 1
    assert deductions == 0 # No new deduction

    db_session.refresh(test_product)
    assert test_product.available_quantity == 10 # Remained the same
