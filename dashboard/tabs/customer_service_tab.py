import streamlit as st
import requests
import datetime

def _get_ml_credentials(api_url: str, headers: dict):
    try:
        r = requests.get(f"{api_url}/credentials", headers=headers)
        if r.status_code == 200:
            return [c for c in r.json() if c["provider"] == "mercado_livre"]
    except Exception:
        pass
    return []

def render(api_url: str, headers: dict, current_user: dict) -> None:
    st.markdown("### 🗣️ Perguntas dos Clientes (Mercado Livre)")
    st.markdown("""
    Sincronize as perguntas pré-venda do Mercado Livre. O agente de IA formulará rascunhos de respostas
    baseados nas descrições dos seus anúncios. **Nenhuma resposta é enviada sem a sua revisão e aprovação.**
    """)

    ml_creds = _get_ml_credentials(api_url, headers)

    if not ml_creds:
        st.warning("Nenhuma credencial do Mercado Livre conectada ainda. Conecte na aba 'Credenciais' ou 'Anúncios ML'.")
        return

    cred_options = {f"{c['label']} (#{c['id']})": c["id"] for c in ml_creds}
    sel_label = st.selectbox("Conta do Mercado Livre:", list(cred_options.keys()))
    credential_id = cred_options[sel_label]

    # Sincronização
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔄 Sincronizar perguntas do ML", type="primary"):
            with st.spinner("Buscando novas perguntas..."):
                try:
                    r = requests.post(
                        f"{api_url}/marketplace-integrations/mercado-livre/questions/sync",
                        json={"credential_id": credential_id, "max_fetch": 50},
                        headers=headers
                    )
                    if r.status_code == 200:
                        st.success("Perguntas sincronizadas com sucesso!")
                        st.rerun()
                    else:
                        st.error(f"Erro ao sincronizar: {r.text}")
                except Exception as e:
                    st.error(f"Erro de conexão: {e}")

    st.markdown("---")

    # Abas por status
    tab_pendentes, tab_prontas, tab_respondidas, tab_descartadas, tab_erros = st.tabs([
        "📥 Pendentes / Novos", 
        "📝 Rascunhos Prontos", 
        "✅ Respondidas", 
        "🗑️ Descartadas",
        "⛔ Requer atenção"
    ])

    def fetch_questions(status: str):
        try:
            r = requests.get(f"{api_url}/marketplace-integrations/mercado-livre/questions/?status={status}", headers=headers)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

    # Helper para renderizar a pergunta e opções
    def render_question_card(q, is_draft=False):
        with st.expander(f"De {q.get('asker_nickname', 'Comprador')} - Em: {q.get('ml_created_at', q.get('fetched_at', ''))}"):
            if q.get('status') == 'error':
                st.error(f"⛔ Requer atendimento manual — {q.get('review_reason','')}")
            
            st.markdown(f"**Pergunta:** {q['question_text']}")

            if q.get('matched_product_id'):
                st.caption(f"Referente ao produto Local ID: {q['matched_product_id']} | Item ML: {q['item_id']}")
            else:
                st.caption(f"Anúncio ML: {q['item_id']} (Não pareado localmente)")

            st.markdown("---")

            # Se ainda não tem rascunho
            if not is_draft and q['status'] == 'pending_draft':
                if st.button("✨ Gerar Rascunho com IA", key=f"gen_{q['id']}"):
                    with st.spinner("O Gemini está analisando seu produto..."):
                        try:
                            r = requests.post(f"{api_url}/marketplace-integrations/mercado-livre/questions/{q['id']}/draft", headers=headers)
                            if r.status_code == 200:
                                st.success("Rascunho gerado!")
                                st.rerun()
                            else:
                                st.error(f"Erro ao gerar rascunho: {r.text}")
                        except Exception as e:
                            st.error(f"Erro de conexão: {e}")

                if st.button("Descartar / Ignorar", key=f"dism1_{q['id']}"):
                    try:
                        requests.post(f"{api_url}/marketplace-integrations/mercado-livre/questions/{q['id']}/dismiss", headers=headers)
                        st.rerun()
                    except Exception:
                        pass

            # Se já tem rascunho
            if is_draft or q['status'] == 'draft_ready':
                if q.get('needs_human_review'):
                    st.warning(f"⚠️ A IA marcou esta resposta para revisão cuidadosa: **{q.get('review_reason')}**")

                with st.form(f"form_q_{q['id']}"):
                    final_text = st.text_area("Rascunho Sugerido (Edite se necessário):", value=q.get('ai_suggested_answer', ''), height=150)

                    c1, c2 = st.columns([1, 1])
                    with c1:
                        submit = st.form_submit_button("✅ Aprovar e Enviar", type="primary")

                    if submit:
                        try:
                            r = requests.post(
                                f"{api_url}/marketplace-integrations/mercado-livre/questions/{q['id']}/send",
                                json={"credential_id": credential_id, "final_text": final_text},
                                headers=headers
                            )
                            if r.status_code == 200:
                                st.success("Resposta enviada!")
                                st.rerun()
                            else:
                                st.error(f"Erro ao enviar: {r.text}")
                        except Exception as e:
                            st.error(f"Erro: {e}")

                if st.button("Descartar / Ignorar", key=f"dism2_{q['id']}"):
                    try:
                        requests.post(f"{api_url}/marketplace-integrations/mercado-livre/questions/{q['id']}/dismiss", headers=headers)
                        st.rerun()
                    except Exception:
                        pass

            if q['status'] == 'approved_sent':
                st.info(f"**Resposta Enviada:** {q.get('final_answer_text')}")
                st.caption(f"Enviado em: {q.get('answered_at')}")

    with tab_pendentes:
        pendentes = fetch_questions("pending_draft")
        st.subheader(f"{len(pendentes)} Pergunta(s) Pendente(s) de Análise")
        for q in pendentes:
            render_question_card(q)

    with tab_prontas:
        prontas = fetch_questions("draft_ready")
        st.subheader(f"{len(prontas)} Rascunho(s) Pronto(s) para Revisão")
        for q in prontas:
            render_question_card(q, is_draft=True)

    with tab_respondidas:
        respondidas = fetch_questions("approved_sent")
        st.subheader("Últimas Respostas Enviadas")
        for q in respondidas:
            render_question_card(q)

    with tab_descartadas:
        descartadas = fetch_questions("dismissed")
        st.subheader(f"Perguntas Descartadas/Ignoradas")
        for q in descartadas:
            render_question_card(q)

    with tab_erros:
        erros = fetch_questions("error")
        st.subheader(f"{len(erros)} Pergunta(s) com Falha ou Necessidade de Revisão Urgente")
        for q in erros:
            render_question_card(q, is_draft=(q.get("ai_suggested_answer") is not None))

    # Top metrics
    total_pendentes = len(pendentes)
    hoje = datetime.date.today().isoformat()
    respondidas_hoje = [q for q in respondidas if q.get("answered_at") and q["answered_at"].startswith(hoje)]

    todas = pendentes + prontas + respondidas + descartadas
    com_revisao = [q for q in todas if q.get("needs_human_review")]
    pct_revisao = (len(com_revisao) / len(todas) * 100) if todas else 0

    st.sidebar.markdown("### 📊 Métricas de Atendimento")
    st.sidebar.metric("Pendentes", total_pendentes)
    st.sidebar.metric("Respondidas (Hoje)", len(respondidas_hoje))
    st.sidebar.metric("Taxa de Incerteza da IA", f"{pct_revisao:.1f}%")
