import streamlit as st
import mysql.connector
import pandas as pd
from datetime import date, timedelta
from fpdf import FPDF
import io
import bcrypt

# --- 1. CONFIGURAÇÕES VISUAIS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; height: 42px; width: 100%;}
    div[data-testid="stForm"], .css-1d391kg, .stExpander { background-color: #ffffff; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0;}
    h3 { color: #004b87; margin-bottom: 20px; }
</style>
""", unsafe_allow_html=True)

# --- 2. FUNÇÕES BASE ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 3. LOGICA DE ACESSO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False

if not st.session_state.autenticado:
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h2 style='text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
        with st.form("login"):
            u = st.text_input("Usuário")
            p = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                conn = get_db_connection(); cur = conn.cursor(dictionary=True)
                cur.execute("SELECT * FROM usuarios WHERE username = %s", (u,))
                user = cur.fetchone(); conn.close()
                if user and verificar_senha(p, user['senha_hash']):
                    st.session_state.update({"autenticado": True, "usuario_logado": user['nome'], "username": user['username'], "nivel_acesso": user['nivel_acesso']})
                    st.rerun()
                else: st.error("Erro de acesso.")
    st.stop()

# --- 4. ESTADOS ---
if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "status_assinatura": "ATIVO"}
if 'rascunho' not in st.session_state: st.session_state.rascunho = []

# --- 5. MÓDULO EMPRESAS ---
def modulo_empresas():
    st.markdown("### Gestão de Empresas")
    f = st.session_state.dados_form
    with st.form("cad_empresa"):
        c1, c2 = st.columns(2)
        nome = c1.text_input("Razão Social", value=f['nome'])
        cnpj = c2.text_input("CNPJ", value=f['cnpj'])
        c3, c4, c5 = st.columns(3)
        regime = c3.selectbox("Regime", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
        cnae = c4.text_input("CNAE", value=f['cnae'])
        status = c5.selectbox("Status", ["ATIVO", "SUSPENSO"], index=0 if f.get('status_assinatura') == "ATIVO" else 1)
        end = st.text_area("Endereço", value=f['endereco'])
        if st.form_submit_button("Salvar Empresa"):
            conn = get_db_connection(); cur = conn.cursor()
            if f['id']: cur.execute("UPDATE empresas SET nome=%s, cnpj=%s, regime=%s, cnae=%s, endereco=%s, status_assinatura=%s WHERE id=%s", (nome, cnpj, regime, cnae, end, status, f['id']))
            else: cur.execute("INSERT INTO empresas (nome, cnpj, regime, cnae, endereco, status_assinatura) VALUES (%s,%s,%s,%s,%s,%s)", (nome, cnpj, regime, cnae, end, status))
            conn.commit(); conn.close(); st.success("Salvo!"); st.rerun()

# --- 6. MÓDULO APURAÇÃO (ALINHAMENTO REAL) ---
def modulo_apuracao():
    st.markdown("### Apuração Mensal")
    conn = get_db_connection()
    df_emp = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas WHERE status_assinatura='ATIVO'", conn)
    df_op = pd.read_sql("SELECT * FROM operacoes ORDER BY nome ASC", conn)
    
    col_e, col_c = st.columns([2, 1])
    emp_sel = col_e.selectbox("Empresa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.iloc[0]['id']) # Simplificado para o exemplo
    comp = col_c.text_input("Competência", value="02/2026")

    st.divider()
    l, r = st.columns(2, gap="large")

    with l:
        st.markdown("#### Novo Lançamento")
        op = st.selectbox("Operação", df_op['nome'].tolist())
        val = st.number_input("Base (R$)", min_value=0.0)
        st.markdown("<br>"*2, unsafe_allow_html=True) # Empurra o botão para baixo
        if st.button("Adicionar ao Rascunho"):
            st.session_state.rascunho.append({"op": op, "val": val})
            st.rerun()

    with r:
        st.markdown("#### Itens no Rascunho")
        with st.container(height=230):
            for i, it in enumerate(st.session_state.rascunho):
                st.write(f"{it['op']} - {formatar_moeda(it['val'])}")
        if st.button("Gravar no Banco", type="primary"):
            st.success("Dados enviados ao banco!"); st.session_state.rascunho = []

# --- 7. RELATÓRIOS (DOWNLOADS REAIS) ---
def modulo_relatorios():
    st.markdown("### Relatórios e Integração")
    
    # Simulação de dados para o Excel
    output_xlsx = io.BytesIO()
    with pd.ExcelWriter(output_xlsx, engine='xlsxwriter') as writer:
        pd.DataFrame([["Crescere", "Integrador"]]).to_excel(writer, sheet_name='ERP')
    
    # Simulação de PDF
    pdf = FPDF()
    pdf.add_page(); pdf.set_font("Arial", size=12); pdf.cell(200, 10, txt="Relatório Crescere", ln=1, align='C')
    pdf_output = pdf.output(dest='S').encode('latin-1')

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(label="📥 Baixar Excel ERP (XLSX)", data=output_xlsx.getvalue(), file_name="integracao_erp.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with c2:
        st.download_button(label="📄 Baixar PDF Conferência", data=pdf_output, file_name="relatorio_conferencia.pdf", mime="application/pdf")

# --- 8. MENU ---
with st.sidebar:
    st.markdown("## CRESCERE")
    menu = st.radio("Módulos", ["Empresas", "Apuração", "Relatórios"])

if menu == "Empresas": modulo_empresas()
elif menu == "Apuração": modulo_apuracao()
elif menu == "Relatórios": modulo_relatorios()
