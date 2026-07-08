import time
import logging
from typing import Tuple, Optional
import httpx
from google import genai
from google.genai import types

from backend.app.config import GEMINI_API_KEY, GEMINI_MODEL, is_mock_mode, is_api_key_configured
from backend.app.schemas import GeminiAuditResponse, MissingAttribute, ImageIssue, GeminiQuestionDraftResponse
from backend.app.constants import Severity

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

Importante: Você receberá as imagens do produto como anexos multimodais na mesma ordem da lista "Imagens Cadastradas". Você DEVE avaliar cada imagem diretamente observando os pixels reais da foto (detectar fundo branco real RGB 255, 255, 255, marcas d'água, textos promocionais, resolução e enquadramento), em vez de tentar adivinhar pelo nome do arquivo ou pela URL. Caso uma imagem falhe no download ou não seja enviada nos anexos, ela não estará presente nos anexos multimodais (e as falhas de download serão tratadas antes). Avalie apenas as imagens reais fornecidas.
"""

def download_image(url: str, timeout: float = 3.0, max_size_bytes: int = 4 * 1024 * 1024) -> Tuple[bytes, str]:
    """Baixa uma imagem de forma segura, validando o tamanho e o tipo de conteúdo.

    Retorna uma tupla (conteúdo_bytes, content_type).
    Lança ValueError ou httpx.HTTPError se falhar.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL de imagem inválida: {url}")

    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise ValueError(f"Content-type inválido para imagem: {content_type}")

        # Verifica content-length se disponível antes de ler
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > max_size_bytes:
            raise ValueError(f"Imagem excede tamanho máximo permitido de {max_size_bytes} bytes")

        chunks = []
        bytes_downloaded = 0
        for chunk in response.iter_bytes():
            bytes_downloaded += len(chunk)
            if bytes_downloaded > max_size_bytes:
                raise ValueError(f"Imagem excede tamanho máximo permitido de {max_size_bytes} bytes")
            chunks.append(chunk)

        return b"".join(chunks), content_type


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
    
    # 1. Download de imagens do produto (máximo 5)
    downloaded_parts = []
    failed_downloads = []
    
    images_list = images or []
    for url in images_list[:5]:
        try:
            img_bytes, mime_type = download_image(url)
            part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
            downloaded_parts.append(part)
        except Exception as e:
            logger.warning(f"Não foi possível acessar a imagem {url}: {e}")
            failed_downloads.append(url)

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
    
    contents_list = [product_data]
    for part in downloaded_parts:
        contents_list.append(part)

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
                contents=contents_list,
                config=config
            )
            
            latency = time.time() - start_time
            
            tokens_in = 0
            tokens_out = 0
            if response.usage_metadata:
                tokens_in = response.usage_metadata.prompt_token_count or 0
                tokens_out = response.usage_metadata.candidates_token_count or 0
            
            has_multimodal = len(downloaded_parts) > 0
            logger.info(
                f"Auditoria concluída com sucesso. Latência: {latency:.2f}s. "
                f"Tokens: {tokens_in} in / {tokens_out} out. Input multimodal: {has_multimodal}"
            )
            
            parsed_result = GeminiAuditResponse.model_validate_json(response.text)
            
            # Adiciona os erros de download ao image_issues do resultado
            for failed_url in failed_downloads:
                parsed_result.image_issues.append(
                    ImageIssue(
                        image_url=failed_url,
                        issue="não foi possível acessar a imagem",
                        severity=Severity.MEDIUM
                    )
                )
                
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


SYSTEM_INSTRUCTION_QUESTION = """
Você é o Assistente especialista em responder perguntas pré-venda de e-commerce no Mercado Livre.
Sua função é propor um rascunho de resposta para as dúvidas dos compradores, mantendo um tom cordial, profissional e direto ao ponto.

### Diretrizes de Resposta:
1. NUNCA invente informações. Utilize estritamente os dados presentes no contexto do produto fornecido (ficha técnica, descrição, etc.). Se a informação necessária (ex: voltagem, cor, compatibilidade, prazo) não constar no contexto, NÃO afirme nada sobre ela e responda educadamente de forma cautelosa.
2. É terminantemente proibido incluir links externos, endereços de e-mail, telefones, links de redes sociais ou qualquer tipo de dado de contato pessoal/externo fora das ferramentas permitidas do Mercado Livre.
3. Se a pergunta for, na verdade, uma dúvida de pós-venda, uma reclamação de pedido já comprado (como atraso na entrega, defeito, código de rastreamento, devolução ou troca), ou qualquer questão que exija acesso a dados de compras passadas (que não temos acesso), você deve:
   - Sinalizar que a pergunta exige revisão humana (needs_human_review = true).
   - Indicar um motivo de revisão claro (ex: "Dúvida/Reclamação de pós-venda ou rastreio").
   - Gerar uma resposta cordial sugerindo ao comprador que envie uma mensagem privada pelos detalhes da compra dele para podermos ajudar de forma segura.

Você deve responder OBRIGATORIAMENTE no formato JSON especificado pelo schema fornecido.
Seus campos de retorno são:
- suggested_answer: A sugestão de resposta para a pergunta pré-venda.
- needs_human_review: Um booleano (true/false) indicando se a pergunta exige revisão humana.
- review_reason: Descrição textual do motivo pelo qual a revisão humana foi acionada (preenchido apenas se needs_human_review for true).
"""


def draft_question_answer(
    question_text: str,
    product_context: Optional[dict],
    marketplace: str = "mercado_livre",
    max_retries: int = 5,
    initial_delay: float = 2.0,
    backoff_factor: float = 2.0
) -> Tuple[str, bool, str, int, int, float]:
    """
    Chama a API do Gemini com structured output para propor uma resposta de pré-venda.
    Caso GEMINI_API_KEY esteja como 'mock', retorna uma resposta simulada.
    Inclui retry com backoff exponencial.
    """
    # Detecção rápida de palavras-chave para o modo Mock
    q_lower = question_text.lower()
    is_pos_venda = any(w in q_lower for w in ["rastreio", "atraso", "defeito", "comprei", "chegar", "compra", "devolver", "trocar", "garantia"])

    if is_mock_mode():
        logger.info(f"[MOCK] Simulando geração de resposta para a pergunta: '{question_text}'...")
        time.sleep(1.0)
        
        if is_pos_venda:
            return (
                "Olá! Por favor, envie uma mensagem privada diretamente nos detalhes da sua compra para que nossa equipe de suporte pós-venda possa ajudá-lo com segurança.",
                True,
                "Dúvida/Reclamação de pós-venda ou rastreio",
                50,
                80,
                1.0
            )
        
        if product_context:
            title = product_context.get("title", "Produto")
            suggested = f"Olá! Com base nos dados de '{title}', confirmamos as especificações técnicas descritas no anúncio. Ficamos à disposição!"
        else:
            suggested = "Olá! Agradecemos o contato. Por favor, confira as especificações detalhadas no corpo do anúncio para obter todas as informações sobre o produto. Qualquer dúvida estamos à disposição!"

        return suggested, False, "", 40, 60, 1.0

    client = get_gemini_client()
    
    # Prepara o payload de entrada
    context_str = "Nenhum contexto de produto disponível."
    if product_context:
        context_str = f"""
        Título do Produto: {product_context.get('title')}
        Descrição do Produto: {product_context.get('description')}
        Atributos: {product_context.get('attributes')}
        Preço: {product_context.get('price')}
        """
        
    prompt = f"""
    --- CONTEXTO DO PRODUTO ---
    {context_str}
    
    --- PERGUNTA DO COMPRADOR ---
    {question_text}
    """
    
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=GeminiQuestionDraftResponse,
        system_instruction=SYSTEM_INSTRUCTION_QUESTION,
        temperature=0.2,
    )
    
    start_time = time.time()
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Enviando pergunta pré-venda para o Gemini (Tentativa {attempt + 1}/{max_retries})...")
            
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config
            )
            
            latency = time.time() - start_time
            
            tokens_in = 0
            tokens_out = 0
            if response.usage_metadata:
                tokens_in = response.usage_metadata.prompt_token_count or 0
                tokens_out = response.usage_metadata.candidates_token_count or 0
            
            parsed_result = GeminiQuestionDraftResponse.model_validate_json(response.text)
            return (
                parsed_result.suggested_answer,
                parsed_result.needs_human_review,
                parsed_result.review_reason or "",
                tokens_in,
                tokens_out,
                latency
            )
            
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
                logger.error(f"Erro fatal ao gerar rascunho de resposta com Gemini: {e}")
                raise e

    raise RuntimeError("Falha ao se comunicar com o Gemini após múltiplas tentativas.")

