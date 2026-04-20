import streamlit as st
import sys
import os
import datetime
import pandas as pd

# 1. AJUSTE DE CAMINHO E IMPORTAÇÃO
# Adiciona a raiz para localizar database.py e mailer.py
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from database import query_banco
    from mailer import enviar_email
except Exception as e:
    st.error(f"Erro crítico de importação: {e}")
    st.stop()

def get_remote_ip():
    try:
        return st.context.headers.get("X-Forwarded-For", "127.0.0.1").split(',')[0]
    except:
        return "127.0.0.1"

st.set_page_config(page_title="Gestão de Férias", layout="wide")
current_ip = get_remote_ip()
setores_lista = ["CONTÁBIL", "D.P", "FISCAL"]

st.title("📅 Sistema Estratégico de Férias")

# 2. CARREGAMENTO DE DADOS
try:
    funcionarios_db = query_banco("SELECT * FROM rh_funcionarios WHERE is_ativo = True ORDER BY nome ASC")
except Exception as e:
    st.error(f"Erro ao carregar banco: {e}")
    st.stop()

tab_func, tab_lider = st.tabs(["👤 Espaço do Funcionário", "🔒 Área Restrita (Líder)"])

# --- 3. ESPAÇO DO FUNCIONÁRIO ---
with tab_func:
    if not funcionarios_db:
        st.info("Nenhum funcionário cadastrado.")
    else:
        nomes = [f['nome'] for f in funcionarios_db]
        selecionado = st.selectbox("Selecione seu nome:", [""] + nomes, key="sel_func")

        if selecionado:
            user = next(item for item in funcionarios_db if item["nome"] == selecionado)
            
            if not user['ip_maquina']:
                st.warning("⚠️ PRIMEIRO ACESSO: Vincule sua máquina para prosseguir.")
                if st.button("Confirmar Identidade e Vincular Máquina"):
                    query_banco(f"UPDATE rh_funcionarios SET ip_maquina='{current_ip}' WHERE id_funcionario={user['id_funcionario']}")
                    st.rerun()
            elif user['ip_maquina'] != current_ip:
                st.error("🚫 Acesso Negado: Esta máquina não é sua estação autorizada.")
            else:
                st.success(f"Conectado: {selecionado}")
                sub_tab1, sub_tab2 = st.tabs(["📝 Nova Solicitação", "🔄 Reagendar Férias"])
                
                with sub_tab1:
                    with st.form("form_nova"):
                        c1, c2 = st.columns(2)
                        d_ini = c1.date_input("Início")
                        d_fim = c2.date_input("Fim")
                        if st.form_submit_button("Enviar Solicitação"):
                            dias = (d_fim - d_ini).days + 1
                            if dias < 5:
                                st.error("Mínimo de 5 dias.")
                            else:
                                query_banco(f"INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos, status) VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {dias}, 'Pendente')")
                                st.success("Enviado com sucesso!")

# --- 4. ÁREA RESTRITA (LÍDER) ---
with tab_lider:
    col_s1, col_s2 = st.columns(2)
    senha_lider = col_s1.text_input("Senha:", type="password")
    setor_trabalho = col_s2.selectbox("Setor:", setores_lista)
    
    if senha_lider == "123":
        funcs_setor = [f for f in funcionarios_db if f['setor'] == setor_trabalho]
        menu = st.sidebar.radio("Menu:", ["Aprovações", "Dossiê", "Equipe"])

        if menu == "Aprovações":
            pendentes = query_banco(f"SELECT f.nome, f.email_corporativo, m.* FROM rh_movimentacao_ferias m JOIN rh_funcionarios f ON m.id_funcionario = f.id_funcionario WHERE m.status IN ('Pendente', 'Reagendamento Solicitado') AND f.setor = '{setor_trabalho}'")
            
            if not pendentes:
                st.info("Nada pendente.")
            else:
                for p in pendentes:
                    with st.expander(f"{p['nome']} - {p['dias_corridos']} dias"):
                        ca, cr = st.columns(2)
                        if ca.button("✅ Aprovar", key=f"ap_{p['id_movimento']}"):
                            query_banco(f"UPDATE rh_movimentacao_ferias SET status='Aprovado' WHERE id_movimento={p['id_movimento']}")
                            
                            # Lógica de E-mail
                            config = query_banco(f"SELECT * FROM rh_configuracoes_setores WHERE setor = '{setor_trabalho}'")
                            if config:
                                c = config[0]
                                periodo = f"{p['data_inicio']} a {p['data_fim']}"
                                enviar_email(p['email_corporativo'], "Férias Aprovadas", f"Suas férias foram aprovadas: {periodo}")
                                enviar_email(c['email_rh'], "Aviso de Férias", f"O funcionário {p['nome']} sairá de {periodo}")
                            
                            st.success("Aprovado!")
                            st.rerun()
