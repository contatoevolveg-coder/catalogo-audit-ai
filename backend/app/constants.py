"""Constantes centrais do projeto: marketplaces, status e severidades.

Centralizar esses valores evita "strings mágicas" espalhadas pelo código e
garante consistência entre o banco de dados, a API e o dashboard.
"""
from enum import Enum


class Marketplace(str, Enum):
    MERCADO_LIVRE = "mercado_livre"
    SHOPEE = "shopee"
    AMAZON = "amazon"
    MAGALU = "magalu"
    TEMU = "temu"
    SHEIN = "shein"
    TIKTOK_SHOP = "tiktok_shop"


class ProductStatus(str, Enum):
    PENDING = "pending"
    AUDITED = "audited"
    OPTIMIZED = "optimized"


class SuggestionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Limites de título por marketplace (em caracteres), usados como referência
# de validação e auditoria.
MARKETPLACE_TITLE_LIMITS = {
    Marketplace.MERCADO_LIVRE: 60,
    Marketplace.SHOPEE: 120,
    Marketplace.AMAZON: 200,
    Marketplace.MAGALU: 100,
    Marketplace.TEMU: 100,
    Marketplace.SHEIN: 100,
    Marketplace.TIKTOK_SHOP: 100,
}
