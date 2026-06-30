from typing import Dict, List, Any

# Definição declarativa por marketplace dos campos e cabeçalhos sinônimos
MARKETPLACE_FIELDS: Dict[str, Dict[str, Any]] = {
    "mercado_livre": {
        "fields": {
            "title": {
                "name": "title",
                "label": "Título",
                "required": True,
                "recommended": True,
                "synonyms": ["título", "titulo", "nome", "name", "anúncio", "anuncio"]
            },
            "category": {
                "name": "category",
                "label": "Categoria",
                "required": True,
                "recommended": True,
                "synonyms": ["categoria", "category", "seção", "secao"]
            },
            "price": {
                "name": "price",
                "label": "Preço",
                "required": True,
                "recommended": True,
                "synonyms": ["preço", "preco", "valor", "price"]
            },
            "available_quantity": {
                "name": "available_quantity",
                "label": "Estoque",
                "required": True,
                "recommended": True,
                "synonyms": ["quantidade", "estoque", "stock", "qtd", "available_quantity", "quantidades"]
            },
            "condition": {
                "name": "condition",
                "label": "Condição",
                "required": True,
                "recommended": True,
                "synonyms": ["condição", "condicao", "condition", "estado"]
            },
            "images": {
                "name": "images",
                "label": "Imagens",
                "required": True,
                "recommended": True,
                "synonyms": ["imagens", "fotos", "images", "urls", "imagem", "foto", "url_imagem", "url_imagens"]
            },
            "brand": {
                "name": "brand",
                "label": "Marca",
                "required": False,
                "recommended": True,
                "synonyms": ["marca", "brand", "fabricante"]
            },
            "model": {
                "name": "model",
                "label": "Modelo",
                "required": False,
                "recommended": True,
                "synonyms": ["modelo", "model"]
            },
            "description": {
                "name": "description",
                "label": "Descrição",
                "required": False,
                "recommended": True,
                "synonyms": ["descrição", "descricao", "description", "texto"]
            },
            "gtin_ean": {
                "name": "gtin_ean",
                "label": "GTIN/EAN",
                "required": False,
                "recommended": False,
                "synonyms": ["gtin", "ean", "código_de_barras", "codigo_de_barras", "barcode", "gtin_ean"]
            }
        }
    }
}

def get_canonical_fields(marketplace: str) -> Dict[str, Any]:
    """Retorna a definição de campos canônicos para um determinado marketplace.

    Fallback para mercado_livre se não encontrado.
    """
    mkt = marketplace.lower() if marketplace else "mercado_livre"
    return MARKETPLACE_FIELDS.get(mkt, MARKETPLACE_FIELDS["mercado_livre"])["fields"]
