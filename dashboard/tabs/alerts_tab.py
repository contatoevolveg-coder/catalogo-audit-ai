import streamlit as st
import requests
from datetime import datetime

def render(api_url: str, headers: dict) -> None:
    st.markdown("### 🔔 Central de Alertas")
    st.markdown("""
    Visualize e gerencie os alertas críticos gerados pelo sistema sobre o estoque, expiração de credenciais, notas baixas de SEO e tempo de espera de perguntas.
    """)

    if not headers or "Authorization" not in headers or not headers["Authorization"].strip():
        st.error("Chave de administrador ausente ou incorreta.")
        return

    # Botão para forçar recálculo/verificação de alertas
    col_trigger, _ = st.columns([1, 3])
    with col_trigger:
        if st.button("🔄 Executar Verificação de Alertas", use_container_width=True):
            with st.spinner("Verificando regras e gerando novos alertas..."):
                try:
                    r = requests.post(f"{api_url}/alerts/trigger", headers=headers)
                    if r.status_code == 200:
                        st.success(r.json().get("message", "Sucesso!"))
                        st.rerun()
                    else:
                        st.error(f"Erro ao disparar: {r.text}")
                except Exception as e:
                    st.error(f"Erro de conexão: {e}")

    # Seleção de visualização
    view_type = st.radio("Filtrar por status do alerta:", ["Não lidos", "Lidos"], horizontal=True)
    is_read_filter = "false" if view_type == "Não lidos" else "true"

    try:
        r = requests.get(f"{api_url}/alerts?is_read={is_read_filter}", headers=headers)
        if r.status_code == 200:
            alerts = r.json()
            if not alerts:
                st.info(f"Nenhum alerta {view_type.lower()} encontrado.")
            else:
                for alert in alerts:
                    severity = alert.get("severity", "LOW")
                    
                    if severity == "HIGH":
                        card_border = "1px solid #ef4444"
                        badge_color = "#ef4444"
                    elif severity == "MEDIUM":
                        card_border = "1px solid #f59e0b"
                        badge_color = "#f59e0b"
                    else:
                        card_border = "1px solid #38bdf8"
                        badge_color = "#38bdf8"
                        
                    created_at_str = alert.get("created_at")
                    try:
                        created_at_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        date_formatted = created_at_dt.strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        date_formatted = created_at_str

                    st.markdown(f"""
                    <div style="background-color: #1a1d27; border-radius: 8px; border: {card_border}; padding: 16px; margin-bottom: 12px; position: relative;">
                        <span style="background-color: {badge_color}; color: #fff; font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; font-weight: bold;">{severity}</span>
                        <span style="font-size: 0.8rem; color: #64748b; float: right;">{date_formatted}</span>
                        <h4 style="margin: 10px 0 5px 0; color: #f1f5f9;">{alert.get('message')}</h4>
                        <small style="color: #64748b;">Tipo: {alert.get('type')}</small>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if view_type == "Não lidos":
                        if st.button(f"✅ Marcar como Lido", key=f"read_{alert.get('id')}"):
                            try:
                                r_read = requests.post(f"{api_url}/alerts/{alert.get('id')}/read", headers=headers)
                                if r_read.status_code == 200:
                                    st.toast("Alerta marcado como lido!")
                                    st.rerun()
                                else:
                                    st.error(f"Erro ao marcar como lido: {r_read.text}")
                            except Exception as e:
                                st.error(f"Erro ao conectar com o backend: {e}")
        else:
            st.error(f"Erro ao buscar alertas: {r.text}")
    except Exception as e:
        st.error(f"Erro de conexão: {e}")
