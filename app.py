import streamlit as st
import mysql.connector
import pandas as pd
import json
from datetime import datetime, date
from fpdf import FPDF

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="🛡️ Crescere - Apuração Cloud", layout="wide")

# Inicialização de Session State
for key in ['itens_memoria', 'id_editando', 'v_key']:
    if key not in st.session_state: st.session_state[key] = [] if 'itens' in key else 0

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. CONEXÃO UOL ---
def get_db_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Tabela de Empresas (Adicionado regime e matriz_id)
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas 
                      (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), cnpj VARCHAR(20), 
                       regime VARCHAR(50), tipo VARCHAR(20), matriz_id INT)''')
    # Tabela de Histórico
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50), log_reprocessamento TEXT, saldo_ant_pis DECIMAL(15,2), saldo_ant_cof DECIMAL(15,2))''')
    conn.commit()
    conn.close()

init_db()

# --- 3. MENU LATERAL ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Navegação", ["Início", "Cadastrar Empresa", "Nova Apuração", "Histórico"])
    st.divider()
    if st.button("⚠️ Limpar Testes (Zerar Banco)"):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE historico_apuracoes")
        conn.commit(); conn.close()
        st.rerun()

# --- PÁGINA: CADASTRO ---
if menu == "Cadastrar Empresa":
    st.header("🏢 Cadastro de Unidades")
    conn = get_db_connection()
    matrizes = pd.read_sql("SELECT id, nome FROM empresas WHERE tipo = 'Matriz'", conn)
    conn.close()

    with st.form("form_emp"):
        col1, col2 = st.columns(2)
        nome = col1.text_input("Razão Social / Nome Unidade")
        cnpj = col2.text_input("CNPJ (completo)")
        tipo = st.selectbox("Esta unidade é:", ["Matriz", "Filial"])
        regime = st.selectbox("Regime Tributário", ["Lucro Presumido", "Lucro Real"])
        
        m_id = None
        if tipo == "Filial" and not matrizes.empty:
            m_id_sel = st.selectbox("Vincular à Matriz:", matrizes['nome'])
            m_id = int(matrizes[matrizes['nome'] == m_id_sel]['id'].values[0])
        
        if st.form_submit_button("💾 Salvar Unidade"):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO empresas (nome, cnpj, regime, tipo, matriz_id) VALUES (%s, %s, %s, %s, %s)", 
                           (nome, cnpj, regime, tipo, m_id))
            conn.commit(); conn.close()
            st.success("Unidade cadastrada com sucesso!")

# --- PÁGINA: APURAÇÃO ---
elif menu == "Nova Apuração":
    st.header("💰 Lançamentos Mensais")
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT * FROM empresas", conn)
    conn.close()

    if df_e.empty:
        st.warning("Cadastre a Matriz primeiro.")
    else:
        with st.expander("📌 Configuração da Apuração", expanded=True):
            col_e, col_m, col_a = st.columns([2,1,1])
            emp_sel = col_e.selectbox("Selecione a Unidade (Matriz ou Filial)", df_e['nome'])
            mes = col_m.selectbox("Mês", ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"])
            ano = col_a.selectbox("Ano", ["2025", "2026"])
            
            dados_emp = df_e[df_e['nome'] == emp_sel].iloc[0]
            st.caption(f"CNPJ: {dados_emp['cnpj']} | Regime: {dados_emp['regime']}")

        # OPERAÇÕES NATIVAS (Seus Prints)
        op_d = ["Venda de Mercadorias", "Receita Financeira", "Serviços Prestados"]
        op_c = ["Compra Insumos", "Energia", "Diesel/Combustível", "Aluguel PJ", "Fretes"]

        with st.container(border=True):
            c1, c2, c3 = st.columns([2,1,1])
            operacao = c1.selectbox("Natureza da Operação", op_d + op_c)
            valor = c2.number_input("Valor Base (R$)", min_value=0.0, key=f"v_{st.session_state.v_key}")
            if c3.button("➕ Adicionar"):
                tipo_op = "Débito" if operacao in op_d else "Crédito"
                # Regra de Alíquota
                if "Financeira" in operacao and dados_emp['regime'] == "Lucro Real":
                    al_p, al_c = 0.0065, 0.04
                elif dados_emp['regime'] == "Lucro Real":
                    al_p, al_c = 0.0165, 0.076
                else:
                    al_p, al_c = 0.0065, 0.03
                
                st.session_state.itens_memoria.append({
                    "Unidade": emp_sel, "Operação": operacao, "Base": valor, 
                    "PIS": valor * al_p, "COF": valor * al_c, "Tipo": tipo_op
                })
                st.session_state.v_key += 1; st.rerun()

        if st.session_state.itens_memoria:
            st.subheader("📋 Resumo da Memória")
            st.dataframe(pd.DataFrame(st.session_state.itens_memoria), use_container_width=True)
            
            if st.button("💾 FINALIZAR E GRAVAR NO UOL"):
                js = json.dumps(st.session_state.itens_memoria)
                agora = datetime.now().strftime("%d/%m/%Y %H:%M")
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO historico_apuracoes (empresa_id, competencia, detalhamento_json, data_reg) VALUES (%s, %s, %s, %s)", 
                               (int(dados_emp['id']), f"{mes}/{ano}", js, agora))
                conn.commit(); conn.close()
                st.session_state.itens_memoria = []
                st.success("Apuração salva!")

# --- PÁGINA: INÍCIO ---
else:
    st.title("🛡️ Sistema de Apuração Crescere")
    st.info("Selecione uma opção no menu lateral para começar.")
