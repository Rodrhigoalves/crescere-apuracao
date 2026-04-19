import streamlit as st
import sys
import os

# Ajuste de caminho para encontrar o database.py na raiz
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from database import query_banco
except ImportError:
    st.error("Erro: O arquivo 'database.py' precisa estar na pasta raiz do projeto.")
    st.stop()

st.set_page_config(page_title="Gestão de Férias", layout="wide")

# Estilização das abas para melhor visualização
st.markdown("""
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { 
        background-color: #f0f2f6; 
        border-radius: 5px; 
        padding: 10px 20px;
    }
    .stTabs [aria-selected="true"] { 
        background-color: #007bff; 
        color: white !important; 
    }
    </style>
    """, unsafe_allow_html=True)

st.title("📅 Sistema de Controle de Férias")

# Busca inicial de dados
try:
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True")
except Exception as e:
    st.error(f"Erro ao acessar o banco: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 1. ÁREA DO FUNCIONÁRIO ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado no sistema.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Identifique-se para solicitar férias:", [""] + nomes)

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            # Validação do E-mail Corporativo (Sua regra de bloqueio)
            if user['email_corporativo'] is None or user['email_corporativo'] == "":
                st.error(f"Acesso Bloqueado para {selecionado}.")
                st.warning("Motivo: E-mail corporativo não cadastrado. Procure seu líder.")
            else:
                st.success(f"Logado como: {user['email_corporativo']}")
                with st.form("solicita_ferias"):
                    col1, col2 = st.columns(2)
                    d_ini = col1.date_input("Data de Início")
                    d_fim = col2.date_input("Data de Término")
                    abono = st.checkbox("Desejo vender 10 dias (Abono Pecuniário)")
                    
                    if st.form_submit_button("Enviar Solicitação"):
                        # Cálculo de dias corridos (+1 para incluir o dia de início)
                        total_dias = (d_fim - d_ini).days + 1
                        
                        if total_dias < 5:
                            st.error("Erro: O período mínimo permitido pela CLT é de 5 dias.")
                        else:
                            sql_insert = f"""
                                INSERT INTO rh_movimentacao_ferias 
                                (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, status)
                                VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {total_dias}, {abono}, 'Pendente')
                            """
                            query_banco(sql_insert)
                            st.success("Solicitação enviada com sucesso! Aguarde a aprovação do líder.")

# --- 2. ÁREA DO LÍDER (PROTEGIDA) ---
with tab_lider:
    # Substitua '123' pela senha que desejar ou use st.secrets
    senha_input = st.text_input("Senha do Líder:", type="password")
    
    if senha_input == "123": # <--- COLOQUE SUA SENHA AQUI
        st.divider()
        menu = st.radio("Selecione a operação:", ["Aprovações Pendentes", "Gerenciar Equipe"], horizontal=True)

        if menu == "Aprovações Pendentes":
            pendentes = query_banco("""
                SELECT f.nome, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario
                WHERE m.status = 'Pendente'
            """)
            
            if not pendentes:
                st.info("Nenhuma solicitação aguardando análise.")
            else:
                for p in pendentes:
                    with st.expander(f"Pedido: {p['nome']} ({p['dias_corridos']} dias)"):
                        st.write(f"Período: {p['data_inicio']} a {p['data_fim']}")
                        col_a, col_r = st.columns(2)
                        
                        if col_a.button("✅ Aprovar", key=f"aprov_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.success(f"Férias de {p['nome']} aprovadas!")
                            st.rerun()
                            
                        if col_r.button("❌ Recusar", key=f"rec_{p['id_movimento']}"):
                            # Aqui você pode adicionar um text_area para o motivo antes do update
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado' WHERE id_movimento={p['id_movimento']}")
                            st.warning("Solicitação recusada.")
                            st.rerun()

        elif menu == "Gerenciar Equipe":
            st.subheader("Novas Contratações e Ativação de E-mail")
            
            # Cadastro de novo funcionário
            with st.expander("➕ Cadastrar Novo Colaborador"):
                with st.form("cad_func"):
                    nome_novo = st.text_input("Nome Completo")
                    data_adm = st.date_input("Data de Admissão")
                    setor_novo = st.selectbox("Setor", ["Contabilidade", "RH", "Fiscal"])
                    if st.form_submit_button("Salvar Cadastro"):
                        # O email_corporativo não é enviado aqui, ficando NULL por padrão
                        sql_cad = f"INSERT INTO rh_funcionarios (nome, data_admissao, setor) VALUES ('{nome_novo}', '{data_adm}', '{setor_novo}')"
                        query_banco(sql_cad)
                        st.success(f"{nome_novo} cadastrado! Lembre-se de ativar o e-mail corporativo em breve.")
                        st.rerun()

            # Ativação de E-mail (Onde o líder libera o acesso do funcionário)
            st.divider()
            st.subheader("Ativar E-mail Corporativo / Desligar")
            for f in funcionarios_db:
                col_n, col_e, col_d = st.columns([2, 2, 1])
                col_n.write(f['nome'])
                
                # Se não tem email, mostra campo para adicionar
                if f['email_corporativo'] is None or f['email_corporativo'] == "":
                    novo_email = col_e.text_input("Definir E-mail", key=f"mail_{f['id_funcionario']}")
                    if col_e.button("Ativar", key=f"btn_mail_{f['id_funcionario']}"):
                        query_banco(f"UPDATE rh_funcionarios SET email_corporativo='{novo_email}' WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                else:
                    col_e.write(f['email_corporativo'])

                # Botão de Desligamento (Inativa o funcionário)
                if col_d.button("Desligar", key=f"desl_{f['id_funcionario']}"):
                    query_banco(f"UPDATE rh_funcionarios SET is_ativo=False WHERE id_funcionario={f['id_funcionario']}")
                    st.rerun()

    elif senha_input != "":
        st.error("Senha inválida.")
