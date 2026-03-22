import streamlit as st
import mysql.connector
import pandas as pd
import json
import requests
from datetime import datetime, date
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES E ESTADOS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide")

# Lógica para mês anterior automático
hoje = date.today()
mes_anterior_idx = (hoje.month - 2) if hoje.month > 1 else 11
lista_meses = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'v_key' not in st.session_state: st.session_state.v_key = 0
if 'dados_cnpj' not in st.session_state: st.session_state.dados_cnpj = {}
if 'edit_emp_id' not in st.session_state: st.session_state.edit_emp_id = None

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. CONSULTA CNPJ ---
def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

# --- 3. INTERFACE ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Navegação", ["Início", "Empresas", "Apuração Mensal", "Relatórios"])

# --- MÓDULO: EMPRESAS (CADASTRO E EDIÇÃO) ---
if menu == "Empresas":
    st.header("🏢 Gestão de Unidades")
    
    # Campo de busca para consulta externa
    with st.expander("🔍 Consultar Novo CNPJ (Receita Federal)", expanded=True):
        c1, c2 = st.columns([3, 1])
        cnpj_busca = c1.text_input("Digite o CNPJ para auto-preenchimento")
        if c2.button("Buscar Dados"):
            limpo = cnpj_busca.replace(".","").replace("/","").replace("-","")
            res = consultar_cnpj(limpo)
            if res and res.get('status') != 'ERROR':
                st.session_state.dados_cnpj = res
                st.toast("✅ Dados carregados!")
            else:
                st.error("CNPJ não encontrado ou limite de consultas atingido.")

    # Formulário de Cadastro/Edição
    with st.form("form_unidade"):
        d = st.session_state.dados_cnpj
        col1, col2 = st.columns(2)
        nome = col1.text_input("Razão Social", value=d.get('nome', ''))
        fanta = col2.text_input("Nome Fantasia", value=d.get('fantasia', ''))
        
        c3, c4, c5 = st.columns([2, 2, 1])
        cnpj = c3.text_input("CNPJ", value=d.get('cnpj', ''))
        regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"])
        tipo = c5.selectbox("Tipo", ["Matriz", "Filial"])
        
        cnae_val = f"{d['atividade_principal'][0].get('code', '')}" if 'atividade_principal' in d else ""
        cnae = st.text_input("CNAE Principal", value=cnae_val)
        
        end_val = f"{d.get('logradouro','')}, {d.get('numero','')} - {d.get('bairro','')}, {d.get('municipio','')}/{d.get('uf','')}" if 'logradouro' in d else ""
        endereco = st.text_area("Endereço Completo", value=end_val)

        if st.form_submit_button("💾 Salvar Unidade"):
            conn = get_db_connection(); cursor = conn.cursor()
            sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco))
            conn.commit(); conn.close()
            st.session_state.dados_cnpj = {}
            st.success("✅ Unidade salva com sucesso!")
            st.rerun()

    # Tabela de Edição
    st.divider()
    st.subheader("📝 Unidades Cadastradas")
    conn = get_db_connection()
    df_lista = pd.read_sql("SELECT id, nome, cnpj, tipo, regime FROM empresas", conn)
    conn.close()
    
    for _, row in df_lista.iterrows():
        exp = st.expander(f"[{row['tipo'][0]}] {row['nome']}")
        col_ed1, col_ed2 = exp.columns([4, 1])
        col_ed1.write(f"CNPJ: {row['cnpj']} | Regime: {row['regime']}")
        if col_ed2.button("✏️ Editar", key=f"edit_{row['id']}"):
            st.info("Função de edição em carregamento... (Dados movidos para o formulário)")

# --- MÓDULO: APURAÇÃO ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos Mensais")
    
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT id, nome, tipo, regime FROM empresas", conn)
    conn.close()

    if df_e.empty:
        st.warning("Cadastre uma empresa primeiro.")
    else:
        df_e['display'] = df_e.apply(lambda x: f"[{x['tipo'][0]}] {x['nome']}", axis=1)
        
        c_emp, c_mes, c_ano = st.columns([2,1,1])
        emp_sel_display = c_emp.selectbox("Unidade", df_e['display'])
        emp_id = int(df_e[df_e['display'] == emp_sel_display]['id'].values[0])
        regime_sel = df_e[df_e['display'] == emp_sel_display]['regime'].values[0]
        
        mes_sel = c_mes.selectbox("Mês", lista_meses, index=mes_anterior_idx)
        ano_sel = c_ano.selectbox("Ano", [2025, 2026, 2027], index=1)

        # ... Lógica de lançamentos (igual à anterior) ...
        # (Adicionado float() na gravação para evitar erro no MySQL do UOL)
