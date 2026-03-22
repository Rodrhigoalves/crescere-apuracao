import streamlit as st
import mysql.connector
import pandas as pd

# Função para conectar ao banco usando as Secrets
def get_db_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

# Cria as tabelas iniciais se não existirem
def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS empresas 
                          (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), tipo VARCHAR(50))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lancamentos 
                          (id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, mes VARCHAR(50), 
                           ano INT, faturamento DECIMAL(15,2), pis DECIMAL(15,2), 
                           cofins DECIMAL(15,2), total DECIMAL(15,2))''')
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Erro ao conectar no banco do UOL: {e}")

st.set_page_config(page_title="Crescere - Apuração", layout="wide")
st.title("🛡️ Crescere - Sistema de Apuração PIS/COFINS")

init_db()

st.success("Conectado ao banco de dados do UOL com sucesso!")
st.info("O próximo passo é cadastrar as empresas no menu lateral.")
