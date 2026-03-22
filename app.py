import streamlit as st
import mysql.connector
import pandas as pd
import json
import requests
from datetime import datetime
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES INICIAIS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide")

# Inicialização de estados para não perder dados ao clicar em botões
if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'v_key' not in st.session_state: st.session_state.v_key = 0
if 'dados_cnpj' not in st.session_state: st.session_state.dados_cnpj = {}

def formata_real(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 2. BANCO DE DADOS (UOL) ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Tabela de Empresas Reforçada (Com CNAE e Endereço para o PDF Profissional)
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas 
                      (id INT AUTO_INCREMENT PRIMARY KEY, 
                       nome VARCHAR(255), fantasia VARCHAR(255), cnpj VARCHAR(20), 
                       regime VARCHAR(50), tipo VARCHAR(20), matriz_id INT,
                       cnae VARCHAR(255), endereco TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS historico_apuracoes (
        id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, competencia VARCHAR(20), 
        detalhamento_json LONGTEXT, pis_total DECIMAL(15,2), cofins_total DECIMAL(15,2), 
        data_reg VARCHAR(50))''')
    conn.commit()
    conn.close()

init_db()

# --- 3. FUNÇÃO DE CONSULTA CNPJ (API) ---
def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_status == 200:
            return response.json()
    except:
        return None
    return None

# --- 4. INTERFACE ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Módulos", ["Início", "Cadastro de Unidades", "Apuração Mensal", "Relatórios/PDF"])
    st.divider()
    st.caption("Usuário: Rodrigo")

# --- MÓDULO: CADASTRO COM AUTOMAÇÃO ---
if menu == "Cadastro de Unidades":
    st.header("🏢 Cadastro de Empresas e Filiais")
    
    with st.container(border=True):
        col_cnpj, col_btn = st.columns([3, 1])
        cnpj_input = col_cnpj.text_input("Digite apenas os números do CNPJ")
        
        if col_btn.button("🔍 Consultar Receita"):
            with st.spinner("Buscando dados na Receita..."):
                info = consultar_cnpj(cnpj_input.replace(".", "").replace("/", "").replace("-", ""))
                if info and info.get('status') != 'ERROR':
                    st.session_state.dados_cnpj = info
                    st.success("Dados encontrados!")
                else:
                    st.error("CNPJ não encontrado ou limite de consultas atingido.")

    # Formulário de Cadastro
    with st.form("form_cadastro"):
        d = st.session_state.dados_cnpj
        col1, col2 = st.columns(2)
        
        nome_social = col1.text_input("Razão Social", value=d.get('nome', ''))
        nome_fantasia = col2.text_input("Nome Fantasia", value=d.get('fantasia', ''))
        
        c1, c2, c3 = st.columns([2, 2, 1])
        cnpj_final = c1.text_input("CNPJ Confirmado", value=d.get('cnpj', cnpj_input))
        regime = c2.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"])
        tipo = c3.selectbox("Tipo", ["Matriz", "Filial"])
        
        cnae = st.text_input("CNAE Principal", value=f"{d.get('atividade_principal', [{}])[0].get('code', '')} - {d.get('atividade_principal', [{}])[0].get('text', '')}")
        endereco = st.text_area("Endereço Completo", value=f"{d.get('logradouro', '')}, {d.get('numero', '')} - {d.get('bairro', '')}, {d.get('municipio', '')}/{d.get('uf', '')}")

        # Lógica de Matriz/Filial
        conn = get_db_connection()
        df_matrizes = pd.read_sql("SELECT id, nome FROM empresas WHERE tipo = 'Matriz'", conn)
        conn.close()
        
        matriz_vinculo = None
        if tipo == "Filial":
            sel_m = st.selectbox("Selecione a Matriz desta Filial", df_matrizes['nome'] if not df_matrizes.empty else ["Nenhuma Matriz Cadastrada"])
            if not df_matrizes.empty:
                matriz_vinculo = int(df_matrizes[df_matrizes['nome'] == sel_m]['id'].values[0])

        if st.form_submit_button("💾 Salvar no Banco de Dados"):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, matriz_id, cnae, endereco) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                cursor.execute(sql, (nome_social, nome_fantasia, cnpj_final, regime, tipo, matriz_vinculo, cnae, endereco))
                conn.commit()
                conn.close()
                st.success(f"Unidade {nome_fantasia} cadastrada com sucesso!")
                st.session_state.dados_cnpj = {} # Limpa após salvar
            except Exception as e:
                st.error(f"Erro ao salvar: {e}")

# --- MÓDULO: APURAÇÃO ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos de Notas e Operações")
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT * FROM empresas", conn)
    conn.close()

    if df_e.empty:
        st.warning("Cadastre uma empresa primeiro.")
    else:
        col_e, col_m, col_a = st.columns([2,1,1])
        emp_sel = col_e.selectbox("Empresa", df_e['nome'])
        comp = f"{col_m.selectbox('Mês', ['01','02','03','04','05','06','07','08','09','10','11','12'])}/{col_a.selectbox('Ano', ['2025','2026'])}"
        
        dados_e = df_e[df_e['nome'] == emp_sel].iloc[0]

        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            ops_deb = ["Venda de Mercadorias", "Receita Financeira", "Serviços"]
            ops_cre = ["Compra Insumos", "Energia", "Aluguel PJ", "Fretes"]
            
            op = c1.selectbox("Operação", ops_deb + ops_cre)
            val = c2.number_input("Valor R$", min_value=0.0, key=f"v_{st.session_state.v_key}")
            
            if c3.button("➕ Adicionar"):
                tipo_op = "Débito" if op in ops_deb else "Crédito"
                # Regra PIS/COFINS (Financeira vs Normal)
                if "Financeira" in op and dados_e['regime'] == "Lucro Real":
                    al_p, al_c = 0.0065, 0.04
                elif dados_e['regime'] == "Lucro Real":
                    al_p, al_c = 0.0165, 0.076
                else:
                    al_p, al_c = 0.0065, 0.03
                
                st.session_state.itens_memoria.append({
                    "Operação": op, "Base": val, "PIS": val * al_p, "COF": val * al_c, "Tipo": tipo_op
                })
                st.session_state.v_key += 1
                st.rerun()

        if st.session_state.itens_memoria:
            df_temp = pd.DataFrame(st.session_state.itens_memoria)
            st.table(df_temp)
            if st.button("🏁 Finalizar e Salvar"):
                js = json.dumps(st.session_state.itens_memoria)
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO historico_apuracoes (empresa_id, competencia, detalhamento_json, data_reg) VALUES (%s, %s, %s, %s)",
                               (int(dados_e['id']), comp, js, datetime.now().strftime("%d/%m/%Y %H:%M")))
                conn.commit(); conn.close()
                st.session_state.itens_memoria = []
                st.success("Apuração salva no UOL!")

else:
    st.subheader("Bem-vindo, Rodrigo!")
    st.write("Use o menu ao lado para gerenciar a Crescere.")
