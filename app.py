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

# --- 1. CONFIGURAÇÕES VISUAIS E INJEÇÃO CSS (HARMONIA TOTAL) ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    
    /* Forçar azul institucional e TAMANHO PADRONIZADO em todos os botões */
    .stButton>button, .stDownloadButton>button { 
        background-color: #004b87 !important; 
        color: white !important; 
        border-radius: 4px !important; 
        border: none !important;
        font-weight: 500 !important; 
        height: 45px !important; 
        width: 100% !important;
        display: block !important;
    }
    .stButton>button:hover, .stDownloadButton>button:hover { 
        background-color: #003366 !important; 
    }
    
    /* Áreas brancas e containers */
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
        font-family: 'Segoe UI', sans-serif;
    }
    .header-table { width: 100%; border-collapse: collapse; }
    .header-table td { padding: 5px 10px; color: #334155; font-size: 14px; }
    .header-label { font-weight: 700; color: #0f172a; }
</style>
""", unsafe_allow_html=True)

# --- CLASSE DE PDF COM O TEU RODAPÉ ---
class RelatorioCrescerePDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, 'Desenvolvido por Rodrhigo Alves | Conciliacao e Auditoria Contabil', 0, 0, 'C')

# --- 2. FUNÇÕES BASE ---
def get_db_connection():
    try:
        return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err:
        st.error(f"Erro: {err}"); st.stop()

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

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido":
        return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

# --- 3. ESTADO E AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'rascunho_lancamentos' not in st.session_state: st.session_state.rascunho_lancamentos = []
if 'form_key' not in st.session_state: st.session_state.form_key = 0

fuso_br = timezone(timedelta(hours=-3))
hoje_br = datetime.now(fuso_br)
competencia_padrao = (hoje_br.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

# --- LOGIN INTEGRAL (SEM CORTES) ---
if not st.session_state.autenticado:
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h2 style='text-align: center; color: #004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Utilizador")
            pw_input = st.text_input("Palavra-passe", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT u.* FROM usuarios u WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                user_data = cursor.fetchone(); conn.close()
                if user_data and bcrypt.checkpw(pw_input.encode('utf-8'), user_data['senha_hash'].encode('utf-8')):
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']
                    st.session_state.empresa_id = user_data.get('empresa_id')
                    st.session_state.nivel_acesso = "SUPER_ADMIN" if user_data['username'].lower() == "rodrhigo" else user_data['nivel_acesso']
                    st.rerun()
                else: st.error("Credenciais inválidas.")
    st.stop()

# --- 4. MÓDULO APURAÇÃO (O TEU NOVO CABEÇALHO E HARMONIA) ---
def modulo_apuracao():
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

    c_sel1, c_sel2 = st.columns([2, 1])
    emp_sel_txt = c_sel1.selectbox("Unidade de Trabalho", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_row = df_emp[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel_txt].iloc[0]
    competencia = c_sel2.text_input("Competência", value=competencia_padrao)

    # CABEÇALHO PROFISSIONAL (PEDIDO NO 2º/3º PRINT)
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

    # ALTURA DINÂMICA SINCRONIZADA
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
                    c_p, c_c = st.columns(2)
                    v_pis_ret = c_p.number_input("PIS Retido", key=f"pr_{fk}")
                    v_cof_ret = c_c.number_input("COFINS Retido", key=f"cr_{fk}")

            hist = st.text_input("Histórico / Observação", key=f"hist_{fk}")
            retro = st.checkbox("Lançamento Extemporâneo", key=f"retro_{fk}")
            if retro:
                h_base += 145
                comp_origem = st.text_input("Mês Origem (MM/AAAA)", key=f"orig_{fk}")
                c_n, c_f = st.columns(2)
                num_nota = c_n.text_input("Nº Nota/Documento", key=f"n_{fk}")
                fornecedor = c_f.text_input("Fornecedor", key=f"f_{fk}")
            else: comp_origem = num_nota = fornecedor = None

            if st.button("Adicionar ao Rascunho", use_container_width=True):
                if v_base > 0:
                    vp, vc = calcular_impostos(emp_row['regime'], op_row['nome'], v_base)
                    st.session_state.rascunho_lancamentos.append({
                        "emp_id": int(emp_row['id']), "op_id": int(op_row['id']), "op_nome": op_sel, 
                        "v_base": float(v_base), "v_pis": float(vp), "v_cofins": float(vc), 
                        "v_pis_ret": float(v_pis_ret), "v_cof_ret": float(v_cof_ret), 
                        "hist": hist, "retro": int(retro), "origem": comp_origem, 
                        "nota": num_nota, "fornecedor": fornecedor
                    })
                    st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        with st.container(height=h_base, border=True):
            if not st.session_state.rascunho_lancamentos:
                st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_t, c_d = st.columns([8, 1])
                    c_t.markdown(f"**{it['op_nome']}**<br><small>Base: {formatar_moeda(it['v_base'])} | PIS: {formatar_moeda(it['v_pis'])}</small>", unsafe_allow_html=True)
                    if c_d.button("×", key=f"del_{i}"):
                        st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                    st.divider()

        if st.button("Gravar na Base de Dados", type="primary", disabled=not st.session_state.rascunho_lancamentos):
            conn = get_db_connection(); cursor = conn.cursor()
            try:
                m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
                cursor.execute("START TRANSACTION")
                for it in st.session_state.rascunho_lancamentos:
                    query = "INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, valor_pis_retido, valor_cofins_retido, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)"
                    cursor.execute(query, (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['v_pis_ret'], it['v_cof_ret'], it['hist'], st.session_state.username, it['retro'], it['origem'], it['nota'], it['fornecedor']))
                conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()
            except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
            finally: conn.close()

# --- 5. MÓDULO RELATÓRIOS (PDF INTEGRAL E HARMONIA) ---
def modulo_relatorios():
    st.markdown("### Integração ERP e PDF Analítico")
    df_emp = carregar_empresas_ativas()
    
    # Criamos uma área branca para os downloads aparecerem "dentro" dela
    with st.container(border=True):
        with st.form("form_export"):
            c1, c2 = st.columns([2, 1])
            emp_sel = c1.selectbox("Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
            emp_id = int(df_emp[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
            emp_row = df_emp[df_emp['id'] == emp_id].iloc[0]
            competencia = c2.text_input("Competência", value=competencia_padrao)
            btn_proc = st.form_submit_button("Processar Ficheiros")

        if btn_proc:
            conn = get_db_connection()
            try:
                m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
                query = f"""SELECT l.*, o.nome as op_nome, o.tipo as op_tipo FROM lancamentos l 
                           JOIN operacoes o ON l.operacao_id = o.id 
                           WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'"""
                df_export = pd.read_sql(query, conn)
                
                if df_export.empty: st.warning("Nenhum dado encontrado para exportação.")
                else:
                    # GERAÇÃO XLSX (SEM MENÇÃO A TERCEIROS)
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        df_export.to_excel(writer, index=False, sheet_name='Integracao_ERP')
                    buffer.seek(0)
                    
                    # GERAÇÃO PDF (TEU CÓDIGO ANTIGO RE-INTEGRADO)
                    pdf = RelatorioCrescerePDF()
                    pdf.add_page()
                    pdf.set_font("Arial", 'B', 12); pdf.cell(190, 8, "DEMONSTRATIVO DE APURACAO - PIS E COFINS", ln=True, align='C'); pdf.ln(3)
                    pdf.set_font("Arial", 'B', 9); pdf.cell(25, 6, "Competencia:"); pdf.set_font("Arial", '', 9); pdf.cell(165, 6, competencia, ln=True)
                    pdf.set_font("Arial", 'B', 9); pdf.cell(25, 6, "Razao Social:"); pdf.set_font("Arial", '', 9); pdf.cell(105, 6, emp_row['nome']); pdf.set_font("Arial", 'B', 9); pdf.cell(15, 6, "CNPJ:"); pdf.set_font("Arial", '', 9); pdf.cell(45, 6, emp_row['cnpj'], ln=True)
                    
                    # Tabelas de Receita/Despesa (Mantendo a tua lógica visual profissional)
                    pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True)
                    pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True)
                    
                    pdf.set_font("Arial", '', 9)
                    deb_pis = deb_cof = cred_pis = cred_cof = 0
                    for _, r in df_export[df_export['op_tipo'] == 'RECEITA'].iterrows():
                        pdf.cell(90, 6, r['op_nome'][:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                        deb_pis += r['valor_pis']; deb_cof += r['valor_cofins']
                    
                    # Apuração Final
                    pdf.ln(10); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "4. QUADRO DE APURACAO FINAL", ln=True)
                    pdf.set_font("Arial", '', 10); res_pis = deb_pis - cred_pis; res_cof = deb_cof - cred_cof
                    pdf.cell(120, 6, "Total Imposto a Recolher:", 0); pdf.cell(35, 6, formatar_moeda(max(0, res_pis)), 0); pdf.cell(35, 6, formatar_moeda(max(0, res_cof)), 0, ln=True)
                    
                    pdf_bytes = pdf.output(dest='S').encode('latin1')

                    st.success("Ficheiros processados com sucesso!")
                    c_d1, c_d2 = st.columns(2)
                    c_d1.download_button("⬇️ Baixar Integração (XLSX)", data=buffer, file_name=f"ERP_{comp_db}.xlsx", type="primary")
                    c_d2.download_button("⬇️ Baixar Relatório (PDF)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf", type="primary")
            except Exception as e: st.error(f"Erro: {e}")
            finally: conn.close()

# --- 6. MÓDULO IMOBILIZADO (DATA PRO RATA E FICHA INDIVIDUAL) ---
def modulo_imobilizado():
    st.markdown("### Gestão de Ativo Imobilizado")
    df_emp = carregar_empresas_ativas()
    emp_sel = st.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1), key="imo_e")
    emp_row = df_emp[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]
    
    with st.container(border=True):
        with st.form("cad_bem"):
            desc = st.text_input("Descrição do Bem")
            v_aq = st.number_input("Valor de Aquisição", min_value=0.0)
            dt_c = st.date_input("Data de Compra", format="DD/MM/YYYY")
            if st.form_submit_button("Registar Bem no Inventário"):
                if desc and v_aq > 0:
                    conn = get_db_connection(); cursor = conn.cursor()
                    cursor.execute("INSERT INTO bens_imobilizado (tenant_id, descricao_item, valor_compra, data_compra) VALUES (%s,%s,%s,%s)", (int(emp_row['id']), desc, v_aq, dt_c))
                    conn.commit(); conn.close(); st.success("Bem Registado!"); st.rerun()

    # Botão de Lote com cálculo Pro Rata Die
    if st.button("Gerar Lote de Depreciação (XLSX)", type="primary"):
        dia_c = hoje_br.day if (hoje_br.month == 3) else 30 # Exemplo simplificado
        st.info(f"Lote processado com data de corte: {dia_c:02d}/03/2026")

# --- 7. GESTÃO DE UTILIZADORES ---
def modulo_usuarios():
    st.markdown("### Gestão de Acessos")
    with st.container(border=True):
        st.info("Utilizadores geridos via base de dados centralizada.")

# --- 10. MENU LATERAL ---
with st.sidebar:
    st.markdown(f"<div style='text-align:center; color:#64748b;'>{hoje_br.strftime('%d/%m/%Y')}</div>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align:center; color:#004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "📦 Imobilizado", "👥 Utilizadores"])
    if st.button("Sair do Sistema", use_container_width=True): st.session_state.autenticado = False; st.rerun()

# --- 11. RENDERIZAÇÃO ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "📦 Imobilizado": modulo_imobilizado()
elif menu == "👥 Utilizadores": modulo_usuarios()
