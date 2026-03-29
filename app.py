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

# --- 1. CONFIGURAÇÕES VISUAIS E INJEÇÃO CSS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button, .stDownloadButton>button { 
        background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; width: 100%; transition: all 0.2s; 
    }
    .stButton>button:hover, .stDownloadButton>button:hover { 
        background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); 
    }
    div[data-testid="stForm"], .css-1d391kg, .stExpander, div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] { 
        background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;
    }
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# --- CLASSE DE PDF PADRONIZADA (CABEÇALHO E RODAPÉ) ---
class RelatorioCrescerePDF(FPDF):
    def add_cabecalho(self, empresa_nome, empresa_cnpj, titulo_relatorio, periodo=""):
        self.set_font("Arial", 'B', 14)
        self.cell(0, 6, empresa_nome, ln=True, align='L')
        self.set_font("Arial", '', 10)
        self.cell(0, 6, f"CNPJ: {empresa_cnpj}", ln=True, align='L')
        self.ln(5)
        self.set_font("Arial", 'B', 12)
        self.cell(0, 8, titulo_relatorio, ln=True, align='C')
        if periodo:
            self.set_font("Arial", '', 10)
            self.cell(0, 6, f"Periodo de Analise: {periodo}", ln=True, align='C')
        self.set_font("Arial", '', 9)
        fuso_br = timezone(timedelta(hours=-3))
        self.cell(0, 6, f"Gerado em: {datetime.now(fuso_br).strftime('%d/%m/%Y')}", ln=True, align='C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 5, 'Desenvolvido por Rodrhigo Alves | Conciliacao e Auditoria Contabil', 0, 1, 'C')
        self.cell(0, 5, f'Pagina {self.page_no()}', 0, 0, 'C')

# --- 2. CONEXÃO E CACHE ---
def get_db_connection():
    try:
        return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err:
        st.error(f"Erro crítico de ligação à base de dados: {err}")
        st.stop()

@st.cache_data(ttl=300)
def carregar_operacoes():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
    conn.close()
    return df

@st.cache_data(ttl=300)
def carregar_empresas_ativas():
    conn = get_db_connection()
    df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo, apelido_unidade, cnae, endereco, conta_transf_pis, conta_transf_cofins FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
    conn.close()
    return df

def verificar_senha(senha_plana, hash_banco): return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))
def gerar_hash_senha(senha_plana): return bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
def formatar_moeda(valor): return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
def consultar_cnpj(cnpj_limpo):
    try:
        res = requests.get(f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}", timeout=10)
        return res.json() if res.status_code == 200 else None
    except: return None

# --- 3. MOTOR DE CÁLCULO ---
def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido":
        return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

# --- 4. CONTROLO DE ESTADO E AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'dados_form' not in st.session_state: st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""}
if 'rascunho_lancamentos' not in st.session_state: st.session_state.rascunho_lancamentos = []
if 'form_key' not in st.session_state: st.session_state.form_key = 0

fuso_br = timezone(timedelta(hours=-3))
hoje_br = datetime.now(fuso_br)
competencia_padrao = (hoje_br.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

if not st.session_state.autenticado:
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h2 style='text-align: center; color: #004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Utilizador")
            pw_input = st.text_input("Palavra-passe", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT u.* FROM usuarios u WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                user_data = cursor.fetchone()
                conn.close()
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']
                    st.session_state.empresa_id = user_data.get('empresa_id')
                    st.session_state.nivel_acesso = "SUPER_ADMIN" if user_data['username'].lower() == "rodrhigo" else user_data['nivel_acesso']
                    st.rerun()
                else: st.error("Credenciais inválidas ou utilizador inativo.")
    st.stop()

# --- 5. MÓDULO GESTÃO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("### Gestão de Empresas e Unidades")
    tab_cad, tab_lista = st.tabs(["Novo Registo", "Unidades Registadas"])
    with tab_cad:
        c_busca, c_btn = st.columns([3, 1])
        with c_busca: cnpj_input = st.text_input("CNPJ para busca automática na Receita Federal:")
        with c_btn:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            if st.button("Consultar CNPJ", use_container_width=True):
                res = consultar_cnpj(cnpj_input.replace(".","").replace("/","").replace("-",""))
                if res and res.get('status') != 'ERROR':
                    st.session_state.dados_form.update({"nome": res.get('nome', ''), "fantasia": res.get('fantasia', ''), "cnpj": res.get('cnpj', ''), "cnae": res.get('atividade_principal', [{}])[0].get('code', ''), "endereco": f"{res.get('logradouro', '')}, {res.get('numero', '')} - {res.get('bairro', '')}, {res.get('municipio', '')}/{res.get('uf', '')}"})
                    st.rerun()
        st.divider()
        f = st.session_state.dados_form
        with st.form("form_empresa"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
            c3, c4, c5, c_apelido = st.columns([2, 1.5, 1.5, 2])
            cnpj = c3.text_input("CNPJ", value=f['cnpj'])
            lista_regimes = ["Lucro Real", "Lucro Presumido", "Simples Nacional", "Simples Nacional - Excesso", "MEI", "Arbitrado", "Imune/Isenta", "Inativa"]
            idx_regime = lista_regimes.index(f.get('regime')) if f.get('regime') in lista_regimes else 0
            regime = c4.selectbox("Regime", lista_regimes, index=idx_regime)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f.get('tipo') == "Matriz" else 1)
            apelido = c_apelido.text_input("Apelido da Unidade", value=f.get('apelido_unidade', ''))
            c6, c7 = st.columns([1, 3])
            cnae = c6.text_input("CNAE", value=f['cnae'])
            endereco = c7.text_input("Endereço", value=f['endereco'])
            if st.form_submit_button("Gravar Unidade", use_container_width=True):
                if not nome or not cnpj: st.error("Razão Social e CNPJ são obrigatórios.")
                else:
                    conn = get_db_connection(); cursor = conn.cursor()
                    try:
                        if f['id']: cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, int(f['id'])))
                        else: cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido))
                        conn.commit(); carregar_empresas_ativas.clear(); st.success("Gravado com sucesso!"); st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""}
                    except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
                    finally: conn.close()
    with tab_lista:
        df = carregar_empresas_ativas()
        for _, row in df.iterrows():
            col_info, col_btn = st.columns([5, 1])
            col_info.markdown(f"**{row['nome']}** ({row['apelido_unidade'] or row['tipo']})<br><small>CNPJ: {row['cnpj']}</small>", unsafe_allow_html=True)
            if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                conn = get_db_connection()
                df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={int(row['id'])}", conn)
                conn.close()
                st.session_state.dados_form = df_edit.iloc[0].to_dict()
                st.rerun()
            st.divider()

# --- 6. MÓDULO APURAÇÃO (COM VISUALIZAÇÃO E AUDITORIA) ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
        if df_emp.empty: st.warning("Nenhuma unidade vinculada a este utilizador."); return

    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Operador", value=st.session_state.usuario_logado, disabled=True)

    st.divider()
    col_in, col_ras = st.columns([1, 1], gap="large")

    with col_in:
        st.markdown("#### Novo Lançamento")
        fk = st.session_state.form_key
        
        op_sel = st.selectbox("Operação", df_op['nome_exibicao'].tolist(), key=f"op_{fk}")
        op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
        
        v_base = st.number_input("Valor Total da Fatura / Base (R$)", min_value=0.00, step=100.0, key=f"base_{fk}")
        v_pis_ret = v_cof_ret = 0.0
        teve_retencao = False
        
        c_retro, c_origem = st.columns([1, 1])
        retro = c_retro.checkbox("Lançamento Extemporâneo", key=f"retro_{fk}")
        comp_origem = c_origem.text_input("Mês de Origem (MM/AAAA)", disabled=not retro, key=f"origem_{fk}")

        if op_row['tipo'] == 'RECEITA' and not retro:
            teve_retencao = st.checkbox("Houve Retenção na Fonte nesta fatura?", key=f"check_ret_{fk}")
            if teve_retencao:
                st.info("Informe os valores exatos retidos no documento para dedução direta.")
                c_p, c_c = st.columns(2)
                v_pis_ret = c_p.number_input("Valor PIS Retido (R$)", min_value=0.00, step=10.0, key=f"p_ret_{fk}")
                v_cof_ret = c_c.number_input("Valor COFINS Retido (R$)", min_value=0.00, step=10.0, key=f"c_ret_{fk}")

        hist = st.text_input("Histórico / Observação (Obrigatório para Extemporâneo)", key=f"hist_{fk}")
        
        exige_doc = retro or teve_retencao
        if exige_doc:
            c_nota, c_forn = st.columns([1, 2])
            num_nota = c_nota.text_input("Nº do Documento", key=f"nota_{fk}")
            fornecedor = c_forn.text_input("Tomador / Fornecedor", key=f"forn_{fk}")
        else: num_nota = fornecedor = None
        
        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if v_base <= 0: st.warning("A base de cálculo deve ser maior que zero.")
            elif teve_retencao and v_pis_ret == 0 and v_cof_ret == 0: st.warning("Informe os valores retidos.")
            elif exige_doc and (not num_nota or not fornecedor or (retro and not comp_origem) or (retro and not hist)): 
                st.error("Para Retenções e Extemporâneos, o Nº do Documento, Fornecedor, Mês de Origem e Histórico são obrigatórios.")
            else:
                vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
                st.session_state.rascunho_lancamentos.append({"emp_id": int(emp_id), "op_id": int(op_row['id']), "op_nome": op_sel, "v_base": float(v_base), "v_pis": float(vp), "v_cofins": float(vc), "v_pis_ret": float(v_pis_ret), "v_cof_ret": float(v_cof_ret), "hist": hist, "retro": int(retro), "origem": comp_origem if retro else None, "nota": num_nota, "fornecedor": fornecedor})
                st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        with st.container(height=390, border=True): 
            if not st.session_state.rascunho_lancamentos: st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    retro_badge = f" <span style='color:red;font-size:10px;'>(EXTEMP: {it['origem']})</span>" if it['retro'] == 1 else ""
                    ret_badge = f" <span style='color:orange;font-size:10px;'>(RETENÇÃO)</span>" if float(it.get('v_pis_ret', 0)) > 0 or float(it.get('v_cof_ret', 0)) > 0 else ""
                    c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}{ret_badge}<br>PIS: {formatar_moeda(it['v_pis']).replace('$', '&#36;')} | COF: {formatar_moeda(it['v_cofins']).replace('$', '&#36;')}</small>", unsafe_allow_html=True)
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base']).replace('$', '&#36;')}</span>", unsafe_allow_html=True)
                    if c_del.button("×", key=f"del_{i}"): st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                    st.markdown("<hr style='margin: 5px 0;'>", unsafe_allow_html=True)
        if st.button("Gravar na Base de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos)==0):
            conn = get_db_connection(); cursor = conn.cursor()
            try:
                m, a = competencia.split('/')
                comp_db = f"{a}-{m.zfill(2)}"
                cursor.execute("START TRANSACTION")
                for it in st.session_state.rascunho_lancamentos:
                    query = """INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, valor_pis_retido, valor_cofins_retido, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)"""
                    c_origem_db = None
                    if it['origem']: 
                        mo, ao = it['origem'].split('/')
                        c_origem_db = f"{ao}-{mo.zfill(2)}"
                    cursor.execute(query, (int(it['emp_id']), int(it['op_id']), comp_db, float(it['v_base']), float(it['v_pis']), float(it['v_cofins']), float(it.get('v_pis_ret', 0)), float(it.get('v_cof_ret', 0)), it['hist'], st.session_state.username, int(it['retro']), c_origem_db, it['nota'], it['fornecedor']))
                conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Gravado com sucesso no banco de dados!"); st.rerun()
            except Exception as e: conn.rollback(); st.error(f"Erro no banco: {e}")
            finally: conn.close()

# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO (COM SALDOS E AUDITORIA) ---
def modulo_relatorios():
    st.markdown("### Exportação para ERP e PDF Analítico")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id: 
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

    c1, c2 = st.columns([2, 1])
    emp_sel = c1.selectbox("Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    emp_row = df_emp[df_emp['id'] == emp_id].iloc[0]
    competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    
    consolidar = st.checkbox("Consolidar apuração com Filiais (mesma Raiz CNPJ)")
    
    if st.button("Gerar Ficheiros e Analisar Saldos"):
        conn = get_db_connection()
        try:
            m, a = competencia.split('/')
            comp_db = f"{a}-{m.zfill(2)}"
            
            # Lógica de Consolidação
            if consolidar:
                raiz_cnpj = emp_row['cnpj'][:10]
                query_ids = f"SELECT id FROM empresas WHERE cnpj LIKE '{raiz_cnpj}%'"
                df_ids = pd.read_sql(query_ids, conn)
                lista_ids = tuple(df_ids['id'].tolist())
                filtro_empresa = f"l.empresa_id = {lista_ids[0]}" if len(lista_ids) == 1 else f"l.empresa_id IN {lista_ids}"
                nome_relatorio_pdf = f"{emp_row['nome']} (CONSOLIDADO MATRIZ E FILIAIS)"
            else:
                filtro_empresa = f"l.empresa_id = {emp_id}"
                nome_relatorio_pdf = f"{emp_row['nome']}"

            # Recuperar lançamentos do mês atual
            query = f"""SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, o.conta_deb_pis, o.conta_cred_pis, o.conta_deb_cof, o.conta_cred_cof, o.conta_deb_custo, o.conta_cred_custo FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE {filtro_empresa} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'"""
            df_export = pd.read_sql(query, conn)

            # --- MOTOR DE SALDO CREDOR ACUMULADO ---
            # Soma de todo o histórico anterior a esta competência para calcular se sobrou crédito
            query_historico = f"""SELECT o.tipo as op_tipo, SUM(l.valor_pis) as t_pis, SUM(l.valor_cofins) as t_cof, SUM(l.valor_pis_retido) as t_pis_ret, SUM(l.valor_cofins_retido) as t_cof_ret FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE {filtro_empresa} AND l.competencia < '{comp_db}' AND l.status_auditoria = 'ATIVO' GROUP BY o.tipo"""
            df_hist = pd.read_sql(query_historico, conn)
            
            saldo_ant_pis = 0.0
            saldo_ant_cof = 0.0
            
            if not df_hist.empty:
                hist_deb = df_hist[df_hist['op_tipo'] == 'RECEITA']
                hist_cred = df_hist[df_hist['op_tipo'] == 'DESPESA']
                
                h_pis_deb = hist_deb['t_pis'].sum() if not hist_deb.empty else 0
                h_cof_deb = hist_deb['t_cof'].sum() if not hist_deb.empty else 0
                h_pis_ret = hist_deb['t_pis_ret'].sum() if not hist_deb.empty else 0
                h_cof_ret = hist_deb['t_cof_ret'].sum() if not hist_deb.empty else 0
                
                h_pis_cred = hist_cred['t_pis'].sum() if not hist_cred.empty else 0
                h_cof_cred = hist_cred['t_cof'].sum() if not hist_cred.empty else 0
                
                # Se Débito (o que devia) - Crédito (o que tomou) - Retenção for negativo, sobrou saldo
                res_hist_pis = h_pis_deb - h_pis_cred - h_pis_ret
                res_hist_cof = h_cof_deb - h_cof_cred - h_cof_ret
                
                if res_hist_pis < 0: saldo_ant_pis = abs(res_hist_pis)
                if res_hist_cof < 0: saldo_ant_cof = abs(res_hist_cof)

            # --- EXPORTAÇÃO EXCEL ---
            linhas_excel = []
            if not df_export.empty:
                for _, row in df_export.iterrows():
                    data_str = row['data_lancamento'].strftime('%d/%m/%Y') if pd.notnull(row['data_lancamento']) else ''
                    if pd.notnull(row['conta_deb_pis']) and pd.notnull(row['conta_cred_pis']): linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_pis']).replace('.', ''), "Credito": str(row['conta_cred_pis']).replace('.', ''), "Data": data_str, "Valor": row['valor_pis'], "Historico": f"PIS - {row['op_nome']} COMP {competencia}", "Nr.Documento": row['num_nota'] or row['id']})
                    if pd.notnull(row['conta_deb_cof']) and pd.notnull(row['conta_cred_cof']): linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_cof']).replace('.', ''), "Credito": str(row['conta_cred_cof']).replace('.', ''), "Data": data_str, "Valor": row['valor_cofins'], "Historico": f"COF - {row['op_nome']} COMP {competencia}", "Nr.Documento": row['num_nota'] or row['id']})
            
            df_xlsx = pd.DataFrame(linhas_excel)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Lançamentos')
            buffer.seek(0)
            
            # --- GERAÇÃO DO PDF ---
            pdf = RelatorioCrescerePDF()
            pdf.add_page()
            pdf.add_cabecalho(nome_relatorio_pdf, emp_row['cnpj'], "*** DEMONSTRATIVO DE APURACAO - PIS E COFINS ***", competencia)
            
            deb_pis = deb_cof = cred_pis = cred_cof = ret_pis = ret_cof = ext_pis = ext_cof = 0
            
            pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True)
            pdf.set_font("Arial", '', 9)
            if not df_export.empty:
                for _, r in df_export[(df_export['op_tipo'] == 'RECEITA') & (df_export['origem_retroativa'] == 0)].iterrows(): 
                    pdf.cell(90, 6, f"{r['op_nome']}"[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    deb_pis += r['valor_pis']; deb_cof += r['valor_cofins']
                    ret_pis += r['valor_pis_retido']; ret_cof += r['valor_cofins_retido']
            
            pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "2. INSUMOS, CREDITOS E EXTEMPORANEOS", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
            if not df_export.empty:
                for _, r in df_export[df_export['op_tipo'] == 'DESPESA'].iterrows(): 
                    pdf.cell(90, 6, f"{r['op_nome']}"[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    if r['origem_retroativa'] == 1:
                        ext_pis += r['valor_pis']; ext_cof += r['valor_cofins']
                    else:
                        cred_pis += r['valor_pis']; cred_cof += r['valor_cofins']
            
            pdf.ln(10); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "3. QUADRO DE APURACAO FINAL", ln=True); pdf.set_font("Arial", '', 10)
            pdf.cell(120, 6, "A) Total de Debitos:", 0); pdf.cell(35, 6, formatar_moeda(deb_pis), 0); pdf.cell(35, 6, formatar_moeda(deb_cof), 0, ln=True)
            pdf.cell(120, 6, "B) (-) Creditos do Mes:", 0); pdf.cell(35, 6, formatar_moeda(cred_pis), 0); pdf.cell(35, 6, formatar_moeda(cred_cof), 0, ln=True)
            pdf.cell(120, 6, "C) (-) Retencoes na Fonte:", 0); pdf.cell(35, 6, formatar_moeda(ret_pis), 0); pdf.cell(35, 6, formatar_moeda(ret_cof), 0, ln=True)
            pdf.cell(120, 6, "D) (-) Creditos Extemporaneos:", 0); pdf.cell(35, 6, formatar_moeda(ext_pis), 0); pdf.cell(35, 6, formatar_moeda(ext_cof), 0, ln=True)
            pdf.cell(120, 6, "E) (-) Saldo Credor Mes Anterior:", 0); pdf.cell(35, 6, formatar_moeda(saldo_ant_pis), 0); pdf.cell(35, 6, formatar_moeda(saldo_ant_cof), 0, ln=True)
            
            res_pis = deb_pis - cred_pis - ret_pis - ext_pis - saldo_ant_pis
            res_cof = deb_cof - cred_cof - ret_cof - ext_cof - saldo_ant_cof
            
            pdf.set_font("Arial", 'B', 11)
            pdf.cell(120, 8, "(=) TOTAL IMPOSTO A RECOLHER:", 0); pdf.cell(35, 8, formatar_moeda(max(0, res_pis)), 0); pdf.cell(35, 8, formatar_moeda(max(0, res_cof)), 0, ln=True)
            
            pdf.set_font("Arial", 'B', 9); pdf.set_text_color(0, 100, 0)
            pdf.cell(120, 6, "(=) SALDO CREDOR TRANSPORTADO PARA O MES SEGUINTE:", 0); pdf.cell(35, 6, formatar_moeda(abs(res_pis) if res_pis < 0 else 0), 0); pdf.cell(35, 6, formatar_moeda(abs(res_cof) if res_cof < 0 else 0), 0, ln=True)
            pdf.set_text_color(0, 0, 0)

            # --- ANEXO DE AUDITORIA ---
            pdf.add_page()
            pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "ANEXO I - DETALHAMENTO E NOTAS DE AUDITORIA FISCAL", ln=True)
            
            # 1. Extemporâneos Apropriados Neste Mês
            df_ext = df_export[df_export['origem_retroativa'] == 1]
            if not df_ext.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - APROVEITAMENTO DE CREDITO EXTEMPORANEO:", ln=True); pdf.set_font("Arial", '', 8)
                msg_base = "Esta apuracao inclui a apropriacao de credito tributario originado em competencia anterior, lancado tempestivamente neste periodo para fins de restituicao/compensacao."
                pdf.multi_cell(0, 4, msg_base); pdf.ln(2)
                for _, r in df_ext.iterrows():
                    pdf.multi_cell(0, 4, f"- Origem: {r['competencia_origem']} | Doc: {r['num_nota']} - {r['fornecedor']} | PIS: {formatar_moeda(r['valor_pis'])} | COF: {formatar_moeda(r['valor_cofins'])}\n  Justificativa: {r['historico']}")
            
            # 2. Extemporâneos Originados Neste Mês (mas usados no futuro)
            query_futuros = f"""SELECT * FROM lancamentos l WHERE {filtro_empresa} AND l.competencia_origem = '{comp_db}' AND l.competencia != '{comp_db}' AND l.status_auditoria = 'ATIVO'"""
            df_fut = pd.read_sql(query_futuros, conn)
            if not df_fut.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - CREDITO APROPRIADO EXTEMPORANEAMENTE (NO FUTURO):", ln=True); pdf.set_font("Arial", '', 8)
                for _, r in df_fut.iterrows():
                    msg_fut = f"Registra-se que o documento fiscal {r['num_nota']}, emitido por {r['fornecedor']} nesta competencia ({comp_db}), nao compos a base de calculo original deste demonstrativo. O respectivo credito foi apropriado extemporaneamente na competencia {r['competencia']}.\nMotivo: {r['historico']}"
                    pdf.multi_cell(0, 4, msg_fut); pdf.ln(2)

            pdf_bytes = pdf.output(dest='S').encode('latin1')
            
            st.success("Ficheiros processados e saldos auditados com sucesso!")
            c_btn1, c_btn2, _ = st.columns([1, 1, 2])
            if not df_xlsx.empty: c_btn1.download_button("Baixar XLSX (Exportação ERP)", data=buffer.getvalue(), file_name=f"LCTOS_{comp_db}.xlsx")
            c_btn2.download_button("Baixar PDF (Demonstrativo Fiscal)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
        except Exception as e: 
            st.error(f"Erro na geração: {e}")
        finally: 
            conn.close()

# --- 7.5 MÓDULO IMOBILIZADO E DEPRECIAÇÃO ---
def modulo_imobilizado():
    st.markdown("### Gestão de Ativo Imobilizado")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id: df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
    
    c_emp, c_vazio = st.columns([2, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1), key="imo_emp")
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1) == emp_sel].iloc[0]['id'])
    row_emp_ativa = df_emp[df_emp['id'] == emp_id].iloc[0]

    st.divider()
    col_in, col_ras = st.columns([1, 1], gap="large")
    conn = get_db_connection()
    df_g = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id}", conn)
    conn.close()

    with col_in:
        st.markdown("#### Cadastro do Bem")
        if df_g.empty: st.warning("Cadastre os Grupos em Parâmetros Contábeis primeiro.")
        else:
            with st.form("form_novo_bem"):
                g_sel = st.selectbox("Grupo / Espécie", df_g['nome_grupo'].tolist())
                g_row = df_g[df_g['nome_grupo'] == g_sel].iloc[0]
                desc = st.text_input("Descrição Básica do Bem")
                c_m, c_p = st.columns(2)
                marca = c_m.text_input("Marca / Modelo (Opcional)")
                num_serie = c_p.text_input("Nº Série / Placa (Opcional)")
                c_pl, c_loc = st.columns(2)
                plaqueta = c_pl.text_input("Plaqueta / Património (Opcional)")
                localizacao = c_loc.text_input("Localização / Depto (Opcional)")
                c_n, c_f = st.columns(2)
                nf = c_n.text_input("Nº da Nota Fiscal (Opcional)")
                forn = c_f.text_input("Fornecedor (Opcional)")
                c_v, c_d = st.columns(2)
                v_aq = c_v.number_input("Valor de Aquisição (R$)", min_value=0.0, step=100.0)
                dt_c = c_d.date_input("Data da Compra")
                regra_cred = st.selectbox("Regra de Crédito PIS/COFINS", ["NENHUM (Sem Crédito)", "MENSAL (Pela Depreciação)", "INTEGRAL (Mês de Aquisição)"])

                if st.form_submit_button("Registar no Inventário"):
                    if not desc or v_aq <= 0: st.error("Descrição e Valor são obrigatórios.")
                    elif dt_c > hoje_br.date(): st.error("A Data de Compra não pode ser no futuro.")
                    else:
                        conn = get_db_connection(); cursor = conn.cursor()
                        cursor.execute("""INSERT INTO bens_imobilizado (tenant_id, grupo_id, descricao_item, marca_modelo, num_serie_placa, plaqueta, localizacao, numero_nota_fiscal, nome_fornecedor, data_compra, valor_compra, regra_credito) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (int(emp_id), int(g_row['id']), desc, marca, num_serie, plaqueta, localizacao, nf, forn, dt_c, float(v_aq), regra_cred))
                        conn.commit(); conn.close(); st.success("Bem registado com sucesso!"); st.rerun()

    with col_ras:
        st.markdown("#### Processamento em Lote (Exportação ERP)")
        with st.container(height=260, border=True):
            c_a, c_m = st.columns([1, 2])
            a_proc = c_a.number_input("Ano Base", value=hoje_br.year)
            meses_opcoes = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
            meses_selecionados = c_m.multiselect("Meses para Processar", options=list(meses_opcoes.keys()), format_func=lambda x: meses_opcoes[x], default=[hoje_br.month])
            
            meses_futuros = [m for m in meses_selecionados if a_proc > hoje_br.year or (a_proc == hoje_br.year and m > hoje_br.month)]
            
            if meses_futuros: st.error("ERRO: O processamento bloqueou a apropriação de despesas de meses futuros (CPC 27).")
            elif st.button("Gerar Exportação de Lançamentos (XLSX)"):
                conn = get_db_connection()
                df_bens = pd.read_sql(f"SELECT b.*, g.taxa_anual_percentual FROM bens_imobilizado b JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id} AND b.status = 'ativo'", conn)
                conn.close()
                if not df_bens.empty:
                    linhas = []
                    for m_proc in sorted(meses_selecionados):
                        last_day = calendar.monthrange(a_proc, m_proc)[1]
                        
                        # Pro Rata Die - Se for o mês corrente, para no dia de hoje. Se for fechado, usa o último dia.
                        dia_final_calculo = hoje_br.day if (a_proc == hoje_br.year and m_proc == hoje_br.month) else last_day
                        data_lancamento_str = f"{dia_final_calculo:02d}/{m_proc:02d}/{a_proc}"
                        
                        for _, b in df_bens.iterrows():
                            dt_compra = b['data_compra']
                            if a_proc < dt_compra.year or (a_proc == dt_compra.year and m_proc < dt_compra.month): continue
                            
                            dia_inicial = dt_compra.day if (a_proc == dt_compra.year and m_proc == dt_compra.month) else 1
                            dias_uso = max(0, dia_final_calculo - dia_inicial + 1)
                            
                            cota_diaria = (float(b['valor_compra']) * (float(b['taxa_anual_percentual'])/100)) / 365.0
                            cota = cota_diaria * dias_uso
                            
                            linhas.append({"Lancto Aut.": "", "Debito": str(b.get('conta_despesa', '')).replace('.', ''), "Credito": str(b.get('conta_dep_acumulada', '')).replace('.', ''), "Data": data_lancamento_str, "Valor": round(cota, 2), "Historico": f"DEPRECIACAO REF {m_proc:02d}/{a_proc} - {b['descricao_item']}", "Nr.Documento": b['numero_nota_fiscal'] or b['id']})
                    
                    df_xlsx = pd.DataFrame(linhas)
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Depreciacao')
                    buffer.seek(0)
                    st.download_button("Gerar Exportação de Lançamentos (XLSX)", data=buffer.getvalue(), file_name=f"DEPREC_LOTE_{a_proc}.xlsx")

    st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
    st.markdown("#### Consultar Inventário Dinâmico")
    
    mostrar_inativos = st.checkbox("Exibir bens inativos (baixados nos últimos 5 anos)")
    limite_anos = hoje_br.year - 5
    filtro_status = "1=1" if mostrar_inativos else "b.status = 'ativo'"
    if mostrar_inativos: filtro_status += f" AND (b.data_baixa IS NULL OR YEAR(b.data_baixa) >= {limite_anos})"

    conn = get_db_connection()
    df_todos = pd.read_sql(f"SELECT b.*, g.taxa_anual_percentual, g.nome_grupo FROM bens_imobilizado b JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id} AND {filtro_status}", conn)
    conn.close()

    if not df_todos.empty:
        dados_visao = []
        for _, rb in df_todos.iterrows():
            dt_compra = rb['data_compra']
            dt_ref = hoje_br.date() if rb['status'] == 'ativo' else rb['data_baixa']
            
            dias_totais = max(0, (dt_ref - dt_compra).days)
            cota_dia = (float(rb['valor_compra']) * (float(rb['taxa_anual_percentual'])/100)) / 365.0
            dep_acumulada = min(float(rb['valor_compra']), cota_dia * dias_totais)
            valor_residual = max(0.0, float(rb['valor_compra']) - dep_acumulada)
            
            dados_visao.append({"Descrição": f"{rb['descricao_item']} {rb['marca_modelo'] or ''}".strip(), "Data Aquisição": dt_compra.strftime('%d/%m/%Y'), "Valor Original": formatar_moeda(rb['valor_compra']), "Taxa (%)": f"{rb['taxa_anual_percentual']}%", "Valor Residual": formatar_moeda(valor_residual), "Situação": rb['status'].upper()})
        
        st.dataframe(pd.DataFrame(dados_visao), use_container_width=True, hide_index=True)

        if st.button("Gerar Relação Geral (PDF)"):
            pdf = RelatorioCrescerePDF()
            pdf.add_page()
            pdf.add_cabecalho(row_emp_ativa['nome'], row_emp_ativa['cnpj'], "*** RELACAO GERAL DO ATIVO IMOBILIZADO ***")
            
            pdf.set_font("Arial", 'B', 8)
            # Inserida a coluna Valor Residual com redimensionamento
            pdf.cell(50, 6, "Descricao", 1); pdf.cell(25, 6, "Aquisicao", 1); pdf.cell(30, 6, "Valor Original", 1); pdf.cell(15, 6, "Taxa", 1); pdf.cell(30, 6, "Valor Residual", 1); pdf.cell(40, 6, "Status", 1, ln=True)
            pdf.set_font("Arial", '', 8)
            for _, r in pd.DataFrame(dados_visao).iterrows():
                pdf.cell(50, 6, r['Descrição'][:30], 1); pdf.cell(25, 6, r['Data Aquisição'], 1); pdf.cell(30, 6, r['Valor Original'], 1); pdf.cell(15, 6, r['Taxa (%)'], 1); pdf.cell(30, 6, r['Valor Residual'], 1); pdf.cell(40, 6, r['Situação'], 1, ln=True)
            
            st.download_button("Baixar Relação PDF", data=pdf.output(dest='S').encode('latin1'), file_name=f"IMOBILIZADO_{emp_id}.pdf")

    st.markdown("---")
    busca = st.text_input("Pesquisar Item Específico para Simulação de Ganho de Capital:")
    if busca:
        df_res = df_todos[(df_todos['descricao_item'].str.contains(busca, case=False, na=False))]
        if not df_res.empty:
            for _, rb in df_res.iterrows():
                dt_compra = rb['data_compra']
                cota_dia = (float(rb['valor_compra']) * (float(rb['taxa_anual_percentual'])/100)) / 365.0
                
                with st.expander(f"{rb['descricao_item']} - {rb['marca_modelo'] or ''}"):
                    # Simulador LIVRE para datas futuras (Projeção)
                    dt_simulada = st.date_input("Data para Simulação/Venda", value=hoje_br, key=f"sim_date_{rb['id']}")
                    if dt_simulada < dt_compra: st.error("Data de simulação não pode ser anterior à data de aquisição.")
                    else:
                        dias_simulados = max(0, (dt_simulada - dt_compra).days)
                        dep_simulada = min(float(rb['valor_compra']), cota_dia * dias_simulados)
                        valor_res_simulado = max(0.0, float(rb['valor_compra']) - dep_simulada)
                        
                        pdf = RelatorioCrescerePDF()
                        pdf.add_page()
                        pdf.add_cabecalho(row_emp_ativa['nome'], row_emp_ativa['cnpj'], "*** FICHA INDIVIDUAL DE ATIVO IMOBILIZADO ***")
                        
                        pdf.set_font("Arial", 'B', 10); pdf.cell(30, 6, "Bem:"); pdf.set_font("Arial", '', 10); pdf.cell(0, 6, f"{rb['descricao_item']} - {rb['marca_modelo'] or ''}", ln=True)
                        pdf.set_font("Arial", 'B', 10); pdf.cell(30, 6, "Data Compra:"); pdf.set_font("Arial", '', 10); pdf.cell(0, 6, f"{dt_compra.strftime('%d/%m/%Y')} (Valor Original: {formatar_moeda(rb['valor_compra'])})", ln=True)
                        pdf.ln(5)
                        pdf.set_font("Arial", 'B', 12); pdf.cell(0, 8, "PROJECAO DE VENDA / GANHO DE CAPITAL", ln=True)
                        pdf.set_font("Arial", '', 10)
                        pdf.cell(0, 6, f"Data da Projecao: {dt_simulada.strftime('%d/%m/%Y')}", ln=True)
                        pdf.cell(0, 6, f"Depreciacao Acumulada Projetada (Pro Rata Die - {dias_simulados} dias): {formatar_moeda(dep_simulada)}", ln=True)
                        pdf.set_font("Arial", 'B', 11)
                        pdf.cell(0, 8, f"VALOR RESIDUAL (CUSTO DO BEM): {formatar_moeda(valor_res_simulado)}", ln=True)
                        
                        st.download_button("Gerar Ficha de Projeção (PDF)", data=pdf.output(dest='S').encode('latin1'), file_name=f"FICHA_{rb['id']}.pdf", key=f"btn_ficha_{rb['id']}")

# Omitidos módulos de Parâmetros e Gestão de Utilizadores para manter foco na regra de negócio atualizada (eles continuam idênticos à versão anterior).
# --- 10. MENU LATERAL ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'><b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação"])
    if st.button("Encerrar Sessão", use_container_width=True): st.session_state.autenticado = False; st.rerun()

if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "Imobilizado & Depreciação": modulo_imobilizado()
