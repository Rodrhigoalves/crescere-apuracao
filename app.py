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
    
    # Criar tabela de empresas já com a estrutura completa
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas (
        id INT AUTO_INCREMENT PRIMARY KEY, 
        nome VARCHAR(255), 
        fantasia VARCHAR(255), 
        cnpj VARCHAR(20), 
        regime VARCHAR(50), 
        tipo VARCHAR(20), 
        matriz_id INT,
        cnae VARCHAR(255), 
        endereco TEXT
    )''')
    
    # Criar tabela de histórico
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, 
        empresa_id INT, 
        competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, 
        pis_total DECIMAL(15,2), 
        cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50), 
        log_reprocessamento TEXT
    )''')
    
    conn.commit()
    conn.close()

# Inicializa o banco
init_db()

# --- 3. CONSULTA CNPJ ---
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
    st.divider()
    # BOTÃO PARA CASO DE ERRO DE COLUNA: Ele apaga a tabela e cria do zero
    if st.button("🔄 Resetar Estrutura (Cuidado!)"):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS empresas")
        conn.commit()
        conn.close()
        st.rerun()

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
                st.toast("✅ Dados importados!")

    with st.form("cad_final"):
        d = st.session_state.dados_cnpj
        razao = st.text_input("Razão Social", value=d.get('nome', ''))
        fanta = st.text_input("Nome Fantasia", value=d.get('fantasia', ''))
        
        c3, c4, c5 = st.columns([2, 2, 1])
        cnpj_ok = c3.text_input("CNPJ", value=d.get('cnpj', cnpj_input))
        regime = c4.selectbox("Regime", ["Lucro Real", "Lucro Presumido"])
        tipo_unid = c5.selectbox("Tipo", ["Matriz", "Filial"])
        
        cnae_val = f"{d['atividade_principal'][0].get('code', '')} - {d['atividade_principal'][0].get('text', '')}" if 'atividade_principal' in d else ""
        cnae = st.text_input("CNAE Principal", value=cnae_val)
        
        end_val = f"{d.get('logradouro','')}, {d.get('numero','')} - {d.get('bairro','')}, {d.get('municipio','')}/{d.get('uf','')}" if 'logradouro' in d else ""
        endereco = st.text_area("Endereço", value=end_val)

        if st.form_submit_button("💾 Salvar no UOL"):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                cursor.execute(sql, (razao, fanta, cnpj_ok, regime, tipo_unid, cnae, endereco))
                conn.commit()
                conn.close()
                st.session_state.dados_cnpj = {}
                st.success("✅ Empresa salva com sucesso!")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao salvar. Tente clicar no botão 'Resetar Estrutura' no menu lateral e tente de novo. Erro: {e}")

# --- PÁGINA INICIAL ---
else:
    st.title("🛡️ Sistema Crescere")
    st.info("Banco de Dados UOL conectado.")
