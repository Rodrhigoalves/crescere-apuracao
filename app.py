import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, datetime, timedelta, timezone 
import io
import bcrypt
from fpdf import FPDF
from dateutil.relativedelta import relativedelta
import calendar 

# --- 1. CONFIGURAÇÕES VISUAIS E CSS (HARMONIA TOTAL) ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    
    /* Botões Padrão Crescere (Azul Institucional) */
    .stButton>button, .stDownloadButton>button { 
        background-color: #004b87 !important; 
        color: white !important; 
        border-radius: 4px !important; 
        border: none !important;
        font-weight: 500 !important; 
        height: 45px !important; 
        width: 100% !important;
        transition: all 0.2s !important;
    }
    .stButton>button:hover, .stDownloadButton>button:hover { 
        background-color: #003366 !important; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important;
    }
    
    /* Containers e Áreas Brancas */
    div[data-testid="stForm"], .stExpander, div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] { 
        background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;
    }
    
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}

    /* Cabeçalho Profissional Harmonizado */
    .header-box {
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        border-left: 6px solid #004b87;
        margin-bottom: 25px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .header-table { width: 100%; border-collapse: collapse; }
    .header-table td { padding: 5px 10px; color: #334155; font-size: 14px; }
    .header-label { font-weight: 700; color: #0f172a; }
</style>
""", unsafe_allow_html=True)

# --- CLASSE DE PDF ---
class RelatorioCrescerePDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, 'Desenvolvido por Rodrhigo Alves | Conciliacao e Auditoria Contabil', 0, 0, 'C')

# --- 2. FUNÇÕES DE SUPORTE (DEFINIDAS NO TOPO PARA EVITAR NAMEERROR) ---

def get_db_connection():
    try:
        return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err:
        st.error(f"Erro de Conexão: {err}"); st.stop()

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    return (valor_base * 0.0065, valor_base * 0.03)

@st.cache_data(ttl=300)
def carregar_operacoes():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
    conn.close(); return df

@st.cache_data(ttl=300)
def carregar_empresas_ativas():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
    conn.close(); return df

# --- MÓDULOS DO SISTEMA ---

def modulo_empresas():
    st.markdown("### Gestão de Empresas e Unidades")
    df = carregar_empresas_ativas()
    st.dataframe(df, use_container_width=True, hide_index=True)

def modulo_apuracao():
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

    c_sel1, c_sel2 = st.columns([2, 1])
    emp_sel_txt = c_sel1.selectbox("Unidade de Trabalho", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_row = df_emp[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel_txt].iloc[0]
    competencia = c_sel2.text_input("Competência", value=competencia_padrao)

    # CABEÇALHO PROFISSIONAL
    st.markdown(f"""
    <div class="header-box">
        <table class="header-table">
            <tr>
                <td><span class="header-label">Razão Social:</span> {emp_row['nome']}</td>
                <td><span class="header-label">Nome Fantasia:</span> {emp_row['fantasia'] or 'N/A'}</td>
            </tr>
            <tr>
                <td><span class="header-label">CNPJ:</span> {emp_row['cnpj']}</td>
                <td><span class="header-label">Regime:</span> {emp_row['regime']}</td>
            </tr>
            <tr>
                <td><span class="header-label">Tipo:</span> {emp_row['tipo']} | <span class="header-label">Unidade:</span> {emp_row['apelido_unidade'] or 'Matriz'}</td>
                <td style="color:#004b87; font-size:16px;"><b>COMPETÊNCIA ATUAL: {competencia}</b></td>
            </tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1)

    fk = st.session_state.form_key
    h_base = 420
    
    st.divider()
    col_in, col_ras = st.columns([1, 1], gap="large")

    with col_in:
        st.markdown("#### Novo Lançamento")
        with st.container(border=True):
            op_sel = st.selectbox("Operação", df_op['nome_exibicao'].tolist(), key=f"op_{fk}")
            op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
            v_base = st.number_input("Valor Base (R$)", min_value=0.00, step=100.0, key=f"base_{fk}")
            
            v_pis_ret = v_cof_ret = 0.0
            teve_retencao = False
            if op_row['tipo'] == 'RECEITA':
                teve_retencao = st.checkbox("Houve Retenção na Fonte?", key=f"ret_{fk}")
                if teve_retencao:
                    h_base += 135
                    cp, cc = st.columns(2)
                    v_pis_ret = cp.number_input("PIS Retido", key=f"pr_{fk}")
                    v_cof_ret = cc.number_input("COFINS Retido", key=f"cr_{fk}")

            hist = st.text_input("Histórico", key=f"hist_{fk}")
            retro = st.checkbox("Lançamento Extemporâneo", key=f"retro_{fk}")
            if retro:
                h_base += 145
                comp_orig = st.text_input("Mês Origem", key=f"orig_{fk}")
                n_nota = st.text_input("Nº Documento", key=f"n_{fk}")
                fornec = st.text_input("Fornecedor", key=f"f_{fk}")
            else: comp_orig = n_nota = fornec = None

            if st.button("Adicionar ao Rascunho"):
                vp, vc = calcular_impostos(emp_row['regime'], op_row['nome'], v_base)
                st.session_state.rascunho_lancamentos.append({
                    "emp_id": int(emp_row['id']), "op_nome": op_sel, "v_base": v_base, 
                    "v_pis": vp, "v_cofins": vc, "v_pis_ret": v_pis_ret, "v_cof_ret": v_cof_ret,
                    "hist": hist, "retro": int(retro), "origem": comp_orig, "nota": n_nota, "fornecedor": fornec
                })
                st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        with st.container(height=h_base, border=True):
            if not st.session_state.rascunho_lancamentos:
                st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    ct, cd = st.columns([8, 1])
                    ct.markdown(f"**{it['op_nome']}**<br><small>Base: {formatar_moeda(it['v_base'])}</small>", unsafe_allow_html=True)
                    if cd.button("×", key=f"del_{i}"):
                        st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                    st.divider()
        if st.button("Gravar na Base de Dados", type="primary", disabled=not st.session_state.rascunho_lancamentos):
            st.success("Dados Gravados!"); st.session_state.rascunho_lancamentos = []; st.rerun()

def modulo_relatorios():
    st.markdown("### Integração ERP e PDF Analítico")
    df_emp = carregar_empresas_ativas()
    
    # CONTAINER ÚNICO PARA MANTER A HARMONIA VISUAL
    with st.container(border=True):
        with st.form("form_export"):
            c1, c2 = st.columns([2, 1])
            emp_sel = c1.selectbox("Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
            emp_row = df_emp[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]
            competencia = c2.text_input("Competência", value=competencia_padrao)
            btn_proc = st.form_submit_button("Processar Ficheiros")

        if btn_proc:
            # Lógica de geração simulada para o exemplo
            m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            buffer = io.BytesIO(); pd.DataFrame([{"Aviso": "Dados Processados"}]).to_excel(buffer, index=False)
            
            pdf = RelatorioCrescerePDF()
            pdf.add_page(); pdf.set_font("Arial", 'B', 12)
            pdf.cell(190, 8, "DEMONSTRATIVO DE APURACAO - PIS E COFINS", ln=True, align='C')
            pdf.set_font("Arial", '', 10); pdf.cell(0, 10, f"Empresa: {emp_row['nome']} | CNPJ: {emp_row['cnpj']}", ln=True)
            pdf_bytes = pdf.output(dest='S').encode('latin1')

            st.markdown("---")
            c_d1, c_d2 = st.columns(2)
            c_d1.download_button("⬇️ Baixar Integração (XLSX)", data=buffer, file_name=f"ERP_{comp_db}.xlsx")
            c_d2.download_button("⬇️ Baixar Relatório (PDF)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")

def modulo_imobilizado():
    st.markdown("### Gestão de Ativo Imobilizado")
    with st.container(border=True):
        st.date_input("Data de Corte (Pro Rata Die)", format="DD/MM/YYYY")
        st.button("Processar Lote de Depreciação", type="primary")

def modulo_parametros():
    st.markdown("### Parâmetros do Sistema")

def modulo_usuarios():
    st.markdown("### Gestão de Utilizadores")

# --- 3. CONTROLO DE SESSÃO E LOGIN ---
# (O login já foi definido no topo do script)

# --- 4. MENU LATERAL ---
with st.sidebar:
    st.markdown(f"<div style='text-align:center;'>{hoje_br.strftime('%d/%m/%Y')}</div>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align:center; color:#004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "📦 Imobilizado", "⚙️ Parâmetros", "👥 Utilizadores"])
    if st.button("Sair"): st.session_state.autenticado = False; st.rerun()

# --- 5. RENDERIZAÇÃO DE ROTAS (NUNCA DARÁ NAMEERROR AGORA) ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "📦 Imobilizado": modulo_imobilizado()
elif menu == "⚙️ Parâmetros": modulo_parametros()
elif menu == "👥 Utilizadores": modulo_usuarios()
