import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
from thefuzz import fuzz
from ofxparse import OfxParser
import logging
import hashlib

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# =============================================================================
# CLASSE UNDO STACK
# =============================================================================
class UndoStack:
    def __init__(self):
        if 'undo_stack' not in st.session_state:
            st.session_state.undo_stack = []

    def push(self, action_type, data):
        st.session_state.undo_stack.append({'type': action_type, 'data': data})

    def pop(self):
        if st.session_state.undo_stack:
            return st.session_state.undo_stack.pop()
        return None

    def clear(self):
        st.session_state.undo_stack = []

    def is_empty(self):
        return not bool(st.session_state.undo_stack)

# =============================================================================
# 1. UTILITÁRIOS E CONEXÃO
# =============================================================================
def get_connection():
    try:
        return mysql.connector.connect(
            host=st.secrets["mysql"]["host"],
            user=st.secrets["mysql"]["user"],
            password=st.secrets["mysql"]["password"],
            database=st.secrets["mysql"]["database"],
            use_pure=True,
            ssl_disabled=True
        )
    except mysql.connector.Error as err:
        logging.error(f"Erro ao conectar ao MySQL: {err}")
        st.error(f"Erro ao conectar ao banco de dados: {err}")
        st.stop()

def safe_db_query(query, params=(), conn_func=get_connection):
    conn = None
    try:
        conn = conn_func()
        return pd.read_sql(query, conn, params=params)
    except Exception as e:
        logging.error(f"Erro DB: {e}")
        st.error(f"Erro no banco: {str(e)[:100]}...")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()

def padronizar_texto(texto):
    if not texto:
        return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

def limpar_cnpj(cnpj_str):
    if not cnpj_str:
        return ""
    return re.sub(r'[^0-9]', '', str(cnpj_str))

def formatar_cnpj(cnpj_limpo):
    if not cnpj_limpo or len(cnpj_limpo) != 14:
        return cnpj_limpo
    return f"{cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}"

def formatar_contraparte_display(empresa_data):
    if empresa_data is None:
        return "Empresa Não Identificada"
    cnpj_formatado = formatar_cnpj(limpar_cnpj(empresa_data['cnpj']))
    return f"{empresa_data['nome']} | {empresa_data['tipo']} | {cnpj_formatado}"

# =============================================================================
# 2. INTELIGÊNCIA: AUTO-LEITURA DE CNPJ E BANCO
# =============================================================================
BBOX_HEADER_AREA = (0, 0, 600, 150)
BBOX_BANK_NAME_AREA = (50, 0, 550, 150)

BANCOS_KEYWORDS = {
    'STONE': ['STONE', 'INSTITUIÇÃO DE PAGAMENTO'],
    'SICOOB': ['SICOOB', 'BANCOOB', 'SICOOB BANCO'],
    'BRADESCO': ['BRADESCO', 'BANCO BRADESCO'],
    'ITAU': ['ITAU', 'BANCO ITAU'],
    'CAIXA': ['CAIXA', 'CAIXA ECONOMICA FEDERAL'],
    'SANTANDER': ['SANTANDER', 'BANCO SANTANDER'],
    'BB': ['BANCO DO BRASIL', 'BB'],
    'NUBANK': ['NUBANK', 'NU PAGAMENTOS'],
}

def identificar_cnpj_no_pdf(file_bytes):
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                header_text = pdf.pages[0].crop(BBOX_HEADER_AREA).extract_text()
                if header_text:
                    cnpj_match = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', header_text)
                    if cnpj_match:
                        return cnpj_match.group(0)
    except Exception as e:
        logging.error(f"Erro ao identificar CNPJ: {e}")
    return None

def identificar_banco_no_pdf(file_bytes):
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                header_text = pdf.pages[0].crop(BBOX_BANK_NAME_AREA).extract_text()
                if header_text:
                    header_upper = padronizar_texto(header_text)
                    for banco, keywords in BANCOS_KEYWORDS.items():
                        for kw in keywords:
                            if padronizar_texto(kw) in header_upper:
                                return banco
    except Exception as e:
        logging.error(f"Erro ao identificar banco: {e}")
    return "DESCONHECIDO"

@st.cache_data(ttl=300, show_spinner=False)
def buscar_empresa_por_cnpj_otimizado(cnpj_formatado, df_empresas):
    if not cnpj_formatado:
        return None
    cnpj_limpo_buscado = limpar_cnpj(cnpj_formatado)
    if 'cnpj_limpo' not in df_empresas.columns:
        df_empresas = df_empresas.copy()
        df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(limpar_cnpj)
    match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_limpo_buscado]
    return match_df.iloc[0].to_dict() if not match_df.empty else None

@st.cache_data(ttl=60, show_spinner=False)
def buscar_conta_por_banco(id_empresa, nome_banco):
    return safe_db_query(
        "SELECT conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s AND nome_banco = %s",
        (id_empresa, nome_banco)
    )['conta_contabil'].iloc[0] if not safe_db_query(...).empty else None  # Simplified

@st.cache_data(ttl=300, show_spinner=False)
def carregar_empresas():
    return safe_db_query("SELECT id, nome, fantasia, cnpj, tipo, apelido_unidade, conta_contabil FROM empresas")

@st.cache_data(ttl=300, show_spinner=False)
def carregar_contas_por_banco(id_empresa):
    return safe_db_query("SELECT id, nome_banco, conta_contabil FROM empresa_banco_contas WHERE id_empresa = %s ORDER
