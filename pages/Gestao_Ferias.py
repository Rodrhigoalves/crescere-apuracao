import streamlit as st
import sys
import os

# 1. AJUSTE DE CAMINHO E IMPORTAÇÃO
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from database import query_banco
except ImportError:
    st.error("Erro: O arquivo 'database.py' não foi encontrado na raiz do projeto.")
    st.stop()

# 2. CONFIGURAÇÃO DA PÁGINA
st.set_page_config(page_title="Gestão de Férias", layout="wide")

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

# 3. CARREGAMENTO DE DADOS INICIAIS
try:
    # Busca funcionários ativos
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True ORDER BY nome ASC")
except Exception as e:
    st.error(f"Erro ao acessar o banco de dados: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 4. ÁREA DO FUNCIONÁRIO (PÚBLICA) ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado no sistema.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome para solicitar férias:", [""] + nomes)

        if selecionado:
            # Localiza o usuário selecionado na lista
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            # REGRA DE BLOQUEIO: Só acessa se tiver e-mail corporativo cadastrado
            if user['email_corporativo'] is None or user['email_corporativo'].strip() == "":
                st.error(f"⚠️ Acesso Bloqueado para {selecionado}.")
                st.warning("Motivo: Seu e-mail corporativo ainda não foi cadastrado. O agendamento só é liberado após a ativação do e-mail pelo seu líder.")
            else:
                st.info(f"Conectado como: {user['email_corporativo']}")
                with st.form("solicita_ferias", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    d_ini = col1.date_input("Data de Início")
                    d_fim = col2.date_input("Data de Término (Último dia de descanso)")
                    abono = st.checkbox("Desejo vender 10 dias (Abono Pecuniário)")
                    
                    if st.form_submit_button("Enviar Solicitação"):
                        total_dias = (d_fim - d_ini).days + 1
                        
                        if total_dias < 5:
                            st.error("Erro: Pela CLT, o período mínimo de férias deve ser de 5 dias.")
                        else:
                            # SQL para gravar a solicitação
                            sql_solicitacao = f"""
                                INSERT INTO rh_movimentacao_ferias 
                                (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, status)
                                VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {total_dias}, {abono}, 'Pendente')
                            """
                            query_banco(sql_solicitacao)
                            st.success("✅ Solicitação enviada com sucesso! Seu líder receberá uma notificação.")

# --- 5. ÁREA DO LÍDER (PROTEGIDA POR SENHA) ---
with tab_lider:
    # DICA: Use st.secrets para a senha em produção
    senha_acesso = st.text_input("Digite a senha de Gestão:", type="password")
    
    if senha_acesso == "123": # <--- ALTERE SUA SENHA AQUI
        st.divider()
        menu = st.radio("Escolha uma opção:", ["Aprovações Pendentes", "Gerenciar Equipe"], horizontal=True)

        # 5.1 APROVAÇÕES
        if menu == "Aprovações Pendentes":
            pendentes = query_banco("""
                SELECT f.nome, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario
                WHERE m.status = 'Pendente'
            """)
            
            if not pendentes:
                st.info("Não há solicitações aguardando análise no momento.")
            else:
                for p in pendentes:
                    with st.expander(f"Pedido de {p['nome']} - {p['dias_corridos']} dias"):
                        st.write(f"**Período:** {p['data_inicio'].strftime('%d/%m/%Y')} até {p['data_fim'].strftime('%d/%m/%Y')}")
                        st.write(f"**Abono Pecuniário:** {'Sim' if p['abono_pecuniario'] else 'Não'}")
                        
                        col_a, col_r = st.columns(2)
                        if col_a.button("✅ Aprovar e Notificar RH", key=f"aprov_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            st.success(f"Férias de {p['nome']} aprovadas! O RH e os setores ligados serão avisados.")
                            st.rerun()
                            
                        if col_r.button("❌ Recusar Pedido", key=f"rec_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Recusado' WHERE id_movimento={p['id_movimento']}")
                            st.warning("Solicitação recusada.")
                            st.rerun()

        # 5.2 GERENCIAR EQUIPE
        elif menu == "Gerenciar Equipe":
            st.subheader("Novas Admissões e Controle de Acesso")
            
            # Formulário de Cadastro
            with st.expander("➕ Cadastrar Novo Funcionário"):
                with st.form("cad_novo_func", clear_on_submit=True):
                    n_nome = st.text_input("Nome Completo")
                    n_adm = st.date_input("Data de Admissão")
                    n_setor = st.selectbox("Setor", ["Contabilidade", "RH", "Fiscal"])
                    
                    if st.form_submit_button("Salvar no Banco"):
                        if n_nome:
                            # Limpeza de aspas simples para evitar erro de SQL
                            n_nome_safe = n_nome.replace("'", "''")
                            sql_cad = f"""
                                INSERT INTO rh_funcionarios (nome, data_admissao, setor, is_ativo) 
                                VALUES ('{n_nome_safe}', '{n_adm}', '{n_setor}', True)
                            """
                            query_banco(sql_cad)
                            st.success(f"Funcionário {n_nome} cadastrado com sucesso!")
                            st.rerun()
                        else:
                            st.error("O campo Nome é obrigatório.")

            st.divider()
            st.subheader("Lista de Colaboradores e Ativação de E-mail")
            
            # Lista de funcionários para ativação de email ou desligamento
            for f in funcionarios_db:
                with st.container():
                    c1, c2, c3 = st.columns([2, 2, 1])
                    c1.write(f"**{f['nome']}**")
                    
                    # Logica para cadastrar e-mail (Liberação de acesso)
                    if not f['email_corporativo']:
                        novo_mail = c2.text_input("Cadastrar E-mail Corporativo", key=f"in_mail_{f['id_funcionario']}")
                        if c2.button("Ativar Acesso", key=f"btn_mail_{f['id_funcionario']}"):
                            if "@" in novo_mail:
                                query_banco(f"UPDATE rh_funcionarios SET email_corporativo='{novo_mail}' WHERE id_funcionario={f['id_funcionario']}")
                                st.rerun()
                            else:
                                st.error("E-mail inválido.")
                    else:
                        c2.write(f"📧 {f['email_corporativo']}")
                    
                    # Botão de Desligamento (Inativa o funcionário)
                    if c3.button("Desligar", key=f"del_{f['id_funcionario']}", help="Inativa o funcionário no sistema"):
                        query_banco(f"UPDATE rh_funcionarios SET is_ativo=False WHERE id_funcionario={f['id_funcionario']}")
                        st.rerun()
                    st.divider()

    elif senha_acesso != "":
        st.error("Senha de acesso incorreta. Tente novamente.")
