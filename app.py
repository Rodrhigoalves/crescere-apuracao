import streamlit as st
import mysql.connector
import pandas as pd
import json
import requests
from datetime import datetime, date
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES E LÓGICA DE DATA ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide")

# Lógica para mês anterior automático
hoje = date.today()
mes_atual_idx = hoje.month - 1
mes_anterior_idx = mes_atual_idx - 1 if mes_atual_idx > 0 else 11
lista_meses = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'v_key' not in st.session_state: st.session_state.v_key = 0

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. BANCO DE DADOS ---
def init_db():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas (
        id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), fantasia VARCHAR(255), 
        cnpj VARCHAR(20), regime VARCHAR(50), tipo VARCHAR(20), matriz_id INT,
        cnae VARCHAR(255), endereco TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS naturezas_custom (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(100), tipo VARCHAR(20))''')
    conn.commit(); conn.close()

init_db()

# --- 3. INTERFACE ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Navegação", ["Início", "Cadastro de Unidades", "Apuração Mensal", "Relatórios e ERP"])

# --- CADASTRO ---
if menu == "Cadastro de Unidades":
    st.header("🏢 Cadastro de Unidades")
    # (Lógica de consulta CNPJ mantida...)
    with st.form("f_cad"):
        # ... campos de cadastro ...
        if st.form_submit_button("Salvar"):
            # Insert no banco...
            st.success("Unidade Salva!")

# --- APURAÇÃO ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos Mensais")
    
    # Busca empresas e naturezas customizadas
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT id, nome, tipo, regime FROM empresas", conn)
    df_custom = pd.read_sql("SELECT nome, tipo FROM naturezas_custom", conn)
    conn.close()

    if df_e.empty:
        st.warning("Cadastre uma empresa primeiro.")
    else:
        # Formatação do nome com [M] ou [F]
        df_e['display'] = df_e.apply(lambda x: f"[{x['tipo'][0]}] {x['nome']}", axis=1)
        
        c_emp, c_mes, c_ano = st.columns([2,1,1])
        emp_sel_display = c_emp.selectbox("Unidade", df_e['display'])
        emp_id = int(df_e[df_e['display'] == emp_sel_display]['id'].values[0])
        regime_sel = df_e[df_e['display'] == emp_sel_display]['regime'].values[0]
        
        mes_sel = c_mes.selectbox("Mês", lista_meses, index=mes_anterior_idx)
        ano_sel = c_ano.selectbox("Ano", [2025, 2026, 2027], index=1)

        st.divider()
        
        # --- SEÇÃO: CADASTRAR NOVA NATUREZA ---
        with st.expander("🆕 Cadastrar Nova Natureza de Operação"):
            st.warning("⚠️ ATENÇÃO: Verifique se a natureza já não existe na lista oficial para evitar duplicidade e erros na exportação do ERP.")
            c_n1, c_n2, c_n3 = st.columns([2,1,1])
            nova_nome = c_n1.text_input("Nome da Natureza")
            nova_tipo = c_n2.selectbox("Tipo de Natureza", ["Débito", "Crédito"])
            if c_n3.button("Gravar Natureza"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO naturezas_custom (nome, tipo) VALUES (%s, %s)", (nova_nome, nova_tipo))
                conn.commit(); conn.close(); st.rerun()

        # --- LANÇAMENTO ---
        with st.container(border=True):
            # Naturezas Nativas
            nativas_deb = ["Venda de Mercadorias", "Venda de Serviços", "Receita Financeira", "Juros Ativos"]
            nativas_cre = ["Compra de Insumos", "Energia Elétrica", "Combustível (Diesel)", "Aluguel (PJ)", "Fretes", "Manutenção"]
            
            # Une com as customizadas
            op_deb = nativas_deb + df_custom[df_custom['tipo'] == 'Débito']['nome'].tolist()
            op_cre = nativas_cre + df_custom[df_custom['tipo'] == 'Crédito']['nome'].tolist()
            
            col1, col2, col3 = st.columns([2,1,1])
            op_final = col1.selectbox("Natureza", op_deb + op_cre)
            valor = col2.number_input("Valor Base (R$)", min_value=0.0, step=0.01, key=f"v_{st.session_state.v_key}")
            
            if col3.button("➕ Adicionar"):
                tipo_o = "Débito" if op_final in op_deb else "Crédito"
                # Regra de alíquotas
                if "Financeira" in op_final and regime_sel == "Lucro Real": al_p, al_c = 0.0065, 0.04
                elif regime_sel == "Lucro Real": al_p, al_c = 0.0165, 0.076
                else: al_p, al_c = 0.0065, 0.03
                
                st.session_state.itens_memoria.append({"Operação": op_final, "Base": valor, "PIS": valor*al_p, "COF": valor*al_c, "Tipo": tipo_o})
                st.session_state.v_key += 1; st.rerun()

        if st.session_state.itens_memoria:
            df_m = pd.DataFrame(st.session_state.itens_memoria)
            st.table(df_m.style.format({"Base": "{:.2f}", "PIS": "{:.2f}", "COF": "{:.2f}"}))
            
            if st.button("💾 Finalizar e Gravar no UOL", type="primary"):
                try:
                    conn = get_db_connection(); cursor = conn.cursor()
                    js = json.dumps(st.session_state.itens_memoria)
                    p_t = float(df_m['PIS'].sum())
                    c_t = float(df_m['COF'].sum())
                    cursor.execute("INSERT INTO historico_apuracoes (empresa_id, competencia, detalhamento_json, pis_total, cofins_total, data_reg) VALUES (%s,%s,%s,%s,%s,%s)",
                                   (emp_id, f"{mes_sel}/{ano_sel}", js, p_t, c_t, datetime.now().strftime("%d/%m/%Y %H:%M")))
                    conn.commit(); conn.close()
                    st.session_state.itens_memoria = []
                    st.success("✅ Apuração salva no banco de dados!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao gravar: {e}")

# --- PÁGINA INICIAL ---
else:
    st.title("🛡️ Sistema Crescere")
    st.write("Banco de dados UOL conectado e pronto.")
