import streamlit as st
import mysql.connector
import pandas as pd
import json
from datetime import datetime, date
from fpdf import FPDF

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="🛡️ Crescere - Apuração Cloud", layout="wide")

# Inicialização de Memória
if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'id_editando' not in st.session_state: st.session_state.id_editando = None
if 'v_key' not in st.session_state: st.session_state.v_key = 0

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. CONEXÃO UOL (MYSQL) ---
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
    # Tabela de Histórico
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa VARCHAR(255), cnpj VARCHAR(20), competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), data_reg VARCHAR(50), 
        log_reprocessamento TEXT, saldo_ant_pis DECIMAL(15,2), saldo_ant_cof DECIMAL(15,2),
        saldo_credor_pis_final DECIMAL(15,2), saldo_credor_cof_final DECIMAL(15,2))''')
    # Tabela de Operações Customizadas
    cursor.execute('''CREATE TABLE IF NOT EXISTS operacoes_customizadas (
        id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(100) UNIQUE, tipo VARCHAR(20))''')
    conn.commit()
    conn.close()

# --- 3. LÓGICA DE VENCIMENTO ---
def get_data_vencimento(mes, ano):
    meses_num = {'Janeiro':1, 'Fevereiro':2, 'Março':3, 'Abril':4, 'Maio':5, 'Junho':6, 'Julho':7, 'Agosto':8, 'Setembro':9, 'Outubro':10, 'Novembro':11, 'Dezembro':12}
    m = meses_num[mes]
    m_venc = m + 1 if m < 12 else 1
    a_venc = int(ano) if m < 12 else int(ano) + 1
    data_v = date(a_venc, m_venc, 25)
    while data_v.weekday() > 4: data_v = date(data_v.year, data_v.month, data_v.day - 1)
    return data_v

# --- 4. MOTOR DE PDF ---
def gerar_pdf_apuracao(dados, empresa, cnpj, competencia, s_ant_p, s_ant_c):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(190, 10, "DEMONSTRATIVO DE APURAÇÃO - CRESCERE", ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(190, 7, f"Empresa: {empresa} | CNPJ: {cnpj} | Competência: {competencia}", ln=True, align='C')
    pdf.ln(10)
    # (O restante da lógica de PDF permanece igual à de ontem...)
    return pdf.output(dest='S').encode('latin-1')

# --- 5. INTERFACE ---
init_db()

with st.sidebar:
    st.title("🏢 Configuração")
    emp_n = st.text_input("Razão Social", "Minha Empresa LTDA")
    emp_c = st.text_input("CNPJ", "00.000.000/0001-00")
    mes_sel = st.selectbox('Mês', ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'], index=2)
    ano_sel = st.selectbox('Ano', ['2025', '2026'], index=1)
    st.divider()
    regime = st.radio("Regime Tributário:", ["Lucro Presumido", "Lucro Real"])

st.title("🛡️ Crescere - Módulo de Apuração UOL")

# --- 6. LANÇAMENTOS ---
with st.container(border=True):
    st.markdown("**📥 Inserir Dados**")
    c1, c2, c3 = st.columns([2, 1, 1])
    op_base = ["Venda de Mercadorias", "Receita Financeira", "Compra Insumos", "Energia"]
    sel = c1.selectbox("Natureza da Operação", op_base)
    val = c2.number_input("Base de Cálculo (R$)", min_value=0.0, key=f"v_{st.session_state.v_key}")
    
    if c3.button("➕ Adicionar", use_container_width=True):
        tipo = "Débito" if "Venda" in sel or "Receita" in sel else "Crédito"
        # Lógica de Alíquota Dinâmica
        if "Financeira" in sel and regime == "Lucro Real":
            ap, ac = 0.0065, 0.04
        elif regime == "Lucro Real":
            ap, ac = 0.0165, 0.076
        else:
            ap, ac = 0.0065, 0.03
            
        st.session_state.itens_memoria.append({
            "Unidade": "Matriz", "Operação": sel, "Base": val, 
            "PIS": val*ap, "COF": val*ac, "Tipo": tipo
        })
        st.session_state.v_key += 1
        st.rerun()

# --- 7. EXIBIÇÃO E SALVAMENTO ---
if st.session_state.itens_memoria:
    st.subheader("📋 Itens na Memória")
    df_mem = pd.DataFrame(st.session_state.itens_memoria)
    st.table(df_mem.style.format({"Base": "{:.2f}", "PIS": "{:.2f}", "COF": "{:.2f}"}))
    
    if st.button("💾 FINALIZAR E GRAVAR NO UOL", type="primary"):
        js = json.dumps(st.session_state.itens_memoria)
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        query = """INSERT INTO historico_apuracoes 
                   (empresa, cnpj, competencia, detalhamento_json, data_reg, log_reprocessamento) 
                   VALUES (%s, %s, %s, %s, %s, %s)"""
        cursor.execute(query, (emp_n, emp_c, f"{mes_sel}/{ano_sel}", js, agora, f"Apurado via Cloud em {agora}"))
        conn.commit()
        conn.close()
        
        st.session_state.itens_memoria = []
        st.success("✅ Apuração salva com sucesso no banco UOL!")
        st.rerun()

# --- 8. HISTÓRICO ---
st.divider()
st.subheader("📁 Histórico de Apurações (UOL)")
try:
    conn = get_db_connection()
    df_h = pd.read_sql("SELECT id, empresa, competencia, data_reg FROM historico_apuracoes ORDER BY id DESC", conn)
    conn.close()
    st.dataframe(df_h, use_container_width=True)
except:
    st.info("Nenhuma apuração encontrada no banco.")
