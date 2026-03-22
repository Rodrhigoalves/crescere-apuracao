import streamlit as st
import mysql.connector
import pandas as pd
import json
import requests
from datetime import datetime
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide")

if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'v_key' not in st.session_state: st.session_state.v_key = 0
if 'dados_cnpj' not in st.session_state: st.session_state.dados_cnpj = {}

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. INICIALIZAÇÃO BLINDADA DO BANCO ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Tabela de Empresas
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas (
        id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), fantasia VARCHAR(255), 
        cnpj VARCHAR(20), regime VARCHAR(50), tipo VARCHAR(20), matriz_id INT,
        cnae VARCHAR(255), endereco TEXT)''')
    
    # Tabela de Histórico (Garantindo PIS e COFINS TOTAL)
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50))''')

    # Checagem de colunas extras para evitar o erro de "ProgrammingError"
    colunas_historico = [("pis_total", "DECIMAL(15,2)"), ("cofins_total", "DECIMAL(15,2)")]
    for col, tipo in colunas_historico:
        try:
            cursor.execute(f"ALTER TABLE historico_apuracoes ADD COLUMN {col} {tipo}")
        except: pass

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
    pdf.cell(50, 8, "Operação", 1); pdf.cell(45, 8, "Base", 1); pdf.cell(45, 8, "PIS", 1); pdf.cell(50, 8, "COFINS", 1, ln=True)
    pdf.set_font("Arial", '', 9)
    for i in itens:
        pdf.cell(50, 7, i['Operação'], 1)
        pdf.cell(45, 7, formata_real(i['Base']), 1)
        pdf.cell(45, 7, formata_real(i['PIS']), 1)
        pdf.cell(50, 7, formata_real(i['COF']), 1, ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- 4. INTERFACE ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Módulos", ["Início", "Cadastro de Unidades", "Apuração Mensal", "Relatórios e ERP"])
    st.divider()
    if st.button("🗑️ Resetar Histórico"):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE historico_apuracoes")
        conn.commit(); conn.close(); st.rerun()

# --- CADASTRO ---
if menu == "Cadastro de Unidades":
    st.header("🏢 Cadastro de Unidades")
    c1, c2 = st.columns([3, 1])
    cnpj_in = c1.text_input("CNPJ (apenas números)")
    if c2.button("🔍 Consultar Receita"):
        limpo = cnpj_in.replace(".","").replace("/","").replace("-","")
        res = requests.get(f"https://receitaws.com.br/v1/cnpj/{limpo}").json()
        if res.get('status') != 'ERROR': 
            st.session_state.dados_cnpj = res
            st.success("Dados carregados!")
        else: st.error("Erro na consulta. Aguarde 1 min.")

    with st.form("f_cad"):
        d = st.session_state.dados_cnpj
        nome = st.text_input("Razão Social", value=d.get('nome', ''))
        fanta = st.text_input("Nome Fantasia", value=d.get('fantasia', ''))
        cnpj = st.text_input("CNPJ Confirmado", value=d.get('cnpj', cnpj_in))
        reg = st.selectbox("Regime", ["Lucro Real", "Lucro Presumido"])
        tipo = st.selectbox("Tipo", ["Matriz", "Filial"])
        cnae_v = f"{d['atividade_principal'][0].get('code', '')}" if 'atividade_principal' in d else ""
        cnae = st.text_input("CNAE", value=cnae_v)
        end = st.text_area("Endereço", value=f"{d.get('logradouro', '')}, {d.get('numero', '')}")
        
        if st.form_submit_button("💾 Salvar Unidade"):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)", 
                           (nome, fanta, cnpj, reg, tipo, cnae, end))
            conn.commit(); conn.close(); st.success("Salvo!"); st.session_state.dados_cnpj = {}; st.rerun()

# --- APURAÇÃO ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos Mensais")
    conn = get_db_connection(); df_e = pd.read_sql("SELECT * FROM empresas", conn); conn.close()
    
    if not df_e.empty:
        col_e, col_m, col_a = st.columns([2,1,1])
        emp_sel = col_e.selectbox("Empresa", df_e['nome'])
        comp = f"{col_m.selectbox('Mês', ['01','02','03','04','05','06','07','08','09','10','11','12'])}/{col_a.selectbox('Ano', ['2025','2026'])}"
        dados_e = df_e[df_e['nome'] == emp_sel].iloc[0]

        with st.container(border=True):
            st.subheader("Nova Operação")
            c1, c2, c3 = st.columns([2, 1, 1])
            op_list = ["Venda Mercadorias", "Receita Financeira", "Compra Insumos", "Energia", "Aluguel PJ", "Fretes"]
            op = c1.selectbox("Natureza", op_list)
            val = c2.number_input("Valor Base R$", min_value=0.0, step=100.0, key=f"v_{st.session_state.v_key}")
            if c3.button("➕ Adicionar Item"):
                if val > 0:
                    # Lógica de alíquotas
                    if "Financeira" in op and dados_e['regime'] == "Lucro Real": al_p, al_c = 0.0065, 0.04
                    elif dados_e['regime'] == "Lucro Real": al_p, al_c = 0.0165, 0.076
                    else: al_p, al_c = 0.0065, 0.03
                    
                    st.session_state.itens_memoria.append({"Operação": op, "Base": val, "PIS": val*al_p, "COF": val*al_c})
                    st.session_state.v_key += 1; st.rerun()
                else: st.warning("Digite um valor maior que zero.")

        if st.session_state.itens_memoria:
            st.subheader("📋 Resumo Temporário")
            df_temp = pd.DataFrame(st.session_state.itens_memoria)
            st.table(df_temp.style.format({"Base": "{:.2f}", "PIS": "{:.2f}", "COF": "{:.2f}"}))
            
            p_total = df_temp['PIS'].sum()
            c_total = df_temp['COF'].sum()
            
            if st.button(f"🏁 Finalizar e Gravar Apuração ({comp})"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO historico_apuracoes (empresa_id, competencia, detalhamento_json, pis_total, cofins_total, data_reg) VALUES (%s,%s,%s,%s,%s,%s)", 
                               (int(dados_e['id']), comp, json.dumps(st.session_state.itens_memoria), float(p_total), float(c_total), datetime.now().strftime("%d/%m/%Y %H:%M")))
                conn.commit(); conn.close(); st.session_state.itens_memoria = []; st.success("Apuração salva com sucesso!"); st.rerun()

# --- RELATÓRIOS ---
elif menu == "Relatórios e ERP":
    st.header("📊 Histórico de Apurações")
    conn = get_db_connection()
    df_h = pd.read_sql("SELECT h.*, e.nome, e.cnpj, e.cnae, e.endereco, e.regime, e.fantasia FROM historico_apuracoes h JOIN empresas e ON h.empresa_id = e.id ORDER BY h.id DESC", conn)
    conn.close()
    
    if df_h.empty: st.info("Nenhum dado no histórico.")
    for _, r in df_h.iterrows():
        with st.expander(f"{r['nome']} - Comp: {r['competencia']} - Total: {formata_real(r['pis_total']+r['cofins_total'])}"):
            itens = json.loads(r['detalhamento_json'])
            col1, col2 = st.columns(2)
            pdf = gerar_pdf_crescere(r, itens, r['competencia'])
            col1.download_button(f"📄 Baixar PDF", pdf, f"Apuracao_{r['id']}.pdf")
            col2.download_button(f"💾 Exportar CSV (ERP)", pd.DataFrame(itens).to_csv(index=False).encode('utf-8'), f"ERP_{r['id']}.csv")

else:
    st.title("🛡️ Sistema Crescere")
    st.info("Pronto para uso, Rodrigo. Selecione um módulo no menu lateral.")
