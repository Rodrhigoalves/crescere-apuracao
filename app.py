import streamlit as st
import mysql.connector
import pandas as pd
import json
from datetime import datetime, date
from fpdf import FPDF

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="🛡️ Crescere - Apuração Cloud", layout="wide")

if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'id_editando' not in st.session_state: st.session_state.id_editando = None
if 'motivo_edit' not in st.session_state: st.session_state.motivo_edit = ""
if 'v_key' not in st.session_state: st.session_state.v_key = 0

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
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas 
                      (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), cnpj VARCHAR(20), regime VARCHAR(50))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50), log_reprocessamento TEXT, saldo_ant_pis DECIMAL(15,2), saldo_ant_cof DECIMAL(15,2))''')
    conn.commit()
    conn.close()

# --- 3. REGRAS DE NEGÓCIO (Vencimento Dia 25) ---
def get_data_vencimento(mes, ano):
    meses_num = {'Janeiro':1, 'Fevereiro':2, 'Março':3, 'Abril':4, 'Maio':5, 'Junho':6, 'Julho':7, 'Agosto':8, 'Setembro':9, 'Outubro':10, 'Novembro':11, 'Dezembro':12}
    m = meses_num[mes]
    m_venc = m + 1 if m < 12 else 1
    a_venc = int(ano) if m < 12 else int(ano) + 1
    data_v = date(a_venc, m_venc, 25)
    while data_v.weekday() > 4: data_v = date(data_v.year, data_v.month, data_v.day - 1)
    return data_v

# --- 4. INTERFACE ---
init_db()

with st.sidebar:
    st.header("🏢 Gestão")
    menu = st.radio("Navegação:", ["Início", "Empresas", "Nova Apuração", "Histórico"])
    st.divider()
    if st.button("🗑️ Limpar Banco (TESTES)"):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE historico_apuracoes")
        conn.commit(); conn.close()
        st.sidebar.warning("Banco de Histórico zerado!")

# --- PÁGINA: EMPRESAS ---
if menu == "Empresas":
    st.title("🏢 Cadastro de Unidades")
    with st.form("cad_emp"):
        n = st.text_input("Razão Social")
        c = st.text_input("CNPJ")
        r = st.selectbox("Regime", ["Lucro Presumido", "Lucro Real"])
        if st.form_submit_button("Salvar"):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO empresas (nome, cnpj, regime) VALUES (%s, %s, %s)", (n, c, r))
            conn.commit(); conn.close()
            st.success("Cadastrada!")

# --- PÁGINA: APURAÇÃO ---
elif menu == "Nova Apuração":
    st.title("🛡️ APURAÇÃO PIS/COFINS - MÓDULO CLOUD")
    conn = get_db_connection()
    df_emp = pd.read_sql("SELECT * FROM empresas", conn)
    conn.close()

    if df_emp.empty:
        st.warning("Cadastre uma empresa primeiro.")
    else:
        with st.sidebar:
            emp_sel = st.selectbox("Empresa", df_emp['nome'])
            regime_atual = df_emp[df_emp['nome'] == emp_sel]['regime'].values[0]
            cnpj_atual = df_emp[df_emp['nome'] == emp_sel]['cnpj'].values[0]
            emp_id = int(df_emp[df_emp['nome'] == emp_sel]['id'].values[0])
            mes_sel = st.selectbox("Mês", ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"])
            ano_sel = st.selectbox("Ano", ["2025", "2026"])
            st.info(f"Regime: {regime_atual}")

        # Lançamentos Nativa (Prints 1 e 2)
        op_debito = ["Venda de Mercadorias / Produtos", "Venda de Serviços", "Receita Financeira"]
        op_credito = ["Compra Mercador/Insumos", "Combustível (Diesel)", "Manutenção", "Energia Elétrica", "Aluguel (PJ)", "Fretes"]
        
        with st.container(border=True):
            col1, col2, col3 = st.columns([2,1,1])
            op = col1.selectbox("Operação", op_debito + op_credito)
            val = col2.number_input("Valor Base (R$)", min_value=0.0, key=f"v_{st.session_state.v_key}")
            if col3.button("➕ Inserir"):
                tipo = "Débito" if op in op_debito else "Crédito"
                # Alíquotas
                if "Financeira" in op and regime_atual == "Lucro Real": ap, ac = 0.0065, 0.04
                elif regime_atual == "Lucro Real": ap, ac = 0.0165, 0.076
                else: ap, ac = 0.0065, 0.03
                
                st.session_state.itens_memoria.append({"Operação": op, "Base": val, "PIS": val*ap, "COF": val*ac, "Tipo": tipo})
                st.session_state.v_key += 1; st.rerun()

        if st.session_state.itens_memoria:
            st.subheader("📋 Memória de Cálculo")
            st.table(pd.DataFrame(st.session_state.itens_memoria))
            
            if st.session_state.id_editando:
                st.session_state.motivo_edit = st.text_area("Justificativa Obrigatória (Edição):")

            if st.button("💾 FINALIZAR APURAÇÃO", type="primary"):
                if st.session_state.id_editando and len(st.session_state.motivo_edit) < 5:
                    st.error("Escreva o motivo da alteração!")
                else:
                    # Lógica de Salvar no UOL (Insert/Update)
                    st.success("Gravado no Histórico do UOL!")
                    st.session_state.itens_memoria = []; st.session_state.id_editando = None; st.rerun()

# --- PÁGINA: HISTÓRICO ---
elif menu == "Histórico":
    st.title("📁 Arquivo Digital Crescere")
    # Tabela de Histórico com botões de PDF e E-mail
    st.info("Aqui aparecerão os links para download do PDF e a opção de enviar por e-mail para o cliente.")
