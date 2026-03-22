import streamlit as st
import mysql.connector
import pandas as pd
import json
import requests
from datetime import datetime
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES E ESTADOS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide")

if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'v_key' not in st.session_state: st.session_state.v_key = 0
if 'dados_cnpj' not in st.session_state: st.session_state.dados_cnpj = {}

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. INICIALIZAÇÃO DO BANCO ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas (
        id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), fantasia VARCHAR(255), 
        cnpj VARCHAR(20), regime VARCHAR(50), tipo VARCHAR(20), matriz_id INT,
        cnae VARCHAR(255), endereco TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50), log_reprocessamento TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- 3. MOTOR DE PDF ---
def gerar_pdf_crescere(emp_info, itens, comp):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "DEMONSTRATIVO DE APURAÇÃO PIS/COFINS", ln=True, align='C')
    pdf.set_font("Arial", '', 9)
    pdf.cell(190, 5, f"Empresa: {emp_info['nome']} | CNPJ: {emp_info['cnpj']}", ln=True, align='C')
    pdf.cell(190, 5, f"CNAE: {emp_info['cnae']}", ln=True, align='C')
    pdf.line(10, 35, 200, 35)
    pdf.ln(10)
    
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(40, 8, "Operação", 1); pdf.cell(50, 8, "Base", 1); pdf.cell(50, 8, "PIS", 1); pdf.cell(50, 8, "COFINS", 1, ln=True)
    pdf.set_font("Arial", '', 9)
    for i in itens:
        pdf.cell(40, 7, i['Operação'], 1)
        pdf.cell(50, 7, formata_real(i['Base']), 1)
        pdf.cell(50, 7, formata_real(i['PIS']), 1)
        pdf.cell(50, 7, formata_real(i['COF']), 1, ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- 4. INTERFACE ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Módulos", ["Início", "Cadastro de Unidades", "Apuração Mensal", "Relatórios e ERP"])
    st.divider()
    if st.button("🗑️ Limpar Testes"):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE historico_apuracoes")
        conn.commit(); conn.close(); st.rerun()

# --- CADASTRO ---
if menu == "Cadastro de Unidades":
    st.header("🏢 Cadastro de Unidades")
    c1, c2 = st.columns([3, 1])
    cnpj_in = c1.text_input("CNPJ (apenas números)")
    if c2.button("🔍 Consultar"):
        res = requests.get(f"https://receitaws.com.br/v1/cnpj/{cnpj_in.replace('.','')}").json()
        if res.get('status') != 'ERROR': st.session_state.dados_cnpj = res; st.rerun()

    with st.form("f_cad"):
        d = st.session_state.dados_cnpj
        nome = st.text_input("Razão Social", value=d.get('nome', ''))
        cnpj = st.text_input("CNPJ Confirmado", value=d.get('cnpj', cnpj_in))
        reg = st.selectbox("Regime", ["Lucro Real", "Lucro Presumido"])
        tipo = st.selectbox("Tipo", ["Matriz", "Filial"])
        cnae = st.text_input("CNAE", value=f"{d.get('atividade_principal', [{}])[0].get('code', '')}")
        end = st.text_area("Endereço", value=f"{d.get('logradouro', '')}, {d.get('numero', '')}")
        
        if st.form_submit_button("Salvar"):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO empresas (nome, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s)", (nome, cnpj, reg, tipo, cnae, end))
            conn.commit(); conn.close(); st.success("Salvo!"); st.session_state.dados_cnpj = {}; st.rerun()

# --- APURAÇÃO ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos")
    conn = get_db_connection(); df_e = pd.read_sql("SELECT * FROM empresas", conn); conn.close()
    if not df_e.empty:
        emp_sel = st.selectbox("Empresa", df_e['nome'])
        dados_e = df_e[df_e['nome'] == emp_sel].iloc[0]
        with st.container(border=True):
            col1, col2, col3 = st.columns([2,1,1])
            op = col1.selectbox("Operação", ["Venda Mercadorias", "Receita Financeira", "Compra Insumos"])
            val = col2.number_input("Valor R$", min_value=0.0, key=f"v_{st.session_state.v_key}")
            if col3.button("➕ Inserir"):
                al_p, al_c = (0.0065, 0.04) if "Financeira" in op else (0.0165, 0.076) if dados_e['regime']=="Lucro Real" else (0.0065, 0.03)
                st.session_state.itens_memoria.append({"Operação": op, "Base": val, "PIS": val*al_p, "COF": val*al_c})
                st.session_state.v_key += 1; st.rerun()
        
        if st.session_state.itens_memoria:
            st.table(pd.DataFrame(st.session_state.itens_memoria))
            if st.button("💾 Gravar"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO historico_apuracoes (empresa_id, competencia, detalhamento_json) VALUES (%s,%s,%s)", (int(dados_e['id']), "03/2026", json.dumps(st.session_state.itens_memoria)))
                conn.commit(); conn.close(); st.session_state.itens_memoria = []; st.success("Gravado!"); st.rerun()

# --- RELATÓRIOS ---
elif menu == "Relatórios e ERP":
    st.header("📊 Histórico e ERP")
    conn = get_db_connection()
    df_h = pd.read_sql("SELECT h.*, e.nome, e.cnpj, e.cnae, e.endereco, e.fantasia, e.regime FROM historico_apuracoes h JOIN empresas e ON h.empresa_id = e.id", conn)
    conn.close()
    for _, r in df_h.iterrows():
        with st.expander(f"{r['nome']} - {r['competencia']}"):
            itens = json.loads(r['detalhamento_json'])
            pdf = gerar_pdf_crescere(r, itens, r['competencia'])
            st.download_button(f"📄 PDF ID {r['id']}", pdf, f"Apuracao_{r['id']}.pdf")
            st.download_button(f"💾 CSV ERP", pd.DataFrame(itens).to_csv().encode('utf-8'), f"ERP_{r['id']}.csv")

else:
    st.title("🛡️ Sistema Crescere")
    st.info("Selecione um módulo para começar.")
