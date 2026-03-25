import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import calendar
from fpdf import FPDF
import io
import os
import bcrypt

# --- 1. CONFIGURAÇÕES VISUAIS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500;}
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"], .css-1d391kg { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;}
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
</style>
""", unsafe_allow_html=True)

# --- 2. FUNÇÕES DE SEGURANÇA E BANCO ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def gerar_hash_senha(senha):
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def realizar_login(usuario, senha):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        query = "SELECT u.*, e.status_assinatura FROM usuarios u LEFT JOIN empresas e ON u.empresa_id = e.id WHERE u.username = %s AND u.status_usuario = 'ATIVO'"
        cursor.execute(query, (usuario,))
        user_data = cursor.fetchone()
        if user_data and verificar_senha(senha, user_data['senha_hash']):
            if user_data['nivel_acesso'] != 'SUPER_ADMIN' and user_data['status_assinatura'] == 'SUSPENSO':
                return None, "Acesso Suspenso. Entre em contato com o financeiro."
            return user_data, None
        return None, "Usuário ou senha incorretos."
    finally:
        conn.close()

# --- 3. LÓGICA DE ACESSO (TELA DE BLOQUEIO) ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown("<br><br>", unsafe_allow_html=True)
    c1, login_col, c3 = st.columns([1, 1.5, 1])
    
    with login_col:
        st.markdown("<h1 style='text-align: center; color: #004b87;'>🛡️ CRESCERE</h1>", unsafe_allow_html=True)
        
        # --- BOTÃO DE EMERGÊNCIA (INDENTAÇÃO CORRIGIDA) ---
        if st.button("🆘 RESETAR MINHA SENHA AGORA"):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                novo_hash = gerar_hash_senha("Crescere@2026")
                cursor.execute("UPDATE usuarios SET senha_hash = %s WHERE username = 'rodrhigo'", (novo_hash,))
                conn.commit()
                conn.close()
                st.success("Senha resetada via Python! Tente logar com: rodrhigo / Crescere@2026")
            except Exception as e:
                st.error(f"Erro: {e}")

        with st.form("form_login"):
            user_input = st.text_input("Usuário")
            pw_input = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                dados_user, erro = realizar_login(user_input, pw_input)
                if dados_user:
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = dados_user['nome']
                    st.session_state.nivel_acesso = dados_user['nivel_acesso']
                    st.session_state.empresa_id = dados_user['empresa_id']
                    st.rerun()
                else:
                    st.error(erro)
    st.stop()

# --- 4. CONFIGURAÇÕES PÓS-LOGIN ---
hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 5. MÓDULOS ---
def modulo_empresas():
    st.markdown("## Gestão de Empresas")
    # (Restante do código de empresas simplificado para foco no login)
    st.info("Módulo de Empresas Ativo.")

def modulo_apuracao():
    st.markdown("## Apuração Mensal")
    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC", conn)
        df_operacoes['nome_exibicao'] = df_operacoes.apply(lambda x: f"[{'DÉBITO' if x['tipo'] == 'RECEITA' else 'CRÉDITO'}] {x['nome']}", axis=1)
        
        c_emp, c_comp, c_user = st.columns([2, 1, 1])
        emp_sel = c_emp.selectbox("Empresa", df_empresas.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
        competencia = c_comp.text_input("Competência", value=competencia_padrao)
        c_user.text_input("Operador", value=st.session_state.usuario_logado, disabled=True)
    except:
        st.error("Erro ao carregar dados."); return
    finally:
        conn.close()

def modulo_parametros():
    st.markdown("## ⚙️ Parâmetros Contábeis")
    tabs = st.tabs(["➕ Nova Operação", "🚨 Manutenção"]) if st.session_state.nivel_acesso == "SUPER_ADMIN" else st.tabs(["➕ Nova Operação"])
    st.write("Configurações do sistema.")

# --- 6. SIDEBAR ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "⚙️ Parâmetros Contábeis"])
    st.write("---")
    if st.button("🚪 Sair"):
        st.session_state.autenticado = False
        st.rerun()

# --- 7. RENDERIZAÇÃO ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
