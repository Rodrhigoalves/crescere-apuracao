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
import json

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

undo_manager = UndoStack()

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
    return re.sub(r'[^0-9]', '', str(cnpj_str
