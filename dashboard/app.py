import os
import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from dashboard.tabs import imports_tab, credentials_tab, marketplace_publish_tab, erp_sync_tab


# Configuração da página Streamlit
st.set_page_config(
    page_title="Catalog Audit AI Agent - Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Configuração do endpoint da API.
# Resolve a URL do backend nesta ordem: variável de ambiente (local/Render) ->
# secret do Streamlit Community Cloud -> localhost (default de desenvolvimento).
def _resolve_api_url() -> str:
    env_url = os.getenv("API_URL")
    if env_url:
        return env_url
    try:
        secret_url = st.secrets.get("API_URL")
        if secret_url:
            return secret_url
    except Exception:
        pass
    return "http://localhost:8000"

API_URL = _resolve_api_url()

# -------------------------------------------------------------
# Mecanismo de Autenticação JWT (Fase 7)
# -------------------------------------------------------------
token = st.session_state.get("access_token")
current_user = None

if token:
    try:
        r_me = requests.get(f"{API_URL}/auth/me", headers={"Authorization": f"Bearer {token}"})
        if r_me.status_code == 200:
            current_user = r_me.json()
        else:
            st.session_state.pop("access_token", None)
            st.warning("Sessão expirada. Faça login novamente.")
            st.rerun()
    except Exception:
        st.warning("Não foi possível validar o token de acesso com o backend.")

if not current_user:
    st.markdown("""
    <div style="max-width: 400px; margin: 80px auto; padding: 2rem; background-color: #1a1d27; border-radius: 12px; border: 1px solid #2d3142; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
        <h2 style="text-align: center; color: #818cf8; margin-bottom: 1.5rem;">🤖 Catalog Audit AI</h2>
        <h4 style="text-align: center; color: #e2e8f0; margin-bottom: 2rem; font-weight: 300;">Identificação Obrigatória</h4>
    </div>
    """, unsafe_allow_html=True)
    
    with st.container():
        col_space1, col_login, col_space2 = st.columns([1, 2, 1])
        with col_login:
            with st.form("login_form"):
                username = st.text_input("Usuário", placeholder="Seu nome de usuário")
                password = st.text_input("Senha", type="password", placeholder="Sua senha secreta")
                btn_login = st.form_submit_button("🔓 Entrar")
                
                if btn_login:
                    if not username.strip() or not password.strip():
                        st.error("Nome de usuário e senha são obrigatórios.")
                    else:
                        try:
                            # OAuth2 especifica envio via Form-data / Form-url-encoded
                            res = requests.post(
                                f"{API_URL}/auth/login",
                                data={"username": username.strip(), "password": password.strip()}
                            )
                            if res.status_code == 200:
                                st.session_state["access_token"] = res.json()["access_token"]
                                st.success("Autenticado com sucesso!")
                                st.rerun()
                            else:
                                st.error("Usuário ou senha incorretos.")
                        except Exception as e:
                            st.error(f"Erro de conexão com o backend: {e}")
            st.stop()

# Uma vez autenticado, gera o header global para as chamadas e abas
admin_headers = {"Authorization": f"Bearer {token}"}


# CSS customizado para visual moderno, escuro e premium
st.markdown("""
<style>
    /* Estilos Gerais */
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }
    
    .main {
        background-color: #0d0f14;
        color: #e2e8f0;
    }
    
    /* Header e Banner */
    .header-container {
        background: linear-gradient(135deg, #1e1b4b 0%, #311042 50%, #0d0f14 100%);
        padding: 2.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        border: 1px solid #3b0764;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    }
    
    .header-title {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(to right, #818cf8, #c084fc, #fb7185);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
    }
    
    .header-subtitle {
        color: #94a3b8;
        font-size: 1.1rem;
        margin-top: 0.5rem;
        font-weight: 300;
    }
    
    /* Status Pills */
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 500;
        text-transform: uppercase;
    }
    .status-pending { background-color: #3b2c0b; color: #f59e0b; border: 1px solid #d97706; }
    .status-audited { background-color: #1e293b; color: #38bdf8; border: 1px solid #0284c7; }
    .status-optimized { background-color: #064e3b; color: #34d399; border: 1px solid #059669; }
    
    /* Status das Credenciais (Badges) */
    .status-valid { background-color: #064e3b; color: #34d399; border: 1px solid #059669; }
    .status-expired { background-color: #4c0519; color: #fda4af; border: 1px solid #e11d48; }
    .status-error { background-color: #450a0a; color: #fca5a5; border: 1px solid #dc2626; }
    .status-untested { background-color: #1e293b; color: #94a3b8; border: 1px solid #475569; }

    
    /* Cards Modernos */
    .card {
        background: rgba(26, 29, 39, 0.7);
        border: 1px solid #2d3142;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        transition: transform 0.2s, border-color 0.2s;
    }
    .card:hover {
        transform: translateY(-2px);
        border-color: #4f46e5;
    }
    
    /* Comparação de Textos */
    .diff-container {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1rem;
        margin-top: 1rem;
    }
    .diff-box {
        background-color: #161822;
        padding: 1rem;
        border-radius: 8px;
        border-left: 4px solid #4f46e5;
    }
    .diff-box.original { border-left-color: #ef4444; }
    .diff-box.suggested { border-left-color: #10b981; }
    
    .diff-title {
        font-size: 0.85rem;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 0.5rem;
        font-weight: 600;
    }
    
    /* Score */
    .score-circle-container {
        display: flex;
        align-items: center;
        gap: 1.5rem;
        background: linear-gradient(90deg, #1e1e2f 0%, #161822 100%);
        padding: 1rem;
        border-radius: 12px;
        border: 1px solid #2d3142;
    }
    
    .score-value {
        font-size: 3rem;
        font-weight: 700;
        color: #10b981;
    }
    .score-value.low { color: #ef4444; }
    .score-value.mid { color: #f59e0b; }
    .score-value.high { color: #10b981; }
    
    /* Listas de problemas e atributos */
    .issue-item {
        background-color: #1e1c24;
        border: 1px solid #ef444433;
        padding: 10px 15px;
        border-radius: 8px;
        margin-bottom: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .severity-HIGH { border-left: 4px solid #ef4444; }
    .severity-MEDIUM { border-left: 4px solid #f59e0b; }
    .severity-LOW { border-left: 4px solid #38bdf8; }
    
    .attr-item {
        background-color: #1c2421;
        border: 1px solid #10b98133;
        border-left: 4px solid #10b981;
        padding: 10px 15px;
        border-radius: 8px;
        margin-bottom: 8px;
    }
    
    /* Marketplace Tags */
    .mkt-tag {
        font-size: 0.75rem;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        text-transform: uppercase;
    }
    .mkt-mercado_livre { background-color: #ffe600; color: #333; }
    .mkt-shopee { background-color: #ff5722; color: #fff; }
    .mkt-amazon { background-color: #232f3e; color: #ff9900; }
    .mkt-magalu { background-color: #0086ff; color: #fff; }
    .mkt-default { background-color: #4b5563; color: #fff; }
</style>
""", unsafe_allow_html=True)

# Container do Header do Dashboard
st.markdown("""
<div class="header-container">
    <h1 class="header-title">🤖 Catálogo Audit AI Agent</h1>
    <p class="header-subtitle">Fase 0 - Otimização de anúncios de e-commerce em marketplaces brasileiros com Google Gemini</p>
</div>
""", unsafe_allow_html=True)

# Barra Lateral - Conexão e Ações Rápidas
with st.sidebar:
    st.markdown("### 🔌 Conexão do Backend")
    api_status = False
    try:
        r = requests.get(f"{API_URL}/health", timeout=2)
        if r.status_code == 200:
            api_status = True
            st.success("Conectado ao FastAPI Backend!")
        else:
            st.warning(f"Backend retornou código {r.status_code}")
    except Exception:
        st.error("Não foi possível conectar ao FastAPI. Certifique-se de que o backend está rodando no endereço correto.")

    st.markdown("---")
    st.markdown("### 👤 Usuário Autenticado")
    st.write(f"Conectado como: **{current_user['username']}**")
    if st.button("🚪 Sair", type="secondary", use_container_width=True):
        st.session_state.pop("access_token", None)
        st.rerun()


    st.markdown("---")
    st.markdown("### 🛠️ Ações do Banco de Dados")
    if st.button("📥 Importar Anúncios de Teste", disabled=not api_status):
        try:
            r = requests.post(f"{API_URL}/products/import-test-listings", headers=admin_headers)
            if r.status_code == 200:
                msg = r.json().get("message", "Sucesso ao importar.")
                st.success(msg)
                st.rerun()
            else:
                st.error(f"Erro ao importar: {r.text}")
        except Exception as e:
            st.error(f"Erro de conexão: {e}")

    if st.button("⚡ Auditar Todos em Lote", type="primary", disabled=not api_status):
        with st.spinner("Auditando todos os pendentes via Gemini..."):
            try:
                r = requests.post(f"{API_URL}/products/audit-all", headers=admin_headers)
                if r.status_code == 200:
                    data = r.json()
                    st.success(data.get("message", "Sucesso!"))
                    if data.get("failed_count", 0) > 0:
                        st.warning(f"Falhas: {data.get('failed_count')}")
                    st.rerun()
                else:
                    st.error(f"Erro ao processar lote: {r.text}")
            except Exception as e:
                st.error(f"Erro de conexão: {e}")

# Se o backend não estiver conectado, interrompe a renderização para evitar erros visuais
if not api_status:
    st.info("Aguardando inicialização do servidor backend FastAPI...")
    st.stop()

# Busca produtos do banco
try:
    products = requests.get(f"{API_URL}/products", headers=admin_headers).json()
except Exception as e:
    st.error(f"Erro ao buscar produtos: {e}")
    st.stop()

# Cria abas principais no dashboard
tab_catalogo, tab_cadastro, tab_credentials, tab_publish, tab_sync, tab_logs = st.tabs([
    "📋 Catálogo & Auditoria", 
    "📥 Cadastro em Massa", 
    "🔑 Credenciais", 
    "🛒 Publicação ML", 
    "🔗 Sincronização Bling", 
    "📊 Logs de Custos & Tokens"
])

# -------------------------------------------------------------
# ABA 1: Catálogo & Auditoria
# -------------------------------------------------------------
with tab_catalogo:
    if not products:
        st.info("Nenhum anúncio cadastrado no banco de dados. Use a barra lateral para importar os anúncios de teste ou adicione um manualmente.")
        
        # Formulário simples para adicionar produto manual
        with st.expander("➕ Adicionar Anúncio Manualmente"):
            with st.form("manual_product"):
                title = st.text_input("Título do Anúncio")
                desc = st.text_area("Descrição")
                category = st.text_input("Categoria")
                price = st.number_input("Preço", min_value=0.0, format="%.2f")
                mkt = st.selectbox("Marketplace", ["mercado_livre", "shopee", "amazon", "magalu", "temu", "shein", "tiktok_shop"])
                images_input = st.text_area("URLs das Imagens (uma por linha)")
                
                submitted = st.form_submit_button("Cadastrar Produto")
                if submitted:
                    images_list = [line.strip() for line in images_input.split("\n") if line.strip()]
                    payload = {
                        "title": title,
                        "description": desc,
                        "images": images_list,
                        "category": category,
                        "price": price,
                        "marketplace": mkt
                    }
                    r = requests.post(f"{API_URL}/products", json=payload, headers=admin_headers)
                    if r.status_code == 200:
                        st.success("Anúncio cadastrado com sucesso!")
                        st.rerun()
                    else:
                        st.error(f"Erro ao cadastrar: {r.text}")
    else:
        col_list, col_detail = st.columns([1, 2])
        
        # Coluna da Esquerda: Lista de Produtos
        with col_list:
            st.markdown("### Selecione um Anúncio")
            
            # Filtro simples de status
            status_filter = st.selectbox("Filtrar por Status", ["Todos", "pending", "audited", "optimized"])
            
            for p in products:
                # Aplica filtro
                if status_filter != "Todos" and p["status"] != status_filter:
                    continue
                    
                # Definir classes CSS
                mkt_class = f"mkt-{p['marketplace']}" if p['marketplace'] in ['mercado_livre', 'shopee', 'amazon', 'magalu'] else "mkt-default"
                status_class = f"status-{p['status']}"
                
                card_html = f"""
                <div style="cursor: pointer; margin-bottom: 12px;">
                    <span class="mkt-tag {mkt_class}">{p['marketplace']}</span>
                    <span class="status-badge {status_class}" style="float: right;">{p['status']}</span>
                    <h4 style="margin: 8px 0 4px 0; font-size: 1rem; line-height: 1.3;">{p['title']}</h4>
                    <small style="color: #64748b;">R$ {p['price']:.2f} | ID: {p['id']}</small>
                </div>
                """
                
                # Usamos botões nativos do Streamlit simulando links para selecionar o produto
                # Para diferenciar e dar estilo, podemos encapsular no card
                with st.container():
                    st.markdown(card_html, unsafe_allow_html=True)
                    if st.button(f"Ver Detalhes do ID {p['id']}", key=f"btn_sel_{p['id']}"):
                        st.session_state.selected_product_id = p["id"]
                        st.rerun()
                    st.markdown("---")
                    
        # Coluna da Direita: Detalhes e Auditoria do Produto Selecionado
        with col_detail:
            selected_id = st.session_state.get("selected_product_id")
            
            # Se nenhum produto selecionado, pega o primeiro
            if not selected_id and products:
                selected_id = products[0]["id"]
                
            product = next((p for p in products if p["id"] == selected_id), None)
            
            if product:
                st.markdown(f"## {product['title']}")
                mkt_class = f"mkt-{product['marketplace']}" if product['marketplace'] in ['mercado_livre', 'shopee', 'amazon', 'magalu'] else "mkt-default"
                status_class = f"status-{product['status']}"
                
                st.markdown(f"""
                <div style="margin-bottom: 20px;">
                    <span class="mkt-tag {mkt_class}" style="font-size: 0.9rem; padding: 4px 12px;">{product['marketplace']}</span>
                    <span class="status-badge {status_class}" style="font-size: 0.9rem; padding: 4px 12px; margin-left: 10px;">{product['status']}</span>
                    <span style="margin-left: 20px; font-weight: 500; font-size: 1.1rem;">Preço: R$ {product['price']:.2f}</span>
                </div>
                """, unsafe_allow_html=True)
                
                # Detalhes Originais
                with st.expander("🔍 Visualizar Conteúdo Original do Anúncio", expanded=True):
                    st.markdown(f"**Categoria:** `{product['category'] or 'Não informada'}`")
                    st.markdown("**Descrição:**")
                    st.code(product["description"] or "Sem descrição.", language="text")
                    
                    if product["images"]:
                        st.markdown("**Imagens:**")
                        cols_img = st.columns(min(len(product["images"]), 5))
                        for idx, img_url in enumerate(product["images"]):
                            with cols_img[idx % 5]:
                                try:
                                    st.image(img_url, use_container_width=True)
                                except Exception:
                                    st.caption(f"Erro ao carregar imagem: {img_url[:30]}...")
                
                # Ações de Auditoria
                col_btn_audit, _ = st.columns([1, 2])
                with col_btn_audit:
                    audit_label = "⚡ Executar Auditoria AI" if product["status"] == "pending" else "🔄 Re-auditar Anúncio"
                    if st.button(audit_label, type="primary", use_container_width=True):
                        with st.spinner("O Agente Gemini está auditando o anúncio..."):
                            try:
                                r = requests.post(f"{API_URL}/products/{product['id']}/audit", headers=admin_headers)
                                if r.status_code == 200:
                                    st.success("Auditoria realizada com sucesso!")
                                    st.rerun()
                                else:
                                    st.error(f"Erro ao auditar: {r.json().get('detail', r.text)}")
                            except Exception as e:
                                st.error(f"Erro de conexão com o backend: {e}")
                                
                # Se o produto já foi auditado, exibe as sugestões
                if product["status"] in ["audited", "optimized"]:
                    # Busca as sugestões geradas
                    try:
                        suggestions_res = requests.get(f"{API_URL}/products/{product['id']}/suggestions", headers=admin_headers).json()
                        suggestion = suggestions_res[-1] if suggestions_res else None
                    except Exception as e:
                        st.error(f"Erro ao carregar sugestões: {e}")
                        suggestion = None
                        
                    if suggestion:
                        st.markdown("---")
                        st.markdown("### 💡 Sugestões de Melhorias do Agente")
                        
                        # Indicador do Score de SEO
                        score = suggestion["seo_score"]
                        score_class = "low" if score < 50 else ("mid" if score < 80 else "high")
                        
                        st.markdown(f"""
                        <div class="score-circle-container">
                            <div class="score-value {score_class}">{score}</div>
                            <div>
                                <h4 style="margin: 0; color: #e2e8f0;">Score de SEO / Qualidade</h4>
                                <p style="margin: 4px 0 0 0; color: #64748b; font-size: 0.9rem;">
                                    Nota gerada pelo Gemini avaliando regras de otimização e penalidades do marketplace.
                                </p>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        # Título Comparação
                        st.markdown("#### 🏷️ Título do Anúncio")
                        st.markdown(f"""
                        <div class="diff-container">
                            <div class="diff-box original">
                                <div class="diff-title">Título Original ({len(product['title'])} carac.)</div>
                                <strong>{product['title']}</strong>
                            </div>
                            <div class="diff-box suggested">
                                <div class="diff-title">Sugestão do Agente ({len(suggestion['suggested_title'])} carac.)</div>
                                <strong>{suggestion['suggested_title']}</strong>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        # Descrição Comparação
                        st.markdown("#### 📝 Descrição do Anúncio")
                        col_desc_orig, col_desc_sug = st.columns(2)
                        with col_desc_orig:
                            st.caption("Descrição Original")
                            st.code(product["description"] or "Sem descrição.", language="text")
                        with col_desc_sug:
                            st.caption("Descrição Sugerida pelo Gemini")
                            st.code(suggestion["suggested_description"], language="text")
                            
                        # Atributos faltantes
                        st.markdown("#### 🔧 Ficha Técnica e Atributos Faltantes")
                        if suggestion["missing_attributes"]:
                            for attr in suggestion["missing_attributes"]:
                                st.markdown(f"""
                                <div class="attr-item">
                                    <strong>{attr['name']}</strong>: <code style="color: #34d399;">{attr['recommended_value']}</code>
                                    <p style="margin: 4px 0 0 0; font-size: 0.85rem; color: #94a3b8;">{attr['reason']}</p>
                                </div>
                                """, unsafe_allow_html=True)
                        else:
                            st.success("Nenhum atributo crítico ausente identificado!")
                            
                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        # Problemas de Imagem
                        st.markdown("#### 🖼️ Auditoria de Imagens")
                        if suggestion["image_issues"]:
                            for issue in suggestion["image_issues"]:
                                severity = issue.get("severity", "MEDIUM")
                                st.markdown(f"""
                                <div class="issue-item severity-{severity}">
                                    <div>
                                        <strong>Gravidade: <span class="status-badge" style="background-color: #ef444422; color: #f87171; border: 1px solid #ef444455; padding: 2px 8px; font-size: 0.7rem;">{severity}</span></strong>
                                        <p style="margin: 4px 0 0 0; font-size: 0.9rem; color: #e2e8f0;">{issue['issue']}</p>
                                        <small style="color: #64748b; font-size: 0.75rem;">URL: {issue['image_url'][:60]}...</small>
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
                        else:
                            st.success("Nenhum problema grave identificado nas imagens!")
                            
                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        # Status da Sugestão e Ações de Aprovação
                        st.markdown(f"**Status da Sugestão Atual:** `{suggestion['status'].upper()}`")
                        
                        if suggestion["status"] == "pending":
                            col_approve, col_reject, _ = st.columns([1, 1, 2])
                            with col_approve:
                                if st.button("✅ Aprovar Alterações", type="primary", use_container_width=True):
                                    r = requests.post(f"{API_URL}/suggestions/{suggestion['id']}/approve", headers=admin_headers)
                                    if r.status_code == 200:
                                        st.success("Sugestão APROVADA e aplicada no anúncio original!")
                                        st.rerun()
                                    else:
                                        st.error("Erro ao aprovar.")
                            with col_reject:
                                if st.button("❌ Rejeitar Alterações", use_container_width=True):
                                    r = requests.post(f"{API_URL}/suggestions/{suggestion['id']}/reject", headers=admin_headers)
                                    if r.status_code == 200:
                                        st.warning("Sugestão REJEITADA.")
                                        st.rerun()
                                    else:
                                        st.error("Erro ao rejeitar.")
                        elif suggestion["status"] == "approved":
                            st.success(f"Alterações aprovadas e aplicadas em: {suggestion.get('reviewed_at', '')}")
                        elif suggestion["status"] == "rejected":
                            st.error(f"Sugestão rejeitada em: {suggestion.get('reviewed_at', '')}")
            else:
                st.info("Selecione um produto da lista para ver os detalhes da auditoria.")

# -------------------------------------------------------------
# ABA: Cadastro em Massa
# -------------------------------------------------------------
with tab_cadastro:
    imports_tab.render(API_URL, admin_headers)

# -------------------------------------------------------------
# ABA: Credenciais
# -------------------------------------------------------------
with tab_credentials:
    credentials_tab.render(API_URL, admin_headers)

# -------------------------------------------------------------
# ABA: Publicação Mercado Livre
# -------------------------------------------------------------
with tab_publish:
    marketplace_publish_tab.render(API_URL, admin_headers)

# -------------------------------------------------------------
# ABA: Sincronização Bling
# -------------------------------------------------------------
with tab_sync:
    erp_sync_tab.render(API_URL, admin_headers)


# -------------------------------------------------------------
# ABA 2: Logs de Custos & Tokens
# -------------------------------------------------------------
with tab_logs:
    st.markdown("### 📊 Histórico de Execuções e Consumo de Tokens")
    st.markdown("""
    Nesta tela são monitorados todos os acessos feitos à API do Gemini para auditorias, 
    permitindo auditar a latência, a quantidade de tokens consumidos e o controle de custos.
    """)
    
    try:
        logs_res = requests.get(f"{API_URL}/logs", headers=admin_headers).json()
    except Exception as e:
        st.error(f"Erro ao carregar logs: {e}")
        logs_res = []
        
    if not logs_res:
        st.info("Nenhum log de auditoria disponível no momento. Execute uma auditoria para registrar chamadas.")
    else:
        # Cria um DataFrame para exibir em tabela estruturada
        log_data = []
        for log in logs_res:
            log_data.append({
                "ID Log": log["id"],
                "ID Prod": log["product_id"],
                "Modelo": log["model_used"],
                "Tokens In": log["tokens_input"],
                "Tokens Out": log["tokens_output"],
                "Tokens Total": log["tokens_input"] + log["tokens_output"],
                "Custo (USD)": f"${log['token_cost_usd']:.4f}",
                "Latência (s)": round(log["latency_seconds"], 2),
                "Data Execução": datetime.fromisoformat(log["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S")
            })

        df = pd.DataFrame(log_data)
        st.dataframe(df, use_container_width=True)

        # Métricas Consolidadas (a partir dos valores numéricos brutos)
        total_tokens_in = df["Tokens In"].sum()
        total_tokens_out = df["Tokens Out"].sum()
        total_requests = len(df)
        avg_latency = df["Latência (s)"].mean()
        
        st.markdown("### 📈 Métricas Consolidadas")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1:
            st.metric("Total de Requisições", total_requests)
        with col_m2:
            st.metric("Tokens de Entrada Acumulados", f"{total_tokens_in:,}")
        with col_m3:
            st.metric("Tokens de Saída Acumulados", f"{total_tokens_out:,}")
        with col_m4:
            st.metric("Latência Média", f"{avg_latency:.2f}s")
            
        # Explicação de custo
        st.info("ℹ️ Os custos de tokens são exibidos como $0.0000 pois estamos utilizando o Tier Gratuito (Free Tier) do Google AI Studio para testes locais.")
