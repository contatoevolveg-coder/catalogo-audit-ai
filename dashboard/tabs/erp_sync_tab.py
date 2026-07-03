import streamlit as st
import requests
import pandas as pd
from datetime import datetime

def render(api_url: str, headers: dict) -> None:
    st.markdown("### 🔗 Sincronização de Estoque com Bling ERP")
    st.markdown("""
    Gerencie o vínculo de SKUs e atualize o saldo de estoque físico do Bling ERP (API v3) 
    para o seu catálogo local de forma síncrona ou em lote. 
    Esta operação é **estritamente de leitura** (read-only) em relação ao Bling.
    """)

    # Valida cabeçalhos obrigatórios
    if not headers or "Authorization" not in headers or not headers["Authorization"].strip():
        st.error("Chave de administrador ausente ou incorreta. Preencha o campo 'Admin API Key' na barra lateral.")
        return

    # 1. Busca produtos
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
        st.info("Nenhum produto cadastrado no catálogo.")
        return

    # Selectbox de produto
    prod_options = {f"{p['title']} (ID: {p['id']})": p for p in products}
    selected_prod_label = st.selectbox("Selecione o Produto:", list(prod_options.keys()))
    selected_product = prod_options[selected_prod_label]
    product_id = selected_product["id"]

    # 2. Vincular SKU do Bling ERP
    st.markdown("#### 🔗 Vincular SKU ao Produto Local")
    current_sku = selected_product.get("erp_sku") or ""
    
    col_sku, col_btn = st.columns([3, 1])
    with col_sku:
        erp_sku = st.text_input(
            "SKU do Produto no Bling ERP:",
            value=current_sku,
            placeholder="Ex.: FONE-BLUE-123",
            help="Insira o código/SKU cadastrado no ERP do Bling."
        )
    with col_btn:
        st.write("")
        st.write("")
        btn_link = st.button("🔗 Salvar Vínculo", use_container_width=True)

    if btn_link:
        if not erp_sku.strip():
            st.error("O SKU não pode ser vazio.")
        else:
            with st.spinner("Salvando vínculo de SKU..."):
                try:
                    r_link = requests.patch(
                        f"{api_url}/erp-integrations/bling/products/{product_id}/erp-link",
                        json={"erp_sku": erp_sku.strip()},
                        headers=headers
                    )
                    if r_link.status_code == 200:
                        st.success("SKU vinculado com sucesso!")
                        st.rerun()
                    else:
                        st.error(f"Erro ao salvar vínculo: {r_link.text}")
                except Exception as e:
                    st.error(f"Erro de conexão: {e}")

    st.markdown("---")

    # 3. Busca credenciais do Bling
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

    # Filtra apenas provedor bling
    bling_creds = [c for c in all_creds if c["provider"] == "bling"]

    if not bling_creds:
        st.warning("⚠️ Nenhuma credencial do Bling ERP cadastrada no cofre. Vá à aba de Credenciais primeiro.")
        return

    cred_options = {}
    for c in bling_creds:
        status_label = "✅ VÁLIDA" if c["status"] == "valid" else f"❌ {c['status'].upper()}"
        label = f"{c['label']} ({c['masked_preview']}) - Status: {status_label}"
        cred_options[label] = c

    selected_cred_label = st.selectbox("Selecione a credencial do Bling:", list(cred_options.keys()))
    selected_cred = cred_options[selected_cred_label]
    cred_id = selected_cred["id"]

    # 4. Sincronização Unitária
    st.markdown("#### 🔄 Sincronização de Estoque Unitária")
    st.write(f"**Produto Selecionado:** *{selected_product['title']}*")
    st.write(f"**SKU Vinculado:** `{current_sku or '(Sem SKU Vinculado)'}`")
    st.write(f"**Estoque Local Atual:** {selected_product.get('available_quantity') or 0}")

    btn_sync = st.button("🔄 Sincronizar Estoque Local", type="primary", disabled=not current_sku)
    if not current_sku:
        st.info("Insira e salve um SKU acima para habilitar a sincronização.")

    if btn_sync and current_sku:
        with st.spinner("Buscando estoque no Bling ERP (leitura)..."):
            try:
                r_sync = requests.post(
                    f"{api_url}/erp-integrations/bling/products/{product_id}/sync-stock",
                    json={"credential_id": cred_id},
                    headers=headers
                )
                
                if r_sync.status_code == 200:
                    res = r_sync.json()
                    st.success(
                        f"Estoque atualizado! Saldo anterior: **{res['previous_quantity']}** ➡️ "
                        f"Novo saldo lido do Bling: **{res['new_quantity']}**"
                    )
                    st.rerun()
                elif r_sync.status_code == 404:
                    st.error(f"❌ SKU não encontrado no Bling: {r_sync.json().get('detail')}")
                elif r_sync.status_code == 502:
                    st.error(f"❌ Erro de validação/comunicação retornado pelo Bling: {r_sync.json().get('detail')}")
                elif r_sync.status_code == 400:
                    st.warning(f"⚠️ Pré-condição não atendida: {r_sync.json().get('detail')}")
                else:
                    st.error(f"Erro inesperado: {r_sync.text}")
            except Exception as e:
                st.error(f"Erro de conexão com o backend: {e}")

    st.markdown("---")

    # 5. Sincronização em Lote (Bulk Sync)
    st.markdown("#### 🔄 Sincronização em Lote")
    st.info("Sincroniza simultaneamente o estoque de múltiplos produtos locais que possuem SKUs vinculados.")
    
    max_sync = st.number_input(
        "Limite máximo de produtos para sincronizar neste lote (Cap de segurança):",
        min_value=1,
        max_value=100,
        value=20
    )

    btn_sync_all = st.button("⚡ Executar Sincronização em Lote")
    if btn_sync_all:
        with st.spinner("Sincronizando produtos em lote (resiliente)..."):
            try:
                r_bulk = requests.post(
                    f"{api_url}/erp-integrations/bling/sync-all",
                    json={"credential_id": cred_id, "max_sync": int(max_sync)},
                    headers=headers
                )
                
                if r_bulk.status_code == 200:
                    res_bulk = r_bulk.json()
                    st.success("Sincronização em lote finalizada!")
                    
                    st.write(f"- **Produtos Atualizados com Sucesso:** {res_bulk.get('synced')}")
                    st.write(f"- **Produtos Não Encontrados no Bling:** {res_bulk.get('not_found')}")
                    
                    errs = res_bulk.get("errors", [])
                    if errs:
                        st.error("Ocorreram falhas ao processar alguns produtos do lote:")
                        for e in errs:
                            st.write(f"- Produto ID {e.get('product_id')}: {e.get('error')}")
                    st.rerun()
                else:
                    st.error(f"Erro ao executar sincronização em lote: {r_bulk.text}")
            except Exception as e:
                st.error(f"Erro de conexão: {e}")

    st.markdown("---")

    # 6. Histórico de Sincronização do Produto
    st.markdown("#### 📜 Histórico de Sincronização deste Produto")
    try:
        r_hist = requests.get(
            f"{api_url}/erp-integrations/bling/products/{product_id}/sync-history",
            headers=headers
        )
        if r_hist.status_code == 200:
            history = r_hist.json()
        else:
            st.error(f"Erro ao carregar histórico: {r_hist.text}")
            history = []
    except Exception as e:
        st.error(f"Erro de rede ao buscar histórico: {e}")
        history = []

    if not history:
        st.info("Nenhuma tentativa de sincronização registrada para este produto.")
    else:
        hist_data = []
        for h in history:
            hist_data.append({
                "ID Sync": h["id"],
                "SKU": h["erp_sku"],
                "Tipo": h["sync_type"],
                "Status": h["status"],
                "Estoque Antigo": h.get("previous_quantity") if h.get("previous_quantity") is not None else "N/A",
                "Novo Estoque": h.get("new_quantity") if h.get("new_quantity") is not None else "N/A",
                "Detalhe de Erro": h.get("error_detail") or "Nenhum",
                "Data Sincronização": datetime.fromisoformat(h["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")
            })
            
        df_hist = pd.DataFrame(hist_data)
        st.dataframe(df_hist, use_container_width=True)
