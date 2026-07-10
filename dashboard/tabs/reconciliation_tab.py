import streamlit as st
import pandas as pd
import requests

def render(api_url: str, token: str):
    st.header("🔍 Reconciliação de Estoque")
    st.markdown("""
    Auditoria contínua entre o **Saldo Real (Bling)** e a **Quantidade no Anúncio (Marketplaces)**.
    A anomalia **'vendendo_fantasma'** (Bling=0, Anúncio>0) é corrigida automaticamente.
    """)

    headers = {"Authorization": f"Bearer {token}"}

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("▶️ Executar Auditoria Agora", use_container_width=True):
            with st.spinner("Reconciliando..."):
                try:
                    resp = requests.post(f"{api_url}/stock-reconciliation/run", headers=headers)
                    if resp.status_code == 200:
                        st.success("Reconciliação finalizada!")
                    else:
                        st.error(f"Erro: {resp.text}")
                except Exception as e:
                    st.error(f"Falha de rede: {str(e)}")

    with st.spinner("Carregando últimas anomalias..."):
        try:
            resp = requests.get(f"{api_url}/stock-reconciliation/?limit=100", headers=headers)
            if resp.status_code == 200:
                logs = resp.json()
                if not logs:
                    st.info("Nenhuma divergência registrada recentemente. O estoque está saudável!")
                    return

                df = pd.DataFrame(logs)

                # Traduções e cores
                category_map = {
                    "vendendo_fantasma": "👻 Vendendo Fantasma",
                    "estoque_preso": "🔒 Estoque Preso",
                    "divergencia_quantidade": "⚠️ Divergência de Qtde",
                    "ok": "✅ OK"
                }

                df["category_label"] = df["category"].map(category_map)

                # Exibir métricas
                total_fantasmas = len(df[df["category"] == "vendendo_fantasma"])
                total_preso = len(df[df["category"] == "estoque_preso"])
                total_divergente = len(df[df["category"] == "divergencia_quantidade"])

                c1, c2, c3 = st.columns(3)
                c1.metric("Fantasmas Detectados", total_fantasmas)
                c2.metric("Estoque Preso", total_preso)
                c3.metric("Divergência Qtde", total_divergente)

                st.subheader("📋 Detalhes das Discrepâncias")

                df_view = df[["product_title", "erp_sku", "marketplace", "bling_quantity", "marketplace_quantity", "category_label", "checked_at"]]
                df_view = df_view.rename(columns={
                    "product_title": "Produto",
                    "erp_sku": "SKU",
                    "marketplace": "Marketplace",
                    "bling_quantity": "Saldo Real",
                    "marketplace_quantity": "Anunciado",
                    "category_label": "Classificação",
                    "checked_at": "Data/Hora"
                })

                st.dataframe(
                    df_view,
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.error("Falha ao buscar logs de reconciliação.")
        except Exception as e:
            st.error(f"Falha de conexão: {str(e)}")
