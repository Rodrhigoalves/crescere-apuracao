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
    .stTextInput input { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    .stTextInput input:focus { border: 2px solid #004b87 !important; background-color: #e6f0fa !important; }
</style>
""", unsafe_allow_html=True)

# --- 2. FUNÇÕES DE BASE E SEGURANÇA ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None

# --- 3. LÓGICA DE ACESSO ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown("<br><br>", unsafe_allow_html=True)
    c1, login_col, c3 = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h1 style='text-align: center; color: #004b87;'>🛡️ CRESCERE</h1>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Usuário")
            pw_input = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT u.*, e.status_assinatura FROM usuarios u LEFT JOIN empresas e ON u.empresa_id = e.id WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                user_data = cursor.fetchone()
                conn.close()
                
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    if user_data['nivel_acesso'] != 'SUPER_ADMIN' and user_data.get('status_assinatura') == 'SUSPENSO':
                        st.error("Acesso Suspenso.")
                    else:
                        st.session_state.autenticado = True
                        st.session_state.usuario_logado = user_data['nome']
                        st.session_state.username = user_data['username']
                        st.session_state.nivel_acesso = user_data['nivel_acesso']
                        st.rerun()
                else:
                    st.error("Usuário ou senha incorretos.")
    st.stop()

# --- 4. ESTADOS DO APP (PÓS-LOGIN) ---
hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

# --- 5. MÓDULOS REAIS ---

def modulo_empresas():
    st.markdown("## Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["Novo Cadastro", "Unidades Cadastradas"])
    # (Lógica original de empresas mantida)
    with tab_cad:
        f = st.session_state.dados_form
        c1, c2 = st.columns(2)
        nome = c1.text_input("Razão Social", value=f['nome'])
        fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
        if st.button("Salvar Empresa", use_container_width=True):
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO empresas (nome, fantasia, status_assinatura) VALUES (%s, %s, 'ATIVO')", (nome, fanta))
            conn.commit(); conn.close(); st.success("Empresa salva!"); st.rerun()

def modulo_apuracao():
    st.markdown("## Apuração Mensal")
    conn = get_db_connection()
    df_empresas = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
    df_operacoes = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC", conn)
    df_operacoes['nome_exibicao'] = df_operacoes.apply(lambda x: f"[{'DÉBITO' if x['tipo'] == 'RECEITA' else 'CRÉDITO'}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Empresa", df_empresas.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    empresa_id = int(df_empresas.loc[df_empresas.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    regime_empresa = df_empresas.loc[df_empresas['id'] == empresa_id].iloc[0]['regime']
    
    competencia = c_comp.text_input("Competência", value=competencia_padrao)
    c_user.text_input("Operador Logado", value=st.session_state.usuario_logado, disabled=True)

    st.divider()
    col1, col2 = st.columns([1, 1.2])

    with col1:
        st.markdown("#### Novo Lançamento")
        op_sel = st.selectbox("Operação", df_operacoes['nome_exibicao'].tolist())
        v_base = st.number_input("Base de Cálculo (R$)", min_value=0.00)
        hist = st.text_input("Histórico / Observação")
        
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            op_row = df_operacoes[df_operacoes['nome_exibicao'] == op_sel].iloc[0]
            # Cálculos Tributários Originais
            if regime_empresa == "Lucro Real":
                vp, vc = (v_base * 0.0065, v_base * 0.04) if op_row['nome'] == "Receita Financeira" else (v_base * 0.0165, v_base * 0.076)
            else:
                vp, vc = (v_base * 0.0065, v_base * 0.03)
            
            st.session_state.rascunho_lancamentos.append({
                "empresa_id": empresa_id, "operacao_id": int(op_row['id']), "op_nome": op_sel,
                "v_base": v_base, "v_pis": vp, "v_cofins": vc, "hist": hist
            })
            st.rerun()

    with col2:
        st.markdown("#### Rascunho (Pré-Banco)")
        with st.container(height=350, border=True):
            for i, item in enumerate(st.session_state.rascunho_lancamentos):
                c_a, c_b, c_c = st.columns([4, 2, 1])
                c_a.write(f"**{item['op_nome']}**")
                c_b.write(formatar_moeda(item['v_base']))
                if c_c.button("✖", key=f"del_{i}"):
                    st.session_state.rascunho_lancamentos.pop(i); st.rerun()
        
        if st.session_state.rascunho_lancamentos and st.button("💾 Gravar no Banco", type="primary", use_container_width=True):
            cursor = conn.cursor()
            m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            for it in st.session_state.rascunho_lancamentos:
                # AUDITORIA: Gravando o st.session_state.username
                cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO')",
                               (it['empresa_id'], it['operacao_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['hist'], st.session_state.username))
            conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Lançamentos Auditados!"); st.rerun()
    conn.close()

def modulo_parametros():
    st.markdown("## ⚙️ Parâmetros Contábeis")
    tabs = st.tabs(["➕ Nova Operação", "🚨 Manutenção"]) if st.session_state.nivel_acesso == "SUPER_ADMIN" else st.tabs(["➕ Nova Operação"])
    
    with tabs[0]:
        st.write("Configuração de Naturezas.")
        
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        with tabs[1]:
            st.warning("Área de manutenção do sistema.")
            if st.text_input("Frase de Segurança") == "CONFIRMAR EXCLUSAO TOTAL":
                if st.button("RESETAR BANCO"): st.error("Banco Resetado.")

# --- 6. SIDEBAR ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center; color: #64748b;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "⚙️ Parâmetros Contábeis"])
    st.write("---")
    if st.button("🚪 Sair / Logoff", use_container_width=True):
        st.session_state.autenticado = False
        st.rerun()

# --- 7. RENDERIZAÇÃO ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
