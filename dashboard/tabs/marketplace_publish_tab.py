import streamlit as st
import requests
import pandas as pd
from datetime import datetime

def render(api_url: str, headers: dict) -> None:
    st.markdown("### 🛒 Publicação de Anúncios no Marketplace")
    st.markdown("""
    Publique de forma síncrona anúncios aprovados diretamente no Marketplace (Mercado Livre ou Shopee) utilizando 
    as credenciais seguras cadastradas no cofre. Esta operação é protegida por um duplo portão humano.
    """)

    # Valida cabeçalhos obrigatórios
    if not headers or "Authorization" not in headers or not headers["Authorization"].strip():
        st.error("Chave de administrador ausente ou incorreta. Preencha o campo 'Admin API Key' na barra lateral.")
        return

    # 1. Busca produtos para seleção
    try:
        r_prods = requests.get(f"{api_url}/products", headers=headers)
        if r_prods.status_code == 200:
            products = r_prods.json()
        else:
            st.error(f"Erro ao obter produtos: {r_prods.text}")
            products = []
    except Exception as e:
        st.error(f"Erro ao conectar com o backend: {e}")
        return

    if not products:
        st.info("Nenhum produto cadastrado no catálogo. Importe ou cadastre anúncios primeiro.")
        return

    # Selectbox de produto
    prod_options = {f"{p['title']} (ID: {p['id']})": p for p in products}
    selected_prod_label = st.selectbox("Selecione o Produto para publicar:", list(prod_options.keys()))
    selected_product = prod_options[selected_prod_label]
    product_id = selected_product["id"]

    # Seleção de Marketplace
    st.markdown("#### 🌍 Escolha o Marketplace")
    marketplace_choice = st.radio(
        "Destino da Publicação:",
        options=["mercado_livre", "shopee"],
        format_func=lambda x: "Mercado Livre" if x == "mercado_livre" else "Shopee",
        horizontal=True
    )

    # 2. Sugestão e Seleção de Categoria
    st.markdown(f"#### 🏷️ Categoria ({'Mercado Livre' if marketplace_choice == 'mercado_livre' else 'Shopee'})")
    title_for_prediction = st.text_input("Título base para predição de categoria:", value=selected_product["title"])
    
    category_id = ""
    suggested_categories = []

    if st.button("🔍 Sugerir Categorias") and marketplace_choice == "mercado_livre":
        with st.spinner("Buscando sugestões de categoria no Mercado Livre..."):
            try:
                # O endpoint category-suggestions é protegido pela admin key
                pred_url = f"{api_url}/marketplace-integrations/mercado_livre/category-suggestions?title={title_for_prediction}"
                r_pred = requests.get(pred_url, headers=headers)
                if r_pred.status_code == 200:
                    suggested_categories = r_pred.json()
                    if not suggested_categories:
                        st.warning("Nenhuma sugestão encontrada para o título informado.")
                else:
                    st.error(f"Erro ao sugerir categoria: {r_pred.text}")
            except Exception as e:
                st.error(f"Erro de rede: {e}")
    elif marketplace_choice == "shopee":
        st.info("Predição de categoria automática não disponível para a Shopee. Insira o ID manualmente.")
                
    # Mostra selectbox com as sugestões se existirem
    if suggested_categories:
        cat_options = {f"{c['category_name']} ({c['category_id']})": c["category_id"] for c in suggested_categories}
        selected_cat_label = st.selectbox("Categorias Recomendadas:", list(cat_options.keys()))
        category_id = cat_options[selected_cat_label]

    # Campo de texto para preenchimento manual ou override do ID da categoria
    category_id = st.text_input(
        "ID da Categoria (Numérico para Shopee, MLBxxxxx para Mercado Livre):",
        value=category_id,
        help="Insira o código de categoria oficial do Marketplace. Ex ML.: MLB1051. Ex Shopee: 100013"
    )

    # 3. Seleção de Credencial ML / Shopee
    st.markdown(f"#### 🔑 Selecionar Credencial - {'Mercado Livre' if marketplace_choice == 'mercado_livre' else 'Shopee'}")
    try:
        r_creds = requests.get(f"{api_url}/credentials", headers=headers)
        if r_creds.status_code == 200:
            all_creds = r_creds.json()
        else:
            st.error(f"Erro ao buscar credenciais: {r_creds.text}")
            all_creds = []
    except Exception as e:
        st.error(f"Erro ao conectar com o backend: {e}")
        all_creds = []

    # Filtra pelo marketplace escolhido
    filtered_creds = [c for c in all_creds if c["provider"] == marketplace_choice]

    if not filtered_creds:
        st.warning(f"⚠️ Nenhuma credencial do {'Mercado Livre' if marketplace_choice == 'mercado_livre' else 'Shopee'} cadastrada no cofre. Vá à aba de Credenciais primeiro.")
        return

    # Monta lista de opções marcando credenciais não válidas
    cred_options = {}
    for c in filtered_creds:
        status_label = "✅ VÁLIDA" if c["status"] == "valid" else f"❌ {c['status'].upper()}"
        label = f"{c['label']} ({c['masked_preview']}) - Status: {status_label}"
        cred_options[label] = c

    selected_cred_label = st.selectbox("Selecione a credencial para autenticar:", list(cred_options.keys()))
    selected_cred = cred_options[selected_cred_label]
    
    # Se a credencial não estiver válida, emite um aviso mas permite tentar
    if selected_cred["status"] != "valid":
        st.warning("⚠️ Esta credencial não está com o status 'valid'. Recomenda-se rodar o Teste de Conexão na aba de Credenciais.")

    # 4. Envio de Publicação (Portão 2)
    st.markdown("#### 🚀 Publicar Anúncio")
    
    # Validações visuais antes de enviar
    st.write(f"**Título a ser enviado:** *{selected_product['title']}*")
    st.write(f"**Preço:** R$ {selected_product['price']:.2f}")

    if st.button(f"Confirmo e Desejo Publicar no {'Mercado Livre' if marketplace_choice == 'mercado_livre' else 'Shopee'}", type="primary"):
        if not category_id.strip():
            st.error("O ID da Categoria é obrigatório.")
        else:
            payload = {
                "credential_id": selected_cred["id"],
                "category_id": category_id.strip()
            }
            with st.spinner(f"Enviando dados do anúncio ao {'Mercado Livre' if marketplace_choice == 'mercado_livre' else 'Shopee'} de forma síncrona..."):
                try:
                    r_pub = requests.post(
                        f"{api_url}/marketplace-integrations/products/{product_id}/publish",
                        json=payload,
                        headers=headers
                    )
                    
                    if r_pub.status_code == 200:
                        res_pub = r_pub.json()
                        st.success(f"🎉 Anúncio publicado com sucesso! Item ID no Marketplace: **{res_pub['marketplace_item_id']}**")
                        st.rerun()
                    elif r_pub.status_code == 502:
                        # Falha de validação do Marketplace
                        error_detail = r_pub.json().get("detail", r_pub.text)
                        st.error(f"❌ Falha de validação da API do Marketplace: {error_detail}")
                    elif r_pub.status_code == 400:
                        # Erros de precondições do nosso backend (ex: sugestão não aprovada)
                        error_detail = r_pub.json().get("detail", r_pub.text)
                        st.warning(f"⚠️ Pré-condição não atendida: {error_detail}")
                    else:
                        st.error(f"Erro inesperado na chamada: {r_pub.text}")
                except Exception as e:
                    st.error(f"Erro de conexão com o backend: {e}")

    st.markdown("---")

    # 5. Histórico de Publicações
    st.markdown("#### 📜 Histórico de Publicações deste Produto")
    try:
        r_hist = requests.get(
            f"{api_url}/marketplace-integrations/products/{product_id}/publications",
            headers=headers
        )
        if r_hist.status_code == 200:
            history = r_hist.json()
        else:
            st.error(f"Erro ao buscar histórico: {r_hist.text}")
            history = []
    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")
        history = []

    if not history:
        st.info("Nenhuma tentativa de publicação registrada para este produto.")
    else:
        hist_data = []
        for h in history:
            hist_data.append({
                "ID Pub": h["id"],
                "ID Credencial": h["credential_id"],
                "Categoria": h["category_id"],
                "Status": h["status"],
                "ID Item ML/Shopee": h.get("marketplace_item_id") or "N/A",
                "Detalhe de Erro": h.get("error_detail") or "Nenhum",
                "Data Tentativa": datetime.fromisoformat(h["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")
            })
        
        df_hist = pd.DataFrame(hist_data)
        st.dataframe(df_hist, use_container_width=True)
