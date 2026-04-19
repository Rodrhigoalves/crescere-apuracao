import streamlit as st
import sys
import os

# Garante que o Python ache o database.py na pasta de cima
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database import query_banco

st.set_page_config(page_title="Gestão de Férias", layout="wide")

st.title("📅 Sistema de Gestão de Férias")

# --- INTERFACE DE SELEÇÃO (Para teste, depois você pode integrar com login) ---
# Aqui listamos os funcionários ativos para simular o acesso
funcionarios_db = query_banco("SELECT id_funcionario, nome FROM rh_funcionarios WHERE is_ativo = True")
lista_nomes = [f['nome'] for f in funcionarios_db]

if not lista_nomes:
    st.info("Nenhum funcionário cadastrado. O líder precisa cadastrar o primeiro colaborador.")
    # Aqui entraria o botão para o líder cadastrar (ver abaixo)
else:
    usuario_atual = st.sidebar.selectbox("Acessar como:", lista_nomes)
    
    # Busca dados do usuário selecionado
    user_data = query_banco(f"SELECT * FROM rh_funcionarios WHERE nome = '{usuario_atual}'")[0]

    # --- REGRA 1: BLOQUEIO POR FALTA DE E-MAIL ---
    if user_data['email_corporativo'] is None:
        st.error(f"Olá {usuario_atual}, seu acesso está bloqueado.")
        st.warning("Motivo: E-mail corporativo não identificado. Solicite ao seu líder a regularização.")
    else:
        tabs = st.tabs(["Minhas Férias", "Painel do Líder (Aprovações)", "Administração (RH)"])

        # --- ABA 1: SOLICITAÇÃO DO FUNCIONÁRIO ---
        with tabs[0]:
            st.subheader("Solicitar Novo Período")
            col1, col2 = st.columns(2)
            with col1:
                data_ini = st.date_input("Data de Início")
            with col2:
                data_fim = st.date_input("Data de Retorno")
            
            if st.button("Enviar Solicitação para o Líder"):
                # Aqui entra a lógica de salvar no banco e disparar e-mail para o lider
                dias = (data_fim - data_ini).days
                query_banco(f"""
                    INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos)
                    VALUES ({user_data['id_funcionario']}, '{data_ini}', '{data_fim}', {dias})
                """)
                st.success("Solicitação enviada! Seu líder recebeu um alerta por e-mail.")

        # --- ABA 2: PAINEL DO LÍDER (Somente se for líder) ---
        with tabs[1]:
            st.subheader("Pendentes de Aprovação")
            # Lista solicitações pendentes
            pendentes = query_banco("""
                SELECT f.nome, m.* FROM rh_movimentacao_ferias m 
                JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario
                WHERE m.status = 'Pendente'
            """)
            if pendentes:
                for p in pendentes:
                    st.write(f"**{p['nome']}** solicita {p['dias_corridos']} dias (Início: {p['data_inicio']})")
                    col_ap, col_re = st.columns(5)
                    if col_ap.button("Confirmar", key=f"ap_{p['id_movimento']}"):
                        # Lógica de aprovação + E-mail para RH e Lista Estratégica
                        st.success("Aprovado! E-mails disparados para o RH e setores parceiros.")
            else:
                st.info("Não há solicitações pendentes.")

        # --- ABA 3: CADASTRO E DESLIGAMENTO ---
        with tabs[2]:
            st.subheader("Gestão de Equipe")
            with st.expander("Cadastrar Novo Funcionário"):
                novo_nome = st.text_input("Nome Completo")
                nova_adm = st.date_input("Data de Admissão", key="nova_adm")
                # E-mail corporativo fica vazio por padrão conforme sua regra
                if st.button("Salvar Cadastro"):
                    query_banco(f"INSERT INTO rh_funcionarios (nome, data_admissao, is_ativo) VALUES ('{novo_nome}', '{nova_adm}', True)")
                    st.balloons()
