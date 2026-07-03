import streamlit as st
import requests
from datetime import datetime

def render(api_url: str, headers: dict) -> None:
    st.markdown("### 🔐 Cofre de Credenciais (Marketplaces & ERPs)")
    st.markdown("""
    Cadastre, gerencie e valide as chaves de API e tokens de conexão com segurança. 
    Todos os segredos brutos são criptografados em repouso no banco de dados e as exibições são mascaradas.
    """)

    # Valida cabeçalhos obrigatórios
    if not headers or "Authorization" not in headers or not headers["Authorization"].strip():
        st.error("Chave de administrador ausente ou incorreta. Preencha o campo 'Admin API Key' na barra lateral.")
        return

    # 1. Formulário de Criação de Credenciais
    with st.expander("➕ Cadastrar Nova Credencial"):
        with st.form("create_credential_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                provider = st.selectbox(
                    "Provedor (Marketplace / ERP):",
                    ["mercado_livre", "shopee", "bling", "amazon", "magalu"]
                )
                provider_type = st.selectbox(
                    "Tipo de Provedor:",
                    ["marketplace", "erp"]
                )
            with col2:
                label = st.text_input("Nome Amigável (ex.: ML Principal, Bling Prod):", placeholder="Minha Conexão")
                # Scopes autorizados (deve estar em sincronia com ALLOWED_CREDENTIAL_SCOPES do backend)
                scopes = st.multiselect(
                    "Escopos autorizados:",
                    ["read_products", "write_price", "write_listing", "read_orders"],
                    default=["read_products"]
                )

            st.markdown("**Segredos Brutos (Não serão salvos no histórico local do navegador)**")
            acc_token = st.text_input("Access Token:", type="password", placeholder="Insira o Token Principal de Acesso")
            ref_token = st.text_input("Refresh Token (Opcional):", type="password", placeholder="Insira o Refresh Token se houver")

            submitted = st.form_submit_button("💾 Salvar Credencial")

            if submitted:
                if not label.strip() or not acc_token.strip():
                    st.error("Nome Amigável e Access Token são obrigatórios.")
                else:
                    secret_payload = {"access_token": acc_token.strip()}
                    if ref_token.strip():
                        secret_payload["refresh_token"] = ref_token.strip()

                    payload = {
                        "provider": provider,
                        "provider_type": provider_type,
                        "label": label.strip(),
                        "secret_payload": secret_payload,
                        "scopes": scopes
                    }

                    with st.spinner("Criptografando e salvando credencial..."):
                        try:
                            r = requests.post(f"{api_url}/credentials", json=payload, headers=headers)
                            if r.status_code == 200:
                                st.success("Credencial salva com sucesso!")
                                # Descarta da memória local imediatamente
                                acc_token = ""
                                ref_token = ""
                                st.rerun()
                            elif r.status_code in [401, 503]:
                                st.error("🔑 Chave de administrador ausente ou incorreta. Preencha o campo 'Admin API Key' na barra lateral.")
                            else:
                                st.error(f"Erro ao salvar credencial: {r.text}")
                        except Exception as e:
                            st.error(f"Erro de conexão com o backend: {e}")

    st.markdown("---")

    # 2. Listagem de Credenciais Ativas
    st.markdown("#### 🔑 Credenciais Cadastradas")
    try:
        r_list = requests.get(f"{api_url}/credentials", headers=headers)
        if r_list.status_code == 200:
            creds = r_list.json()
        elif r_list.status_code in [401, 503]:
            st.error("🔑 Chave de administrador ausente ou incorreta. Preencha o campo 'Admin API Key' na barra lateral.")
            return
        else:
            st.error(f"Erro ao obter credenciais: {r_list.text}")
            creds = []
    except Exception as e:
        st.error(f"Erro ao conectar com o backend: {e}")
        creds = []

    if not creds:
        st.info("Nenhuma credencial cadastrada. Use o formulário acima para adicionar uma.")
    else:
        for c in creds:
            # Classes de status para exibição estilizada via HTML
            status_map = {
                "valid": "status-valid",
                "expired": "status-expired",
                "error": "status-error",
                "untested": "status-untested"
            }
            status_cls = status_map.get(c["status"], "status-untested")
            
            # Formatação de datas
            last_check = "Nunca"
            if c.get("last_checked_at"):
                last_check = datetime.fromisoformat(c["last_checked_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")

            # Renderiza card premium da credencial
            card_html = f"""
            <div class="card">
                <span class="mkt-tag mkt-default" style="text-transform: uppercase;">{c['provider_type']}</span>
                <span class="mkt-tag mkt-{c['provider']}">{c['provider']}</span>
                <span class="status-badge {status_cls}" style="float: right;">{c['status']}</span>
                <h3 style="margin: 10px 0 5px 0; font-size: 1.25rem;">{c['label']}</h3>
                <p style="margin: 0; color: #94a3b8; font-size: 0.9rem;">
                    <b>Visualização:</b> <code>{c['masked_preview']}</code> | 
                    <b>Verificado em:</b> {last_check}
                </p>
                <div style="margin-top: 8px;">
                    <span style="font-size: 0.8rem; color: #64748b; font-weight: 600;">ESCOPOS:</span>
                    {" ".join([f'<span style="background-color:#1e1b4b; color:#818cf8; padding:2px 8px; border-radius:4px; font-size:0.75rem; margin-right:4px;">{s}</span>' for s in c['scopes']])}
                </div>
                {"<div style='margin-top:8px; color:#fda4af; font-size:0.85rem;'>⚠️ <b>Detalhe:</b> " + c['status_detail'] + "</div>" if c.get('status_detail') else ""}
            </div>
            """
            st.markdown(card_html, unsafe_allow_html=True)

            # Ações da credencial (Testar, Rotacionar, Excluir)
            col_t, col_r, col_d = st.columns([2, 3, 3])
            
            with col_t:
                # Testar Conexão
                if st.button("🔌 Testar Conexão", key=f"btn_test_{c['id']}"):
                    with st.spinner("Testando conectividade..."):
                        try:
                            r_test = requests.post(f"{api_url}/credentials/{c['id']}/test", headers=headers)
                            if r_test.status_code == 200:
                                res_test = r_test.json()
                                if res_test["status"] == "valid":
                                    st.success("Conexão válida e ativa!")
                                else:
                                    st.error(f"Conexão inválida: {res_test.get('status_detail')}")
                                st.rerun()
                            elif r_test.status_code in [401, 503]:
                                st.error("Chave de admin inválida.")
                            else:
                                st.error(f"Erro ao testar: {r_test.text}")
                        except Exception as e:
                            st.error(f"Erro: {e}")

            with col_r:
                # Rotacionar Token
                with st.popover("🔄 Rotacionar Token"):
                    with st.form(f"rotate_form_{c['id']}", clear_on_submit=True):
                        st.markdown("**Rotacionar segredos**")
                        new_acc = st.text_input("Novo Access Token:", type="password", key=f"new_acc_{c['id']}")
                        new_ref = st.text_input("Novo Refresh Token (Opcional):", type="password", key=f"new_ref_{c['id']}")
                        submit_rot = st.form_submit_button("Confirmar Rotação")
                        
                        if submit_rot:
                            if not new_acc.strip():
                                st.error("Access Token obrigatório.")
                            else:
                                new_payload = {"access_token": new_acc.strip()}
                                if new_ref.strip():
                                    new_payload["refresh_token"] = new_ref.strip()
                                
                                try:
                                    r_rot = requests.patch(
                                        f"{api_url}/credentials/{c['id']}",
                                        json={"secret_payload": new_payload},
                                        headers=headers
                                    )
                                    if r_rot.status_code == 200:
                                        st.success("Token rotacionado com sucesso!")
                                        # Limpa da memória local
                                        new_acc = ""
                                        new_ref = ""
                                        st.rerun()
                                    else:
                                        st.error(f"Erro ao rotacionar: {r_rot.text}")
                                except Exception as e:
                                    st.error(f"Erro: {e}")

            with col_d:
                # Excluir credencial
                confirm_box = st.checkbox("Confirmar exclusão", key=f"chk_del_{c['id']}")
                btn_del = st.button("🗑️ Excluir", key=f"btn_del_{c['id']}", disabled=not confirm_box)
                if btn_del:
                    try:
                        r_del = requests.delete(f"{api_url}/credentials/{c['id']}", headers=headers)
                        if r_del.status_code == 200:
                            st.success("Credencial deletada.")
                            st.rerun()
                        else:
                            st.error(f"Erro ao excluir: {r_del.text}")
                    except Exception as e:
                        st.error(f"Erro de conexão: {e}")
                        
            st.markdown("---")
