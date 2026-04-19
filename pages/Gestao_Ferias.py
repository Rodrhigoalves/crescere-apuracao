import streamlit as st
import sys
import os

# Garante a conexão com o banco
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from database import query_banco
except ImportError:
    st.error("Arquivo database.py não encontrado na raiz.")

st.set_page_config(page_title="Gestão de Férias", layout="wide")

# --- ESTILIZAÇÃO ---
st.markdown("""
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 5px; padding: 10px; }
    .stTabs [aria-selected="true"] { background-color: #007bff; color: white; }
    </style>
    """, unsafe_allow_html=True)

st.title("📅 Portal de Férias")

# Busca funcionários ativos para o formulário público
try:
    funcionarios_db = query_banco("SELECT id_funcionario, nome, email_corporativo FROM rh_funcionarios WHERE is_ativo = True")
except:
    st.error("Erro ao conectar com as tabelas de RH. Verifique se o script SQL foi executado.")
    st.stop()

# Criamos duas áreas principais
tab_publica, tab_privada = st.tabs(["🚀 Espaço do Funcionário", "🔒 Área Restrita (Líder/RH)"])

# --- 1. ESPAÇO DO FUNCIONÁRIO (ACESSO LIVRE) ---
with tab_publica:
    st.subheader("Solicitar Novo Período de Descanso")
    
    if not funcionarios_db:
        st.info("Nenhum funcionário habilitado no sistema.")
    else:
        # Lista apenas nomes para o selectbox
        nomes = [f['nome'] for f in funcionarios_db]
        nome_sel = st.selectbox("Selecione seu nome:", [""] + nomes)
        
        if nome_sel:
            # Verifica se tem e-mail cadastrado
            user = next(item for item in funcionarios_db if item["nome"] == nome_sel)
            
            if user['email_corporativo'] is None:
                st.error("⚠️ Acesso Bloqueado: Seu e-mail corporativo ainda não foi cadastrado pelo líder.")
                st.info("O agendamento só é permitido para colaboradores com e-mail ativo.")
            else:
                with st.form("form_solicitacao"):
                    col1, col2 = st.columns(2)
                    data_ini = col1.date_input("Início das Férias")
                    data_fim = col2.date_input("Último dia de Descanso")
                    abono = st.checkbox("Desejo vender 10 dias (Abono Pecuniário)")
                    
                    if st.form_submit_button("Enviar Solicitação"):
                        dias = (data_fim - data_ini).days + 1
                        # Validação simples da CLT (mínimo 5 dias)
                        if dias < 5:
                            st.warning("Pela CLT, o período mínimo deve ser de 5 dias.")
                        else:
                            query_banco(f"""
                                INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario)
                                VALUES ({user['id_funcionario']}, '{data_ini}', '{data_fim}', {dias}, {abono})
                            """)
                            st.success("✅ Solicitação enviada! Seu líder será notificado por e-mail.")

# --- 2. ÁREA RESTRITA (PROTEGIDA POR SENHA) ---
with tab_privada:
    senha = st.text_input("Digite a senha de acesso do Líder:", type="password")
    
    if senha == "SUA_SENHA_AQUI": # Defina sua senha aqui
        st.success("Acesso Autorizado")
        
        menu_lider = st.radio("O que deseja fazer?", ["Aprovar Pedidos", "Gestão de Equipe", "Histórico de Saldos"], horizontal=True)
        
        if menu_lider == "Aprovar Pedidos":
            st.write("### 📩 Pendentes de Análise")
            # SQL para buscar pendentes com join
            pendentes = query_banco("""
                SELECT f.nome, f.email_lider, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario
                WHERE m.status = 'Pendente'
            """)
            if not pendentes:
                st.info("Não há pedidos aguardando aprovação.")
            else:
                for p in pendentes:
                    with st.expander(f"Pedido de {p['nome']} - {p['dias_corridos']} dias"):
                        st.write(f"**Período:** {p['data_inicio']} até {p['data_fim']}")
                        col_ap, col_re = st.columns(2)
                        if col_ap.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.rerun()
                        if col_re.button("❌ Recusar", key=f"re_{p['id_movimento']}"):
                            motivo = st.text_area("Motivo da recusa:", key=f"mot_{p['id_movimento']}")
                            if motivo:
                                query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado', motivo_recusa='{motivo}' WHERE id_movimento={p['id_movimento']}")
                                st.rerun()
        
        elif menu_lider == "Gestão de Equipe":
            st.write("### 👥 Cadastro e Desligamento")
            # Aqui você coloca o formulário de cadastrar novos e o botão de inativar (is_ativo = False)
            with st.expander("➕ Cadastrar Novo Funcionário"):
                n_nome = st.text_input("Nome")
                n_adm = st.date_input("Admissão")
                if st.button("Salvar"):
                    query_banco(f"INSERT INTO rh_funcionarios (nome, data_admissao) VALUES ('{n_nome}', '{n_adm}')")
                    st.success("Cadastrado com sucesso!")
    
    elif senha != "":
        st.error("Senha incorreta.")
