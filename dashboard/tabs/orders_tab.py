import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import plotly.express as px

def render(api_url: str, headers: dict) -> None:
    st.markdown("### 📦 Gestão de Pedidos")
    st.markdown("Centralize os pedidos dos marketplaces e acompanhe o faturamento.")

    # 1. Filtros
    col1, col2, col3 = st.columns(3)
    with col1:
        marketplace = st.selectbox("Marketplace:", ["Todos", "mercado_livre"])
    with col2:
        status_filter = st.selectbox("Status:", ["Todos", "paid", "confirmed", "cancelled"])
    with col3:
        st.write("")
        st.write("")
        if st.button("🔄 Sincronizar Agora", type="primary"):
            with st.spinner("Sincronizando pedidos..."):
                try:
                    res = requests.post(f"{api_url}/orders/sync", headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        st.success(f"Sucesso! {data.get('synced', 0)} pedidos sincronizados. {data.get('stock_deductions', 0)} abatimentos locais realizados.")
                    else:
                        st.error(f"Erro: {res.text}")
                except Exception as e:
                    st.error(f"Erro de rede: {e}")

    # 2. Busca de dados
    params = {}
    if marketplace != "Todos":
        params["marketplace"] = marketplace
    if status_filter != "Todos":
        params["status"] = status_filter

    try:
        r = requests.get(f"{api_url}/orders", headers=headers, params=params)
        if r.status_code == 200:
            orders = r.json()
        else:
            st.error("Erro ao buscar pedidos.")
            orders = []
    except Exception as e:
        st.error(f"Erro de rede: {e}")
        orders = []

    if not orders:
        st.info("Nenhum pedido encontrado com os filtros atuais.")
        return

    # 3. Métricas
    total_amount = sum(o["total_amount"] for o in orders if o["status"] in ["paid", "confirmed"])
    total_orders = len([o for o in orders if o["status"] in ["paid", "confirmed"]])
    ticket_medio = total_amount / total_orders if total_orders > 0 else 0.0

    st.markdown("#### 📊 Métricas (Pedidos Pagos/Confirmados)")
    m1, m2, m3 = st.columns(3)
    m1.metric("Faturamento", f"R$ {total_amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    m2.metric("Total de Pedidos", total_orders)
    m3.metric("Ticket Médio", f"R$ {ticket_medio:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    # 4. Gráfico
    st.markdown("#### Gráfico de Status")
    status_counts = pd.Series([o["status"] for o in orders]).value_counts().reset_index()
    status_counts.columns = ["Status", "Quantidade"]
    fig = px.pie(status_counts, names="Status", values="Quantidade", title="Pedidos por Status", hole=0.3)
    st.plotly_chart(fig, use_container_width=True)

    # 5. Tabela de Pedidos
    st.markdown("#### Lista de Pedidos")
    table_data = []
    for o in orders:
        items_str = ", ".join([f"{item['quantity']}x {item['title']}" for item in o.get("items", [])])
        dt = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
        table_data.append({
            "ID Externo": o["external_order_id"],
            "Comprador": o["buyer_nickname"] or "N/A",
            "Total": f"R$ {o['total_amount']:.2f}",
            "Status": o["status"],
            "Frete": o["shipping_status"] or "N/A",
            "Estoque Abatido?": "Sim" if o["stock_deducted"] else "Não",
            "Data": dt.strftime("%d/%m/%Y %H:%M"),
            "Itens": items_str
        })
    
    st.dataframe(pd.DataFrame(table_data), use_container_width=True)
