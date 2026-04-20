import streamlit as st
import sys
import os
import datetime
import pandas as pd

# 1. CONEXÃO E INFRAESTRUTURA
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
try:
    from database import query_banco
    from mailer import enviar_email
except ImportError:
    st.error("Erro: Arquivos 'database.py' ou 'mailer.py' não encontrados.")
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
                st.success(f"Conectado: {selecionado} (Setor: {user['setor']})")
                sub_tab1, sub_tab2 = st.tabs(["📝 Nova Solicitação", "🔄 Reagendar Férias"])
                
                with sub_tab1:
                    with st.form("form_nova"):
                        c1, c2 = st.columns(2)
                        d_ini = c1.date_input("Início das Férias")
                        d_fim = c2.date_input("Último dia de Descanso")
                        abono = st.checkbox("Vender 10 dias (Abono)") if user.get('pode_vender_ferias') else False
                        
                        if st.form_submit_button("Enviar Solicitação"):
                            dias = (d_fim - d_ini).days + 1
                            if dias < 5: 
                                st.error("O período mínimo deve ser de 5 dias.")
                            else:
                                query_banco(f"INSERT INTO rh_movimentacao_ferias (id_funcionario, data_inicio, data_fim, dias_corridos, abono_pecuniario, ip_registro, status) VALUES ({user['id_funcionario']}, '{d_ini}', '{d_fim}', {dias}, {abono}, '{current_ip}', 'Pendente')")
                                st.success("Solicitação enviada! O líder será notificado.")

                with sub_tab2:
                    aprovados = query_banco(f"SELECT * FROM rh_movimentacao_ferias WHERE id_funcionario={user['id_funcionario']} AND status='Aprovado'")
                    if not aprovados:
                        st.info("Você não possui férias aprovadas para reagendar.")
                    else:
                        for f_ap in aprovados:
                            with st.expander(f"Férias aprovadas para {f_ap['data_inicio'].strftime('%d/%m/%Y')}"):
                                motivo_re = st.text_area("Justifique o motivo do reagendamento:", key=f"mot_re_{f_ap['id_movimento']}")
                                if st.button("Solicitar Alteração", key=f"btn_re_{f_ap['id_mov
