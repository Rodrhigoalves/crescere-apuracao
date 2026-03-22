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

# --- 2. BANCO DE DADOS (UOL) ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Criar tabela de empresas se não existir
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas 
                      (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), cnpj VARCHAR(20))''')
    
    # Criar tabela de histórico se não existir
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50), log_reprocessamento TEXT)''')

    # MANDAR O COMANDO DE ALERTA PARA CADA COLUNA (Garante que o banco se atualize)
    colunas_novas = [
        ("fantasia", "VARCHAR(255)"),
        ("regime", "VARCHAR(50)"),
        ("tipo", "VARCHAR(20)"),
        ("matriz_id", "INT"),
        ("cnae", "VARCHAR(255)"),
        ("endereco", "TEXT")
    ]
    
    for col, tipo in colunas_novas:
        try:
            cursor.execute(f"ALTER TABLE empresas ADD COLUMN {col} {tipo}")
        except:
            pass # Se a coluna já existir, ele pula
            
    conn.commit()
    conn.close()

# Executa a inicialização logo de cara
init_db()

# --- 3. CONSULTA CNPJ (ReceitaWS) ---
def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

# --- 4. INTERFACE ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Módulos", ["Início", "Cadastro de Unidades", "Apuração Mensal", "Relatórios/PDF"])

# --- MÓDULO: CADASTRO ---
if menu == "Cadastro de Unidades":
    st.header("🏢 Cadastro de Unidades")
    
    with st.container(border=True):
        col_c, col_b = st.columns([3, 1])
        cnpj_input = col_c.text_input("CNPJ (apenas números)")
        if col_b.button("🔍 Consultar Receita"):
            limpo = cnpj_input.replace(".","").replace("/","").replace("-","")
            info = consultar_cnpj(limpo)
            if info and info.get('status') != 'ERROR':
                st.session_state.dados_cnpj = info
                st.toast("✅ Dados importados da Receita!")
            else:
                st.error("Limite da Receita atingido. Tente em 1 minuto.")

    with st.form("cad_final"):
        d = st.session_state.dados_cnpj
        c1, c2 = st.columns(2)
        razao = c1.text_input("Razão Social", value=d.get('nome', ''))
        fanta = c2.text_input("Nome Fantasia", value=d.get('fantasia', ''))
        
        c3, c4, c5 = st.columns([2, 2, 1])
        cnpj_ok = c3.text_input("CNPJ", value=d.get('cnpj', cnpj_input))
        regime = c4.selectbox("Regime", ["Lucro Real", "Lucro Presumido"])
        tipo_unid = c5.selectbox("Tipo", ["Matriz", "Filial"])
        
        cnae_val = f"{d['atividade_principal'][0].get('code', '')} - {d['atividade_principal'][0].get('text', '')}" if 'atividade_principal' in d else ""
        cnae = st.text_input("CNAE Principal", value=cnae_val)
        
        end_val = f"{d.get('logradouro','')}, {d.get('numero','')} - {d.get('bairro','')}, {d.get('municipio','')}/{d.get('uf','')}" if 'logradouro' in d else ""
        endereco = st.text_area("Endereço", value=end_val)

        if st.form_submit_button("💾 Salvar Unidade"):
            conn = get_db_connection()
            cursor = conn.cursor()
            sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            cursor.execute(sql, (razao, fanta, cnpj_ok, regime, tipo_unid, cnae, endereco))
            conn.commit()
            conn.close()
            st.session_state.dados_cnpj = {}
            st.success("✅ Empresa salva no UOL com sucesso!")
            st.rerun()

# --- MÓDULO: APURAÇÃO ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos")
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT id, nome, regime FROM empresas", conn)
    conn.close()

    if df_e.empty:
        st.warning("Cadastre uma empresa primeiro.")
    else:
        emp_sel = st.selectbox("Selecione a Empresa", df_e['nome'])
        dados_e = df_e[df_e['nome'] == emp_sel].iloc[0]
        
        with st.container(border=True):
            col1, col2, col3 = st.columns([2,1,1])
            op = col1.selectbox("Operação", ["Venda Mercadorias", "Receita Financeira", "Compra Insumos", "Energia"])
            val = col2.number_input("Valor R$", min_value=0.0, key=f"v_{st.session_state.v_key}")
            
            if col3.button("➕ Inserir"):
                tp = "Débito" if "Venda" in op or "Receita" in op else "Crédito"
                al_p, al_c = (0.0065, 0.04) if "Financeira" in op else (0.0165, 0.076) if dados_e['regime'] == "Lucro Real" else (0.0065, 0.03)
                st.session_state.itens_memoria.append({"Operação": op, "Base": val, "PIS": val*al_p, "COF": val*al_c, "Tipo": tp})
                st.session_state.v_key += 1
                st.rerun()

        if st.session_state.itens_memoria:
            st.table(pd.DataFrame(st.session_state.itens_memoria))
            if st.button("💾 Gravar no UOL"):
                st.success("Pronto para gravar!")

else:
    st.title("🛡️ Sistema Crescere")
    st.info("Utilize o menu lateral para navegar.")
