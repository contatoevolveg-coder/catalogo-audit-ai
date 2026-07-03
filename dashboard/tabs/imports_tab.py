import streamlit as st
import requests
import pandas as pd
from datetime import datetime

def render(api_url: str, headers: dict) -> None:
    st.markdown("### 📦 Cadastro em Massa de Produtos (Planilhas)")
    st.markdown("""
    Importe lotes de produtos a partir de planilhas CSV ou Excel,
    ajuste o mapeamento de colunas, valide inconsistências e realize auditorias automáticas.
    """)

    # Valida cabeçalhos obrigatórios (Fase 7: todos os endpoints de /imports exigem login)
    if not headers or "Authorization" not in headers or not headers["Authorization"].strip():
        st.error("Chave de administrador ausente ou incorreta. Preencha o campo 'Admin API Key' na barra lateral.")
        return

    # 1. Download de Template
    with st.expander("📥 Baixar Modelo de Planilha (Template)"):
        col_mkt, col_dl = st.columns([3, 1])
        with col_mkt:
            template_mkt = st.selectbox(
                "Selecione o Marketplace desejado para o modelo:",
                ["mercado_livre", "shopee", "amazon", "magalu", "temu", "shein", "tiktok_shop"],
                key="template_mkt_select"
            )
        with col_dl:
            st.write("")  # Alinhamento vertical
            st.write("")
            try:
                template_url = f"{api_url}/imports/template?marketplace={template_mkt}"
                r_temp = requests.get(template_url, headers=headers)
                if r_temp.status_code == 200:
                    st.download_button(
                        label="📄 Baixar CSV",
                        data=r_temp.content,
                        file_name=f"template_{template_mkt}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                else:
                    st.error(f"Erro ao obter template (HTTP {r_temp.status_code})")
            except Exception as e:
                st.error(f"Erro de conexão ao buscar template: {e}")

    st.markdown("---")

    # 2. Upload de Nova Planilha
    st.markdown("#### 📤 Enviar Nova Planilha")
    uploaded_file = st.file_uploader(
        "Selecione o arquivo da planilha (.csv ou .xlsx)",
        type=["csv", "xlsx"]
    )
    col_upload_mkt, col_upload_btn = st.columns([3, 1])
    with col_upload_mkt:
        import_mkt = st.selectbox(
            "Marketplace de Destino:",
            ["mercado_livre", "shopee", "amazon", "magalu", "temu", "shein", "tiktok_shop"],
            key="import_mkt_select"
        )
    with col_upload_btn:
        st.write("")
        st.write("")
        btn_upload = st.button("🚀 Enviar Planilha", use_container_width=True, disabled=not uploaded_file)

    if btn_upload and uploaded_file:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
        data = {"marketplace": import_mkt}
        with st.spinner("Enviando arquivo e analisando cabeçalhos..."):
            try:
                r = requests.post(f"{api_url}/imports", files=files, data=data, headers=headers)
                if r.status_code == 200:
                    res = r.json()
                    st.session_state["import_batch_id"] = res["batch_id"]
                    st.session_state["import_detected_columns"] = res["detected_columns"]
                    st.session_state["import_suggested_mapping"] = res["suggested_mapping"]
                    st.session_state["import_sample_rows"] = res["sample_rows"]
                    st.session_state["import_status"] = "uploaded"
                    st.success(f"Lote #{res['batch_id']} criado com sucesso!")
                    st.rerun()
                else:
                    st.error(f"Erro ao criar lote de importação: {r.text}")
            except Exception as e:
                st.error(f"Erro ao conectar com o backend: {e}")

    # 3. Processamento do Lote Ativo (Mapeamento, Validação, Confirmação)
    batch_id = st.session_state.get("import_batch_id")
    if batch_id:
        st.markdown(f"### ⚙️ Processando Lote Ativo: **Lote #{batch_id}**")
        
        # Carrega dados do lote atualizado do banco
        try:
            batch_data = requests.get(f"{api_url}/imports/{batch_id}", headers=headers).json()
            st.session_state["import_status"] = batch_data["status"]
        except Exception as e:
            st.error(f"Erro ao atualizar status do lote: {e}")
            batch_data = {}

        current_status = st.session_state.get("import_status", "uploaded")
        
        # Botão para descartar lote ativo
        if st.button("🗑️ Descartar Lote Ativo da Tela"):
            st.session_state.pop("import_batch_id", None)
            st.session_state.pop("import_detected_columns", None)
            st.session_state.pop("import_suggested_mapping", None)
            st.session_state.pop("import_sample_rows", None)
            st.session_state.pop("import_status", None)
            st.rerun()

        # ABAIXO EXIBE AS ETAPAS COM BASE NO STATUS DO LOTE
        
        # --- ETAPA A: Definir Mapeamento (se status for 'uploaded') ---
        if current_status == "uploaded":
            st.markdown("#### 1️⃣ Ajustar Mapeamento de Colunas")
            st.info("O sistema identificou as colunas abaixo automaticamente. Ajuste se necessário:")
            
            sample_rows = st.session_state.get("import_sample_rows", [])
            detected_cols = st.session_state.get("import_detected_columns", [])
            suggested_map = st.session_state.get("import_suggested_mapping", {})

            if sample_rows:
                st.markdown("**Prévia das 3 primeiras linhas:**")
                st.dataframe(pd.DataFrame(sample_rows), use_container_width=True)

            # Formulário de mapeamento
            with st.form("mapping_form"):
                new_mapping = {}
                col_left, col_right = st.columns(2)
                
                # Campos canônicos requeridos
                canonical_fields = ["title", "description", "price", "available_quantity", "condition", "images", "category", "attributes"]
                
                for idx, field in enumerate(canonical_fields):
                    # Alterna colunas para renderização lado a lado
                    col_target = col_left if idx % 2 == 0 else col_right
                    
                    # Valor sugerido inicial
                    suggested_val = suggested_map.get(field, "")
                    default_idx = 0
                    if suggested_val in detected_cols:
                        default_idx = detected_cols.index(suggested_val) + 1  # +1 por causa do "(nenhum)"
                        
                    options = ["(nenhum)"] + detected_cols
                    val = col_target.selectbox(
                        f"Mapear campo: **{field}** para coluna:",
                        options=options,
                        index=default_idx,
                        key=f"map_field_{field}"
                    )
                    if val != "(nenhum)":
                        new_mapping[field] = val
                        
                btn_save_map = st.form_submit_button("💾 Salvar Mapeamento")

            if btn_save_map:
                with st.spinner("Salvando mapeamento no backend..."):
                    try:
                        r_map = requests.post(
                            f"{api_url}/imports/{batch_id}/mapping",
                            json={"mapping": new_mapping},
                            headers=headers
                        )
                        if r_map.status_code == 200:
                            st.success("Mapeamento salvo com sucesso!")
                            st.session_state["import_status"] = "mapped"
                            st.rerun()
                        else:
                            st.error(f"Erro ao salvar mapeamento: {r_map.text}")
                    except Exception as e:
                        st.error(f"Erro de conexão: {e}")

        # --- ETAPA B: Validar Lote (se status for 'mapped') ---
        elif current_status == "mapped":
            st.markdown("#### 2️⃣ Validar Consistência dos Dados")
            st.info("O mapeamento foi definido. Clique abaixo para rodar as validações de integridade.")
            
            if st.button("🔍 Rodar Validação dos Dados"):
                with st.spinner("Validando linhas..."):
                    try:
                        r_val = requests.post(f"{api_url}/imports/{batch_id}/validate", headers=headers)
                        if r_val.status_code == 200:
                            st.success("Validação executada com sucesso!")
                            st.session_state["import_status"] = "validated"
                            st.rerun()
                        else:
                            st.error(f"Erro ao rodar validações: {r_val.text}")
                    except Exception as e:
                        st.error(f"Erro de conexão: {e}")

        # --- ETAPA C: Resultados da Validação & Confirmação (se status for 'validated') ---
        elif current_status == "validated":
            st.markdown("#### 3️⃣ Resultados da Validação & Confirmação")
            
            # Mostra estatísticas do lote
            total_rows = batch_data.get("total_rows", 0)
            valid_rows = batch_data.get("valid_rows", 0)
            invalid_rows = batch_data.get("invalid_rows", 0)
            
            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("Total de Linhas", total_rows)
            col_t2.metric("Linhas Válidas (Prontas)", valid_rows)
            col_t3.metric("Linhas Inválidas (Serão Ignoradas)", invalid_rows)

            # Exibe erros de linhas se existirem
            if invalid_rows > 0:
                st.warning("⚠️ Foram detectados erros em algumas linhas da planilha:")
                try:
                    r_rows = requests.get(f"{api_url}/imports/{batch_id}/rows?status=invalid", headers=headers).json()
                    for idx, row in enumerate(r_rows):
                        row_num = row["row_number"]
                        errors = row.get("validation_errors", [])
                        
                        with st.expander(f"Linha #{row_num} - {len(errors)} erro(s)"):
                            st.write("**Dados brutos:**", row["raw_data"])
                            st.write("**Mapeamento:**", row["mapped_data"])
                            st.write("**Erros detalhados:**")
                            for err in errors:
                                severity_icon = "❌" if err["severity"] == "error" else "⚠️"
                                st.markdown(f"- {severity_icon} **{err['field']}**: {err['message']} *(Cód: {err['code']})*")
                except Exception as e:
                    st.error(f"Erro ao buscar linhas inválidas: {e}")

            # Formulário de confirmação
            st.markdown("##### 🚀 Confirmar Carga de Produtos")
            
            auto_audit = st.checkbox(
                "Auditar automaticamente os produtos cadastrados com Inteligência Artificial pós-importação",
                value=True,
                help="Se marcado, inicia imediatamente uma auditoria Gemini nos produtos gerados."
            )
            
            max_audit = st.number_input(
                "Limite de produtos a auditar automaticamente neste lote (Salvaguarda Serverless):",
                min_value=1,
                max_value=100,
                value=15,
                disabled=not auto_audit
            )

            if st.button("✅ Confirmar Importação e Gravar no Catálogo", type="primary"):
                with st.spinner("Confirmando importação e criando produtos no banco..."):
                    try:
                        confirm_url = f"{api_url}/imports/{batch_id}/confirm?auto_audit={str(auto_audit).lower()}&max_audit={max_audit}"
                        r_conf = requests.post(confirm_url, headers=headers)
                        if r_conf.status_code == 200:
                            res_conf = r_conf.json()
                            st.success("Importação concluída!")
                            
                            # Mostra o relatório
                            st.markdown("### 📊 Relatório da Importação")
                            st.write(f"- **Produtos Cadastrados:** {res_conf.get('imported')}")
                            st.write(f"- **Linhas Ignoradas (Erros):** {res_conf.get('skipped_invalid')}")
                            st.write(f"- **Produtos Auditados:** {res_conf.get('audited')}")
                            st.write(f"- **Auditorias Puladas (Limite):** {res_conf.get('audit_skipped')}")
                            
                            errs = res_conf.get("audit_errors", [])
                            if errs:
                                st.error("Ocorreram alguns erros isolados ao auditar:")
                                for e in errs:
                                    st.write(f"- Produto ID {e.get('product_id')}: {e.get('error')}")
                            
                            # Limpa lote ativo
                            st.session_state.pop("import_batch_id", None)
                            st.session_state.pop("import_detected_columns", None)
                            st.session_state.pop("import_suggested_mapping", None)
                            st.session_state.pop("import_sample_rows", None)
                            st.session_state.pop("import_status", None)
                            
                            if st.button("Voltar ao Cadastro"):
                                st.rerun()
                        else:
                            st.error(f"Erro ao confirmar importação: {r_conf.text}")
                    except Exception as e:
                        st.error(f"Erro de conexão: {e}")

        # --- ETAPA D: Lote já confirmado ---
        elif current_status == "confirmed":
            st.success("Este lote já foi confirmado e finalizado!")
            st.session_state.pop("import_batch_id", None)

    st.markdown("---")

    # 4. Seção de Lotes Anteriores
    st.markdown("#### 📂 Histórico de Lotes Importados")
    try:
        batches = requests.get(f"{api_url}/imports", headers=headers).json()
    except Exception as e:
        st.error(f"Erro de conexão ao buscar histórico de lotes: {e}")
        batches = []

    if not batches:
        st.info("Nenhum lote importado anteriormente.")
    else:
        # Tabela formatada para visualização
        batch_list = []
        for b in batches:
            batch_list.append({
                "ID Lote": b["id"],
                "Marketplace": b["marketplace"],
                "Status": b["status"],
                "Total Linhas": b["total_rows"],
                "Válidas": b["valid_rows"],
                "Inválidas": b["invalid_rows"],
                "Data Criação": datetime.fromisoformat(b["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")
            })
        
        df_batches = pd.DataFrame(batch_list)
        st.dataframe(df_batches, use_container_width=True)

        # Excluir lote
        with st.expander("🗑️ Excluir Lote do Histórico"):
            del_id = st.selectbox(
                "Selecione o Lote para excluir definitivamente:",
                [b["id"] for b in batches]
            )
            confirm_del = st.checkbox("Confirmo que desejo excluir os registros temporários deste lote.", key="confirm_del_batch")
            btn_del = st.button("Confirmar Exclusão", disabled=not confirm_del)
            
            if btn_del and del_id:
                try:
                    r_del = requests.delete(f"{api_url}/imports/{del_id}", headers=headers)
                    if r_del.status_code == 200:
                        st.success(f"Lote #{del_id} excluído com sucesso!")
                        st.rerun()
                    else:
                        st.error(f"Erro ao excluir: {r_del.text}")
                except Exception as e:
                    st.error(f"Erro de conexão ao deletar: {e}")
