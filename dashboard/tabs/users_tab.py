import streamlit as st
import requests
from datetime import datetime


def render(api_url: str, headers: dict, current_user: dict) -> None:
    st.markdown("### 👥 Gestão de Usuários (Operadores da Organização)")
    st.markdown("""
    Cadastre operadores/analistas que acessarão o painel. O papel define a permissão:
    **Admin** pode excluir anúncios e gerenciar usuários; **Analista** pode criar, editar
    e responder clientes, mas **não pode excluir** anúncios.
    """)

    is_admin = current_user.get("role") == "admin"

    if not is_admin:
        st.warning("🔒 Apenas usuários com papel **admin** podem cadastrar ou remover operadores.")

    # ---- Formulário de cadastro ----
    with st.expander("➕ Cadastrar Novo Usuário", expanded=is_admin):
        with st.form("create_user_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                new_username = st.text_input("Usuário", placeholder="ex.: joao.analista")
                new_role = st.selectbox("Papel", ["analista", "admin"],
                                        help="analista: não exclui anúncios | admin: acesso total")
            with col2:
                new_password = st.text_input("Senha", type="password", placeholder="mínimo 6 caracteres")
                new_password2 = st.text_input("Confirmar senha", type="password")

            submitted = st.form_submit_button("💾 Cadastrar Usuário", disabled=not is_admin)
            if submitted:
                if not new_username.strip() or not new_password:
                    st.error("Usuário e senha são obrigatórios.")
                elif new_password != new_password2:
                    st.error("As senhas não coincidem.")
                elif len(new_password) < 6:
                    st.error("A senha deve ter no mínimo 6 caracteres.")
                else:
                    try:
                        r = requests.post(
                            f"{api_url}/auth/users",
                            json={"username": new_username.strip(), "password": new_password, "role": new_role},
                            headers=headers
                        )
                        if r.status_code == 201:
                            st.success(f"Usuário '{new_username}' cadastrado como {new_role}!")
                            st.rerun()
                        elif r.status_code == 409:
                            st.error("Já existe um usuário com este nome.")
                        elif r.status_code == 403:
                            st.error("Apenas admins podem cadastrar usuários.")
                        else:
                            st.error(f"Erro ao cadastrar: {r.text}")
                    except Exception as e:
                        st.error(f"Erro de conexão: {e}")

    st.markdown("---")
    st.markdown("#### 🗂️ Usuários da Organização")

    try:
        r_list = requests.get(f"{api_url}/auth/users", headers=headers)
        users = r_list.json() if r_list.status_code == 200 else []
    except Exception as e:
        st.error(f"Erro ao carregar usuários: {e}")
        users = []

    if not users:
        st.info("Nenhum usuário encontrado.")
        return

    for u in users:
        role_color = "#34d399" if u["role"] == "admin" else "#818cf8"
        is_self = u["id"] == current_user.get("id")
        created = ""
        if u.get("created_at"):
            try:
                created = datetime.fromisoformat(u["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y")
            except Exception:
                created = ""

        col_info, col_action = st.columns([4, 1])
        with col_info:
            st.markdown(f"""
            <div style="background:#1a1d27; border:1px solid #2d3142; border-radius:10px; padding:12px 16px; margin-bottom:8px;">
                <b style="font-size:1.05rem;">{u['username']}</b> {"<span style='color:#64748b;'>(você)</span>" if is_self else ""}
                <span style="float:right; background:{role_color}22; color:{role_color}; border:1px solid {role_color}55; padding:2px 10px; border-radius:9999px; font-size:0.75rem; text-transform:uppercase; font-weight:600;">{u['role']}</span>
                <br><small style="color:#64748b;">ID {u['id']} · desde {created}</small>
            </div>
            """, unsafe_allow_html=True)
        with col_action:
            if is_admin and not is_self:
                if st.button("🗑️", key=f"del_user_{u['id']}", help="Remover usuário"):
                    try:
                        rd = requests.delete(f"{api_url}/auth/users/{u['id']}", headers=headers)
                        if rd.status_code == 200:
                            st.success("Usuário removido.")
                            st.rerun()
                        else:
                            st.error(f"Erro: {rd.text}")
                    except Exception as e:
                        st.error(f"Erro: {e}")
