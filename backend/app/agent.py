import time
import logging
from typing import Tuple
from google import genai
from google.genai import types

from backend.app.config import GEMINI_API_KEY, GEMINI_MODEL, is_mock_mode, is_api_key_configured
from backend.app.schemas import GeminiAuditResponse, MissingAttribute, ImageIssue

# A configuração de handlers/nível de log é feita centralmente em main.py.
logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """
Você é o Agente especialista em Catálogo de E-commerce. Sua função é auditar anúncios de produtos criados para marketplaces brasileiros (Mercado Livre, Shopee, Amazon, Magalu, Temu, Shein, TikTok Shop) e gerar sugestões de melhoria (título, descrição, atributos faltantes e problemas nas imagens).

Você deve avaliar os dados do produto contra as políticas e boas práticas do marketplace selecionado:

### Mercado Livre:
1. Títulos: Máximo 60 caracteres. Não inclua palavras de promoção (ex: frete grátis, imperdível, de graça, desconto, original, promoção, barato). Use a estrutura: Produto + Marca + Modelo + Especificação técnica.
2. Descrição: Apenas texto puro. Links externos, emails, telefones, ou HTML são estritamente proibidos e penalizados.
3. Imagens: Primeira imagem deve ter fundo branco puro (RGB 255, 255, 255) sem marcas d'água, textos promocionais ou bordas. Resolução mínima: 500x500px.

### Shopee:
1. Títulos: Máximo 120 caracteres. Use a estrutura: Marca + Nome do Produto + Modelo + Especificação técnica.
2. Descrição: Detalhada, destacando benefícios e ficha técnica. Incluir hashtags relevantes ao final (ex: #fonesemfio #tecnologia).
3. Imagens: Primeira imagem clara, com fundo neutro. Aceita elementos visuais sutis, mas o produto deve ser o foco principal.

### Amazon:
1. Títulos: Limite recomendado de 80 a 150 caracteres (máximo 200). Primeira letra de cada palavra deve ser maiúscula (exceto preposições). Sem termos promocionais.
2. Descrição: Deve destacar de 3 a 5 bullet points (recursos principais) no início, seguido por uma descrição detalhada.
3. Imagens: Primeira imagem deve ter fundo branco puro, e o produto deve ocupar no mínimo 85% do espaço da imagem.

### Magalu, Temu, Shein e TikTok Shop:
1. Títulos: Curtos, diretos e objetivos.
2. Descrição: Foco em especificações de tamanho, material e cores. TikTok Shop deve incluir tags de tendência ou apelo visual moderno.
3. Imagens: Imagens de alta qualidade mostrando ângulos reais.

Você deve responder OBRIGATORIAMENTE no formato JSON especificado pelo schema fornecido.
Seus campos de retorno são:
- suggested_title: O novo título proposto de acordo com o marketplace.
- suggested_description: A nova descrição proposta, com os ajustes necessários (ex: bullet points para Amazon, texto limpo para Mercado Livre, hashtags para Shopee).
- missing_attributes: Lista de atributos que deveriam estar preenchidos no cadastro do produto (ex: Marca, Modelo, Cor, Voltagem, Material, etc.), indicando o valor sugerido e a justificativa para cada um.
- image_issues: Lista de problemas detectados nas URLs das imagens (ex: fundo não branco, resolução baixa, presença de marcas d'água, banners promocionais), indicando a gravidade (HIGH, MEDIUM, LOW) e justificativa.
- seo_score: Uma nota de 0 a 100 de quão otimizado o anúncio ORIGINAL está para as práticas do marketplace.

Importante: Como você não consegue baixar a imagem diretamente, avalie com base nos nomes dos arquivos de imagem (se houver indício na URL de marca d'água ou promoção) ou infira as diretrizes gerais de auditoria de imagens para o produto, sugerindo que o vendedor garanta o fundo branco puro se o marketplace exigir.
"""

def get_gemini_client() -> genai.Client:
    """Instancia e retorna o cliente oficial do Google GenAI.

    Lança ValueError quando a chave não está configurada (placeholder ou vazia).
    O modo mock é tratado antes desta função, em audit_product_with_gemini.
    """
    if not is_api_key_configured():
        raise ValueError(
            "Chave de API do Gemini não configurada! Defina a variável GEMINI_API_KEY no arquivo .env "
            "(ou use GEMINI_API_KEY=mock para rodar com respostas simuladas)."
        )
    return genai.Client(api_key=GEMINI_API_KEY)

def get_mock_response(title: str, marketplace: str, images: list) -> GeminiAuditResponse:
    """Gera respostas simuladas (mock) realistas para testes rápidos sem chave da API."""
    title_lower = title.lower()
    
    if "fone" in title_lower:
        return GeminiAuditResponse(
            suggested_title="Fone de Ouvido Bluetooth JBL Tune 510BT Sem Fio Preto",
            suggested_description="Fone de ouvido Bluetooth JBL Tune 510BT. Conectividade sem fio com Bluetooth 5.0, bateria de longa duração para até 40 horas de uso contínuo e tecnologia de carregamento rápido por USB-C. Desfrute do renomado som JBL Pure Bass com total liberdade de movimentos.",
            missing_attributes=[
                MissingAttribute(
                    name="Marca",
                    recommended_value="JBL",
                    reason="O Mercado Livre penaliza anúncios sem marca especificada no título ou ficha técnica."
                ),
                MissingAttribute(
                    name="Modelo",
                    recommended_value="Tune 510BT",
                    reason="Essencial para o cliente identificar as características técnicas exatas do aparelho."
                ),
                MissingAttribute(
                    name="Cor",
                    recommended_value="Preto",
                    reason="Atributo de variação crítico para a escolha do usuário."
                )
            ],
            image_issues=[
                ImageIssue(
                    image_url=images[0] if images else "imagem_1.jpg",
                    issue="A primeira imagem possui um fundo com elementos visuais e sombras, o Mercado Livre exige fundo branco puro (RGB 255,255,255).",
                    severity="HIGH"
                )
            ],
            seo_score=45
        )
    elif "camiseta" in title_lower:
        return GeminiAuditResponse(
            suggested_title="Camiseta Masculina Preta 100% Algodão Slim Fit Premium Confortável",
            suggested_description="Camiseta Masculina confeccionada em malha 100% algodão penteado fio 30.1, garantindo conforto, maciez e alta durabilidade.\n\nBenefícios:\n- Costura reforçada com acabamento premium de ombro a ombro.\n- Caimento moderno Slim Fit ideal para o dia a dia.\n- Não encolhe e não desbota após a lavagem.\n\n#camiseta #modamasculina #estilocasual #algodao",
            missing_attributes=[
                MissingAttribute(
                    name="Marca",
                    recommended_value="Genérico",
                    reason="A Shopee recomenda informar a marca (ou 'Genérico') para indexação nos filtros de pesquisa."
                ),
                MissingAttribute(
                    name="Material",
                    recommended_value="Algodão",
                    reason="Especificar o material ajuda a converter vendas de clientes que procuram tecidos específicos."
                ),
                MissingAttribute(
                    name="Gênero",
                    recommended_value="Masculino",
                    reason="Facilita a inclusão do anúncio nas categorias de moda específicas por gênero."
                )
            ],
            image_issues=[],
            seo_score=55
        )
    elif "garrafa" in title_lower:
        return GeminiAuditResponse(
            suggested_title="Garrafa Térmica Inox Kouda 500ml Parede Dupla com Isolamento a Vácuo",
            suggested_description="Garrafa Térmica Inox Kouda 500ml.\n\nRECURSOS PRINCIPAIS:\n* ISOLAMENTO A VÁCUO: Mantém bebidas frias por até 24 horas e bebidas quentes por até 12 horas.\n* PAREDE DUPLA: Tecnologia que evita a condensação na parte externa (a garrafa não 'sua').\n* AÇO INOXIDÁVEL 18/8: Material de grau alimentício livre de BPA, que não transfere sabor nem enferruja.\n* TAMPA ANTIVAZAMENTO: Vedação hermética de silicone de alta performance.\n\nAdquira uma garrafa térmica elegante e durável para academia, escritório ou viagens.",
            missing_attributes=[
                MissingAttribute(
                    name="Marca",
                    recommended_value="Kouda",
                    reason="A Amazon prioriza anúncios que começam com a marca do produto no título."
                ),
                MissingAttribute(
                    name="Capacidade",
                    recommended_value="500ml",
                    reason="Atributo primário de escolha para garrafas térmicas e recipientes de líquidos."
                )
            ],
            image_issues=[
                ImageIssue(
                    image_url=images[0] if images else "imagem_1.jpg",
                    issue="A imagem principal deve conter apenas o produto principal ocupando pelo menos 85% do espaço e com fundo totalmente branco.",
                    severity="MEDIUM"
                )
            ],
            seo_score=50
        )
    else:
        # Carregador Portátil ou Genérico
        return GeminiAuditResponse(
            suggested_title="Carregador Portátil Power Bank Pineng PN-951 10000mAh Slim Bateria Externa",
            suggested_description="Carregador Portátil Power Bank Pineng PN-951 10000mAh original.\n\nEspecificações Técnicas:\n- Capacidade: 10.000mAh (permite de 2 a 3 recargas completas no celular).\n- Cabos embutidos: Conectores Lightning (iPhone), Micro-USB e Tipo-C integrados no próprio carregador.\n- Design Slim ultrafino para transporte fácil no bolso ou bolsa.\n- Indicador LED de bateria restante.",
            missing_attributes=[
                MissingAttribute(
                    name="Marca",
                    recommended_value="Pineng",
                    reason="A Magalu utiliza a marca como parâmetro de busca essencial na ficha técnica."
                ),
                MissingAttribute(
                    name="Modelo",
                    recommended_value="PN-951",
                    reason="Ajuda o consumidor a pesquisar avaliações e compatibilidade de carregamento."
                ),
                MissingAttribute(
                    name="Capacidade da Bateria",
                    recommended_value="10000mAh",
                    reason="Atributo mais importante para carregadores portáteis e baterias."
                )
            ],
            image_issues=[
                ImageIssue(
                    image_url=images[0] if images else "imagem_1.jpg",
                    issue="A imagem contém textos de marketing como 'Original' e selos promocionais. A Magalu recomenda fundos limpos e sem textos artificiais.",
                    severity="HIGH"
                )
            ],
            seo_score=40
        )

def audit_product_with_gemini(
    title: str,
    description: str,
    images: list,
    category: str,
    price: float,
    marketplace: str,
    max_retries: int = 5,
    initial_delay: float = 2.0,
    backoff_factor: float = 2.0
) -> Tuple[GeminiAuditResponse, int, int, float]:
    """
    Chama a API do Gemini com structured output para auditar o anúncio do produto.
    Caso GEMINI_API_KEY esteja como 'mock', retorna uma resposta simulada.
    Inclui retry com backoff exponencial para limites do tier gratuito.
    """
    if is_mock_mode():
        logger.info(f"[MOCK] Simulando auditoria para o produto '{title}' no marketplace {marketplace.upper()}...")
        time.sleep(1.5)  # Simula latência de API
        mock_res = get_mock_response(title, marketplace, images)
        return mock_res, 120, 250, 1.5

    client = get_gemini_client()
    
    # Prepara o payload de entrada
    product_data = f"""
    --- DADOS DO PRODUTO ---
    Marketplace de Destino: {marketplace.upper()}
    Título Atual: {title}
    Categoria: {category}
    Preço: R$ {price:.2f}
    Descrição Atual:
    {description}
    
    Imagens Cadastradas:
    {", ".join(images) if images else "Nenhuma imagem cadastrada"}
    """
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=GeminiAuditResponse,
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.2,
    )
    
    start_time = time.time()
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Enviando produto '{title}' para o Gemini (Tentativa {attempt + 1}/{max_retries})...")
            
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=product_data,
                config=config
            )
            
            latency = time.time() - start_time
            
            tokens_in = 0
            tokens_out = 0
            if response.usage_metadata:
                tokens_in = response.usage_metadata.prompt_token_count or 0
                tokens_out = response.usage_metadata.candidates_token_count or 0
            
            logger.info(f"Auditoria concluída com sucesso. Latência: {latency:.2f}s. Tokens: {tokens_in} in / {tokens_out} out.")
            
            parsed_result = GeminiAuditResponse.model_validate_json(response.text)
            return parsed_result, tokens_in, tokens_out, latency
            
        except Exception as e:
            is_rate_limit = False
            error_msg = str(e).lower()
            if "429" in error_msg or "resource_exhausted" in error_msg or "rate limit" in error_msg:
                is_rate_limit = True
                
            if is_rate_limit and attempt < max_retries - 1:
                delay = initial_delay * (backoff_factor ** attempt)
                logger.warning(f"Rate limit atingido. Retentando em {delay}s... Erro: {e}")
                time.sleep(delay)
            else:
                logger.error(f"Erro fatal ao auditar produto com Gemini: {e}")
                raise e

    raise RuntimeError("Falha ao se comunicar com o Gemini após múltiplas tentativas.")
