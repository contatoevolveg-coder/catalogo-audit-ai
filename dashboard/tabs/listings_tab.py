import streamlit as st
import requests


def _get_ml_credentials(api_url: str, headers: dict):
    """Retorna as credenciais do Mercado Livre válidas no cofre."""
    try:
        r = requests.get(f"{api_url}/credentials", headers=headers)
        if r.status_code == 200:
            return [c for c in r.json() if c["provider"] == "mercado_livre"]
    except Exception:
        pass
    return []


def render(api_url: str, headers: dict, current_user: dict) -> None:
    st.markdown("### 🛒 Gestão de Anúncios do Mercado Livre")
    st.markdown("""
    Conecte a conta, **importe todos os anúncios** já publicados, edite (título, preço, estoque,
    descrição) com replicação direta no Mercado Livre e exclua anúncios (restrito a admins).
    """)

    is_admin = current_user.get("role") == "admin"
    ml_creds = _get_ml_credentials(api_url, headers)

    # ---------------------------------------------------------
    # 1. Conectar conta ML (OAuth) — caso não haja credencial
    # ---------------------------------------------------------
    with st.expander("🔗 Conectar conta do Mercado Livre (OAuth)", expanded=not ml_creds):
        st.caption("Autorize o app a acessar sua conta. Após autorizar, a credencial aparece automaticamente no cofre.")
        if st.button("🔓 Gerar link de autorização do Mercado Livre"):
            try:
                r = requests.get(f"{api_url}/oauth/mercado_livre/authorize", headers=headers)
                if r.status_code == 200:
                    auth_url = r.json()["authorization_url"]
                    st.success("Link gerado! Clique abaixo, autorize e volte a esta aba.")
                    st.markdown(f"### 👉 [Autorizar no Mercado Livre]({auth_url})")
                    st.info("Depois de autorizar, aguarde a mensagem de sucesso na nova aba e recarregue esta página.")
                else:
                    st.error(f"Erro ao gerar link: {r.text}")
            except Exception as e:
                st.error(f"Erro de conexão: {e}")

    if not ml_creds:
        st.warning("Nenhuma credencial do Mercado Livre conectada ainda. Use o botão acima para conectar.")
        return

    # Seletor de credencial (quando houver mais de uma conta)
    cred_options = {f"{c['label']} (#{c['id']}, {c['status']})": c["id"] for c in ml_creds}
    sel_label = st.selectbox("Conta do Mercado Livre:", list(cred_options.keys()))
    credential_id = cred_options[sel_label]

    # ---------------------------------------------------------
    # 2. Importar anúncios existentes
    # ---------------------------------------------------------
    col_imp1, col_imp2 = st.columns([1, 3])
    with col_imp1:
        max_items = st.number_input("Máx. a importar", min_value=1, max_value=500, value=100, step=50)
    with col_imp2:
        st.write("")
        st.write("")
        if st.button("📥 Importar / Atualizar anúncios do ML", type="primary"):
            with st.spinner("Buscando anúncios na sua conta do Mercado Livre..."):
                try:
                    r = requests.post(
                        f"{api_url}/marketplace-integrations/mercado_livre/import-items",
                        json={"credential_id": credential_id, "max_items": int(max_items)},
                        headers=headers
                    )
                    if r.status_code == 200:
                        d = r.json()
                        st.success(f"✅ {d['found']} anúncios encontrados · {d['imported']} novos importados · {d['updated']} atualizados.")
                        if d.get("errors"):
                            st.warning(f"{len(d['errors'])} itens com erro.")
                        st.rerun()
                    else:
                        st.error(f"Erro ao importar: {r.json().get('detail', r.text)}")
                except Exception as e:
                    st.error(f"Erro de conexão: {e}")

    st.markdown("---")

    # ---------------------------------------------------------
    # 3. Lista de anúncios (produtos) com edição/exclusão
    # ---------------------------------------------------------
    try:
        products = requests.get(f"{api_url}/products", headers=headers).json()
    except Exception as e:
        st.error(f"Erro ao carregar anúncios: {e}")
        return

    ml_products = [p for p in products if p.get("marketplace") == "mercado_livre"]

    if not ml_products:
        st.info("Nenhum anúncio ainda. Importe da sua conta do Mercado Livre acima.")
        return

    search = st.text_input("🔍 Filtrar por título", placeholder="Digite parte do título...")
    if search:
        ml_products = [p for p in ml_products if search.lower() in (p.get("title") or "").lower()]

    # -----------------------------------------------------
    # Divisão: ATIVOS na plataforma  x  NÃO ATIVOS
    # Ativo = anúncio publicado e com status 'active' no Mercado Livre.
    # Não ativo = pausado, encerrado, em revisão, ou ainda não publicado (só local).
    # -----------------------------------------------------
    ativos = [p for p in ml_products if p.get("marketplace_status") == "active"]
    inativos = [p for p in ml_products if p.get("marketplace_status") != "active"]

    STATUS_LABELS = {
        "active": ("🟢", "Ativo"),
        "paused": ("🟡", "Pausado"),
        "closed": ("🔴", "Encerrado"),
        "under_review": ("🔵", "Em revisão"),
        None: ("⚪", "Não publicado"),
    }

    def render_product(p):
        ext = p.get("external_listing_id")
        mstatus = p.get("marketplace_status")
        icon, label = STATUS_LABELS.get(mstatus, ("⚪", mstatus or "Não publicado"))
        badge = f"🟢 ML: {ext}" if ext else "⚪ só local"
        with st.expander(f"{icon} {p['title']}  —  R$ {p.get('price') or 0:.2f}  ·  estoque {p.get('available_quantity') if p.get('available_quantity') is not None else '—'}"):
            st.markdown(f"**Situação no ML:** {icon} {label} · {badge} · ID local: {p['id']}")

            with st.form(f"edit_form_{p['id']}"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    e_title = st.text_input("Título", value=p.get("title") or "", key=f"t_{p['id']}")
                with c2:
                    e_price = st.number_input("Preço (R$)", value=float(p.get("price") or 0), min_value=0.0, step=1.0, key=f"p_{p['id']}")
                with c3:
                    e_qty = st.number_input("Estoque", value=int(p.get("available_quantity") or 0), min_value=0, step=1, key=f"q_{p['id']}")
                e_desc = st.text_area("Descrição", value=p.get("description") or "", height=100, key=f"d_{p['id']}")

                sync_ml = st.checkbox("🔄 Replicar alterações no Mercado Livre", value=bool(ext), key=f"s_{p['id']}",
                                      help="Se marcado, a edição é enviada ao anúncio real no ML.")
                save = st.form_submit_button("💾 Salvar alterações")

                if save:
                    payload = {
                        "title": e_title,
                        "price": e_price,
                        "available_quantity": int(e_qty),
                        "description": e_desc,
                        "sync_to_ml": bool(sync_ml and ext),
                        "credential_id": credential_id,
                    }
                    try:
                        r = requests.put(f"{api_url}/products/{p['id']}", json=payload, headers=headers)
                        if r.status_code == 200:
                            st.success("Anúncio atualizado!" + (" (sincronizado com o ML)" if sync_ml and ext else ""))
                            st.rerun()
                        else:
                            st.error(f"Erro ao salvar: {r.json().get('detail', r.text)}")
                    except Exception as e:
                        st.error(f"Erro de conexão: {e}")

            # Exclusão — restrita a admins
            st.markdown("<hr style='margin:8px 0; border-color:#2d3142;'>", unsafe_allow_html=True)
            if is_admin:
                cd1, cd2 = st.columns([3, 1])
                with cd1:
                    confirm = st.checkbox("Confirmo a exclusão deste anúncio", key=f"cfd_{p['id']}")
                    close_ml = st.checkbox("Encerrar também no Mercado Livre", value=bool(ext), key=f"clm_{p['id']}")
                with cd2:
                    if st.button("🗑️ Excluir", key=f"del_{p['id']}", disabled=not confirm):
                        try:
                            url = f"{api_url}/products/{p['id']}?close_on_marketplace={str(close_ml).lower()}&credential_id={credential_id}"
                            r = requests.delete(url, headers=headers)
                            if r.status_code == 200:
                                st.success("Anúncio excluído.")
                                st.rerun()
                            else:
                                st.error(f"Erro ao excluir: {r.json().get('detail', r.text)}")
                        except Exception as e:
                            st.error(f"Erro de conexão: {e}")
            else:
                st.caption("🔒 Exclusão disponível apenas para usuários **admin**.")

    # Métricas no topo
    m1, m2, m3 = st.columns(3)
    m1.metric("📦 Total de anúncios", len(ml_products))
    m2.metric("🟢 Ativos na plataforma", len(ativos))
    m3.metric("⚪ Não ativos", len(inativos))

    sub_ativos, sub_inativos = st.tabs([
        f"🟢 Ativos no Mercado Livre ({len(ativos)})",
        f"⚪ Não ativos / Não publicados ({len(inativos)})",
    ])

    with sub_ativos:
        if not ativos:
            st.info("Nenhum anúncio ativo no Mercado Livre no momento.")
        else:
            st.caption("Anúncios publicados e com status **ativo** no Mercado Livre (visíveis e vendendo).")
            for p in ativos:
                render_product(p)

    with sub_inativos:
        if not inativos:
            st.info("Nenhum anúncio inativo. Tudo publicado e ativo! 🎉")
        else:
            st.caption("Anúncios **pausados, encerrados, em revisão** ou ainda **não publicados** (só locais).")
            for p in inativos:
                render_product(p)
