import streamlit as st
import mysql.connector
import pandas as pd
import numpy as np
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
    .stButton>button, .stDownloadButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; width: 100%; transition: all 0.2s; }
    .stButton>button:hover, .stDownloadButton>button:hover { background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    div[data-testid="stForm"], .css-1d391kg, .stExpander, div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; }
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# --- FUNÇÕES AUXILIARES DE LIMPEZA E FORMATAÇÃO ---
def limpar_texto(v):
    return "" if pd.isna(v) or str(v).strip().lower() == 'nan' else str(v).strip()

def formatar_nome_empresa(r):
    apelido = limpar_texto(r.get('apelido_unidade', ''))
    if not apelido: apelido = limpar_texto(r.get('tipo', ''))
    return f"{r['nome']} - {apelido}"

def formatar_moeda(valor): 
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- CLASSE DE PDF PADRONIZADA ---
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

# --- FUNÇÃO PADRÃO PARA EXPORTAÇÃO ERP ---
def criar_linha_erp(deb, cred, data, valor, cod_hist, hist, nr_doc):
    return {
        "Lancto Aut.": "",
        "Debito": str(deb).replace('.', '') if pd.notnull(deb) and deb else "",
        "Credito": str(cred).replace('.', '') if pd.notnull(cred) and cred else "",
        "Data": data,
        "Valor": round(float(valor), 2),
        "Cod. Historico": limpar_texto(cod_hist),
        "Historico": hist,
        "Ccusto Debito": "",
        "Ccusto Credito": "",
        "Nr.Documento": limpar_texto(nr_doc),
        "Complemento": ""
    }

# --- 2. CONEXÃO E CACHE ---
def get_db_connection():
    try: return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err: st.error(f"Erro crítico de banco de dados: {err}"); st.stop()

@st.cache_data(ttl=300)
def carregar_operacoes():
    conn = get_db_connection(); df = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn); conn.close(); return df

@st.cache_data(ttl=300)
def carregar_empresas_ativas():
    conn = get_db_connection(); df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO'", conn); conn.close(); return df

def verificar_senha(senha_plana, hash_banco): return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))
def gerar_hash_senha(senha_plana): return bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
def consultar_cnpj(cnpj_limpo):
    try: res = requests.get(f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}", timeout=10); return res.json() if res.status_code == 200 else None
    except: return None

# --- 3. MOTOR DE CÁLCULO ---
def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido": return (valor_base * 0.0065, valor_base * 0.03)
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
                else: st.error("Credenciais inválidas.")
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
            nome = c1.text_input("Razão Social", value=limpar_texto(f['nome']))
            fanta = c2.text_input("Nome Fantasia", value=limpar_texto(f['fantasia']))
            c3, c4, c5, c_apelido = st.columns([2, 1.5, 1.5, 2])
            cnpj = c3.text_input("CNPJ", value=limpar_texto(f['cnpj']))
            lista_regimes = ["Lucro Real", "Lucro Presumido", "Simples Nacional", "Simples Nacional - Excesso", "MEI", "Arbitrado", "Imune/Isenta", "Inativa"]
            regime = c4.selectbox("Regime", lista_regimes, index=lista_regimes.index(f.get('regime')) if f.get('regime') in lista_regimes else 0)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f.get('tipo') == "Matriz" else 1)
            apelido = c_apelido.text_input("Apelido da Unidade", value=limpar_texto(f.get('apelido_unidade', '')))
            c6, c7 = st.columns([1, 3])
            cnae = c6.text_input("CNAE", value=limpar_texto(f['cnae']))
            endereco = c7.text_input("Endereço", value=limpar_texto(f['endereco']))
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
            nome_display = formatar_nome_empresa(row)
            col_info.markdown(f"**{nome_display}**<br><small>CNPJ: {row['cnpj']}</small>", unsafe_allow_html=True)
            if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                conn = get_db_connection(); df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={int(row['id'])}", conn); conn.close()
                st.session_state.dados_form = df_edit.iloc[0].to_dict(); st.rerun()
            st.divider()

# --- 6. MÓDULO APURAÇÃO ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
        if df_emp.empty: st.warning("Nenhuma unidade vinculada a este utilizador."); return

    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(formatar_nome_empresa, axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(formatar_nome_empresa, axis=1) == emp_sel].iloc[0]['id'])
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
                st.info("Informe os valores retidos para dedução direta.")
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
                st.error("Para Retenções e Extemporâneos, o Nº do Documento, Fornecedor, Mês Origem e Histórico são obrigatórios.")
            else:
                vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
                st.session_state.rascunho_lancamentos.append({
                    "id_unico": str(datetime.now().timestamp()),
                    "emp_id": int(emp_id),
                    "op_id": int(op_row['id']),
                    "op_nome": op_sel,
                    "v_base": float(v_base),
                    "v_pis": float(vp),
                    "v_cofins": float(vc),
                    "v_pis_ret": float(v_pis_ret),
                    "v_cof_ret": float(v_cof_ret),
                    "hist": hist,
                    "retro": int(retro),
                    "origem": comp_origem if retro else None,
                    "nota": num_nota,
                    "fornecedor": fornecedor
                })
                st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        
        def remover_do_rascunho(idx):
            st.session_state.rascunho_lancamentos.pop(idx)

        with st.container(height=390, border=True):
            if not st.session_state.rascunho_lancamentos:
                st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    retro_badge = f" <span style='color:red;font-size:10px;'>(EXTEMP: {it['origem']})</span>" if it['retro'] == 1 else ""
                    ret_badge = f" <span style='color:orange;font-size:10px;'>(RETENÇÃO)</span>" if float(it.get('v_pis_ret', 0)) > 0 or float(it.get('v_cof_ret', 0)) > 0 else ""
                    doc_str = f" | Doc: {it['nota']}" if it.get('nota') else ""
                    forn_str = f" | Forn: {it['fornecedor']}" if it.get('fornecedor') else ""
                    hist_str = f"<br>Histórico: {it['hist']}" if it.get('hist') else ""
                    
                    c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}{ret_badge}<br>PIS: {formatar_moeda(it['v_pis']).replace('$', '&#36;')} | COF: {formatar_moeda(it['v_cofins']).replace('$', '&#36;')}<br><span style='color:#64748b;'>{doc_str}{forn_str}{hist_str}</span></small>", unsafe_allow_html=True)
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base']).replace('$', '&#36;')}</span>", unsafe_allow_html=True)
                    
                    c_del.button("×", key=f"del_{it['id_unico']}", on_click=remover_do_rascunho, args=(i,))
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

    st.markdown("---")
    st.markdown("#### Lançamentos Gravados nesta Competência (Auditoria DB)")
    try:
        m, a = competencia.split('/')
        comp_db = f"{a}-{m.zfill(2)}"
        conn = get_db_connection()
        query_gravados = f"SELECT l.id, o.nome as operacao, l.valor_base, l.valor_pis, l.valor_cofins, l.historico, l.usuario_registro FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'"
        df_gravados = pd.read_sql(query_gravados, conn)
        conn.close()
        
        if df_gravados.empty:
            st.info("Nenhum lançamento ativo salvo na base de dados para esta competência.")
        else:
            st.dataframe(df_gravados, use_container_width=True, hide_index=True)
            with st.expander("Estornar / Inativar Lançamento com Histórico"):
                st.warning("Boas práticas proíbem a exclusão silenciosa. Informe o ID para inativar o lançamento.")
                with st.form("form_edicao_lancamento"):
                    c_id, c_motivo = st.columns([1, 3])
                    id_alvo = c_id.selectbox("ID do Lançamento", df_gravados['id'].tolist())
                    motivo = c_motivo.text_input("Motivo do Estorno/Cancelamento (Obrigatório)")
                    
                    if st.form_submit_button("Confirmar Estorno"):
                        if not motivo or len(motivo.strip()) < 5: st.error("É obrigatório informar um motivo válido para a auditoria.")
                        else:
                            conn = get_db_connection(); cursor = conn.cursor()
                            historico_add = f" | [ESTORNADO por {st.session_state.username}]: {motivo}"
                            cursor.execute("UPDATE lancamentos SET status_auditoria = 'INATIVO', historico = CONCAT(IFNULL(historico,''), %s) WHERE id = %s", (historico_add, int(id_alvo)))
                            conn.commit(); conn.close()
                            st.success("Lançamento inativado e auditado com sucesso!"); st.rerun()
    except Exception as e:
        st.error("Verifique o formato da competência.")

# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO ---
def modulo_relatorios():
    st.markdown("### Exportação para ERP e PDF Analítico")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id: df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

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
            
            if consolidar:
                raiz_cnpj = emp_row['cnpj'][:10]
                df_ids = pd.read_sql(f"SELECT id FROM empresas WHERE cnpj LIKE '{raiz_cnpj}%'", conn)
                lista_ids = tuple(df_ids['id'].tolist())
                filtro_empresa = f"l.empresa_id = {lista_ids[0]}" if len(lista_ids) == 1 else f"l.empresa_id IN {lista_ids}"
                nome_relatorio_pdf = f"{emp_row['nome']} (CONSOLIDADO MATRIZ E FILIAIS)"
            else:
                filtro_empresa = f"l.empresa_id = {emp_id}"
                nome_relatorio_pdf = f"{emp_row['nome']}"

            query = f"SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, e.apelido_unidade, e.tipo as emp_tipo, o.conta_deb_pis, o.conta_cred_pis, o.pis_h_codigo, o.pis_h_texto, o.conta_deb_cof, o.conta_cred_cof, o.cofins_h_codigo, o.cofins_h_texto, o.conta_deb_custo, o.conta_cred_custo, o.custo_h_codigo, o.custo_h_texto FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id JOIN empresas e ON l.empresa_id = e.id WHERE {filtro_empresa} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'"
            df_export = pd.read_sql(query, conn)

            query_hist = f"SELECT o.tipo as op_tipo, SUM(l.valor_pis) as t_pis, SUM(l.valor_cofins) as t_cof, SUM(l.valor_pis_retido) as t_pis_ret, SUM(l.valor_cofins_retido) as t_cof_ret FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE {filtro_empresa} AND l.competencia < '{comp_db}' AND l.status_auditoria = 'ATIVO' GROUP BY o.tipo"
            df_hist = pd.read_sql(query_hist, conn)
            
            saldo_ant_pis = 0.0; saldo_ant_cof = 0.0
            if not df_hist.empty:
                hist_deb = df_hist[df_hist['op_tipo'] == 'RECEITA']
                hist_cred = df_hist[df_hist['op_tipo'] == 'DESPESA']
                res_hist_pis = (hist_deb['t_pis'].sum() if not hist_deb.empty else 0) - (hist_cred['t_pis'].sum() if not hist_cred.empty else 0) - (hist_deb['t_pis_ret'].sum() if not hist_deb.empty else 0)
                res_hist_cof = (hist_deb['t_cof'].sum() if not hist_deb.empty else 0) - (hist_cred['t_cof'].sum() if not hist_cred.empty else 0) - (hist_deb['t_cof_ret'].sum() if not hist_deb.empty else 0)
                if res_hist_pis < 0: saldo_ant_pis = abs(res_hist_pis)
                if res_hist_cof < 0: saldo_ant_cof = abs(res_hist_cof)

            # --- EXPORTAÇÃO EXCEL ---
            linhas_excel = []
            if not df_export.empty:
                def p_txt(txt, op_nome): return txt.replace("{operacao}", op_nome).replace("{competencia}", competencia) if txt else f"VLR REF {op_nome} COMP {competencia}"
                for _, r in df_export.iterrows():
                    d_str = r['data_lancamento'].strftime('%d/%m/%Y') if pd.notnull(r['data_lancamento']) else ''
                    doc = r['num_nota'] or r['id']
                    if pd.notnull(r['conta_deb_pis']) and pd.notnull(r['conta_cred_pis']):
                        linhas_excel.append(criar_linha_erp(r['conta_deb_pis'], r['conta_cred_pis'], d_str, r['valor_pis'], r.get('pis_h_codigo'), f"PIS - {p_txt(r.get('pis_h_texto'), r['op_nome'])}", doc))
                    if pd.notnull(r['conta_deb_cof']) and pd.notnull(r['conta_cred_cof']):
                        linhas_excel.append(criar_linha_erp(r['conta_deb_cof'], r['conta_cred_cof'], d_str, r['valor_cofins'], r.get('cofins_h_codigo'), f"COF - {p_txt(r.get('cofins_h_texto'), r['op_nome'])}", doc))
                    if pd.notnull(r['conta_deb_custo']) and pd.notnull(r['conta_cred_custo']):
                        v_custo = r['valor_base'] - r['valor_pis'] - r['valor_cofins']
                        linhas_excel.append(criar_linha_erp(r['conta_deb_custo'], r['conta_cred_custo'], d_str, v_custo, r.get('custo_h_codigo'), f"CUSTO LIQ - {p_txt(r.get('custo_h_texto'), r['op_nome'])}", doc))
            
            df_xlsx = pd.DataFrame(linhas_excel)
            buffer = io.BytesIO()
            colunas_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
            if df_xlsx.empty: df_xlsx = pd.DataFrame(columns=colunas_erp)
            else: df_xlsx = df_xlsx[colunas_erp]
            
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Lançamentos')
            
            # --- GERAÇÃO DO PDF ---
            pdf = RelatorioCrescerePDF()
            pdf.add_page(); pdf.add_cabecalho(nome_relatorio_pdf, emp_row['cnpj'], "*** DEMONSTRATIVO DE APURACAO - PIS E COFINS ***", competencia)
            deb_pis = deb_cof = cred_pis = cred_cof = ret_pis = ret_cof = ext_pis = ext_cof = 0
            
            pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
            if not df_export.empty:
                for _, r in df_export[(df_export['op_tipo'] == 'RECEITA') & (df_export['origem_retroativa'] == 0)].iterrows():
                    desc_op = r['op_nome']
                    apelido_clean = limpar_texto(r.get('apelido_unidade', ''))
                    if consolidar and r['emp_tipo'] == 'Filial':
                        desc_op += f" ({apelido_clean or 'Filial'})"
                    pdf.cell(90, 6, desc_op[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    deb_pis += r['valor_pis']; deb_cof += r['valor_cofins']; ret_pis += r['valor_pis_retido']; ret_cof += r['valor_cofins_retido']
            
            pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "2. INSUMOS, CREDITOS E EXTEMPORANEOS", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
            if not df_export.empty:
                for _, r in df_export[df_export['op_tipo'] == 'DESPESA'].iterrows():
                    desc_op = r['op_nome']
                    apelido_clean = limpar_texto(r.get('apelido_unidade', ''))
                    if consolidar and r['emp_tipo'] == 'Filial':
                        desc_op += f" ({apelido_clean or 'Filial'})"
                    pdf.cell(90, 6, desc_op[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    if r['origem_retroativa'] == 1: ext_pis += r['valor_pis']; ext_cof += r['valor_cofins']
                    else: cred_pis += r['valor_pis']; cred_cof += r['valor_cofins']
            
            pdf.ln(10); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "3. QUADRO DE APURACAO FINAL", ln=True); pdf.set_font("Arial", '', 10)
            pdf.cell(120, 6, "A) Total de Debitos:", 0); pdf.cell(35, 6, formatar_moeda(deb_pis), 0); pdf.cell(35, 6, formatar_moeda(deb_cof), 0, ln=True)
            pdf.cell(120, 6, "B) (-) Creditos do Mes:", 0); pdf.cell(35, 6, formatar_moeda(cred_pis), 0); pdf.cell(35, 6, formatar_moeda(cred_cof), 0, ln=True)
            pdf.cell(120, 6, "C) (-) Retencoes na Fonte:", 0); pdf.cell(35, 6, formatar_moeda(ret_pis), 0); pdf.cell(35, 6, formatar_moeda(ret_cof), 0, ln=True)
            pdf.cell(120, 6, "D) (-) Creditos Extemporaneos:", 0); pdf.cell(35, 6, formatar_moeda(ext_pis), 0); pdf.cell(35, 6, formatar_moeda(ext_cof), 0, ln=True)
            pdf.cell(120, 6, "E) (-) Saldo Credor Mes Anterior:", 0); pdf.cell(35, 6, formatar_moeda(saldo_ant_pis), 0); pdf.cell(35, 6, formatar_moeda(saldo_ant_cof), 0, ln=True)
            
            res_pis = deb_pis - cred_pis - ret_pis - ext_pis - saldo_ant_pis; res_cof = deb_cof - cred_cof - ret_cof - ext_cof - saldo_ant_cof
            
            pdf.set_font("Arial", 'B', 11)
            pdf.cell(120, 8, "(=) TOTAL IMPOSTO A RECOLHER:", 0); pdf.cell(35, 8, formatar_moeda(max(0, res_pis)), 0); pdf.cell(35, 8, formatar_moeda(max(0, res_cof)), 0, ln=True)
            pdf.set_font("Arial", 'B', 9); pdf.set_text_color(0, 100, 0)
            pdf.cell(120, 6, "(=) SALDO CREDOR TRANSPORTADO PARA O MES SEGUINTE:", 0); pdf.cell(35, 6, formatar_moeda(abs(res_pis) if res_pis < 0 else 0), 0); pdf.cell(35, 6, formatar_moeda(abs(res_cof) if res_cof < 0 else 0), 0, ln=True)
            pdf.set_text_color(0, 0, 0)

            # --- ANEXO DE AUDITORIA ---
            pdf.add_page(); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "ANEXO I - DETALHAMENTO E NOTAS DE AUDITORIA FISCAL", ln=True)
            df_ext = df_export[df_export['origem_retroativa'] == 1]
            if not df_ext.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - APROVEITAMENTO DE CREDITO EXTEMPORANEO:", ln=True); pdf.set_font("Arial", '', 8)
                pdf.multi_cell(0, 4, "Esta apuracao inclui a apropriacao de credito tributario originado em competencia anterior, lancado tempestivamente neste periodo."); pdf.ln(2)
                for _, r in df_ext.iterrows(): pdf.multi_cell(0, 4, f"- Origem: {r['competencia_origem']} | Doc: {r['num_nota']} - {r['fornecedor']} | PIS: {formatar_moeda(r['valor_pis'])} | COF: {formatar_moeda(r['valor_cofins'])}\n  Justificativa: {r['historico']}")
            
            df_fut = pd.read_sql(f"SELECT * FROM lancamentos l WHERE {filtro_empresa} AND l.competencia_origem = '{comp_db}' AND l.competencia != '{comp_db}' AND l.status_auditoria = 'ATIVO'", conn)
            if not df_fut.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - CREDITO APROPRIADO EXTEMPORANEAMENTE (NO FUTURO):", ln=True); pdf.set_font("Arial", '', 8)
                for _, r in df_fut.iterrows(): pdf.multi_cell(0, 4, f"Registra-se que o documento fiscal {r['num_nota']}, emitido por {r['fornecedor']} nesta competencia ({comp_db}), nao compos a base de calculo original deste demonstrativo. O respectivo credito foi apropriado extemporaneamente na competencia {r['competencia']}.\nMotivo: {r['historico']}"); pdf.ln(2)

            pdf_bytes = pdf.output(dest='S').encode('latin1')
            st.success("Ficheiros processados e saldos auditados com sucesso!")
            c_btn1, c_btn2, _ = st.columns([1, 1, 2])
            c_btn1.download_button("Baixar XLSX (Exportação ERP)", data=buffer.getvalue(), file_name=f"LCTOS_{comp_db}.xlsx")
            c_btn2.download_button("Baixar PDF (Demonstrativo Fiscal)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
        except Exception as e: st.error(f"Erro na geração: {e}")
        finally: conn.close()

# --- 7.5 MÓDULO IMOBILIZADO E DEPRECIAÇÃO ---
def modulo_imobilizado():
    st.markdown("### Gestão de Ativo Imobilizado")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id: df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
    
    c_emp, c_vazio = st.columns([2, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(formatar_nome_empresa, axis=1), key="imo_emp")
    emp_id = int(df_emp.loc[df_emp.apply(formatar_nome_empresa, axis=1) == emp_sel].iloc[0]['id'])
    row_emp_ativa = df_emp[df_emp['id'] == emp_id].iloc[0]

    st.divider()
    
    abas = ["Cadastro e Processamento", "Inventário Dinâmico"]
    if st.session_state.nivel_acesso in ["SUPER_ADMIN", "ADMIN"]: abas.append("Manutenção de Ativos (Admin)")
    
    tabs = st.tabs(abas)
    tab_main = tabs[0]
    tab_inv = tabs[1]
    tab_manut = tabs[2] if len(tabs) > 2 else None

    conn = get_db_connection()
    df_g = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id}", conn)
    conn.close()

    with tab_main:
        col_in, col_ras = st.columns([1, 1], gap="large")
        with col_in:
            st.markdown("#### Cadastro do Bem")
            if df_g.empty: st.warning("Cadastre os Grupos em Parâmetros Contábeis primeiro nesta empresa para realizar novos registros.")
            else:
                cenario = st.selectbox("Cenário de Implantação (Estratégia de Depreciação)", [
                    "1. Bem Novo (Folha em Branco - Cálculo Automático)", 
                    "2. Cliente Novo (Saldo de Partida - Sem Histórico Mensal)", 
                    "3. Continuidade (Memória de Cálculo - Cota Fixa Histórica)"
                ])
                
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
                    v_aq = c_v.number_input("Valor de Aquisição Base (R$)", min_value=0.0, step=100.0)
                    dt_c = c_d.date_input("Data da Compra Original")
                    regra_cred = st.selectbox("Regra de Crédito PIS/COFINS", ["NENHUM (Sem Crédito)", "MENSAL (Pela Depreciação)", "INTEGRAL (Mês de Aquisição)"])
                    
                    if "2" in cenario or "3" in cenario:
                        st.markdown("---")
                        st.markdown("##### Saldo de Implantação / Histórico")
                        c_si, c_vi = st.columns(2)
                        dt_saldo = c_si.date_input("Data Base do Balancete (Última Posição)")
                        v_saldo = c_vi.number_input("Valor Residual no Balancete (R$)", min_value=0.0, step=100.0)
                    else:
                        dt_saldo = None; v_saldo = 0.0

                    if "3" in cenario:
                        st.info("Informe a cota exata que você vinha depreciando. O sistema criará um Plano de Voo para zerar o bem usando este valor mensal.")
                        v_cota_fixa = st.number_input("Valor da Parcela/Cota Mensal Histórica (R$)", min_value=0.0, step=10.0)
                    else:
                        v_cota_fixa = 0.0

                    if st.form_submit_button("Registar no Inventário"):
                        if not desc or v_aq <= 0: st.error("Descrição e Valor de Aquisição são obrigatórios.")
                        elif dt_c > hoje_br.date(): st.error("A Data de Compra não pode ser no futuro.")
                        elif ("2" in cenario or "3" in cenario) and v_saldo <= 0: st.error("No cenário escolhido, o Valor Residual é obrigatório.")
                        elif "3" in cenario and v_cota_fixa <= 0: st.error("No cenário de Continuidade, o valor da cota histórica é obrigatório.")
                        else:
                            conn = get_db_connection(); cursor = conn.cursor()
                            try:
                                dt_s_db = dt_saldo if ("2" in cenario or "3" in cenario) else None
                                v_s_db = float(v_saldo) if ("2" in cenario or "3" in cenario) else 0.0
                                
                                cursor.execute("""INSERT INTO bens_imobilizado (tenant_id, grupo_id, descricao_item, marca_modelo, num_serie_placa, plaqueta, localizacao, numero_nota_fiscal, nome_fornecedor, data_compra, valor_compra, regra_credito, data_saldo_inicial, valor_residual_inicial) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (int(emp_id), int(g_row['id']), desc, marca, num_serie, plaqueta, localizacao, nf, forn, dt_c, float(v_aq), regra_cred, dt_s_db, v_s_db))
                                bem_id = cursor.lastrowid

                                if "3" in cenario and v_cota_fixa > 0 and v_s_db > 0:
                                    saldo_restante = v_s_db
                                    ano_plan = dt_saldo.year
                                    mes_plan = dt_saldo.month
                                    if mes_plan == 12: mes_plan = 1; ano_plan += 1
                                    else: mes_plan += 1
                                    data_plan = date(ano_plan, mes_plan, 1)

                                    while saldo_restante > 0.009:
                                        cota_atual = min(saldo_restante, float(v_cota_fixa))
                                        cursor.execute("INSERT INTO plano_depreciacao_itens (bem_id, mes_referencia, valor_cota, tipo_registro, status_contabil) VALUES (%s, %s, %s, 'PROJETADO', 'PENDENTE')", (bem_id, data_plan.strftime('%Y-%m-%d'), cota_atual))
                                        saldo_restante -= cota_atual
                                        if data_plan.month == 12: data_plan = date(data_plan.year + 1, 1, 1)
                                        else: data_plan = date(data_plan.year, data_plan.month + 1, 1)

                                conn.commit(); st.success("Bem registado com sucesso (e Plano de Voo gerado, se aplicável)!"); st.rerun()
                            except Exception as e: conn.rollback(); st.error(f"Erro ao salvar: {e}")
                            finally: conn.close()

        with col_ras:
            st.markdown("#### Processamento em Lote (Exportação ERP)")
            with st.container(height=380, border=True):
                c_a, c_m = st.columns([1, 2])
                a_proc = c_a.number_input("Ano Base", value=hoje_br.year)
                meses_opcoes = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
                meses_selecionados = c_m.multiselect("Meses para Processar", options=list(meses_opcoes.keys()), format_func=lambda x: meses_opcoes[x], default=[hoje_br.month])
                
                st.markdown("---")
                metodo_calc = st.selectbox("Método de Cálculo (Para itens sem Plano Fixo)", ["Pro Rata Die (Dias Exatos)", "Mês Comercial (30 Dias)"])
                tipo_export = st.radio("Tipo de Exportação", ["Analítica (Item a Item)", "Sintética (Agrupada por Grupo)"])
                
                meses_futuros = [m for m in meses_selecionados if a_proc > hoje_br.year or (a_proc == hoje_br.year and m > hoje_br.month)]
                
                if meses_futuros: st.error("ERRO: O processamento bloqueou a apropriação de despesas de meses futuros (CPC 27).")
                elif st.button("Gerar Exportação de Lançamentos (XLSX)", type="primary"):
                    conn = get_db_connection()
                    df_bens = pd.read_sql(f"SELECT b.*, g.taxa_anual_percentual, g.conta_contabil_despesa, g.conta_contabil_dep_acumulada, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id} AND b.status = 'ativo'", conn)
                    df_planos = pd.read_sql(f"SELECT p.* FROM plano_depreciacao_itens p JOIN bens_imobilizado b ON p.bem_id = b.id WHERE b.tenant_id = {emp_id}", conn)
                    if not df_planos.empty: df_planos['mes_referencia'] = pd.to_datetime(df_planos['mes_referencia']).dt.date
                    conn.close()

                    if not df_bens.empty:
                        linhas = []
                        for m_proc in sorted(meses_selecionados):
                            last_day = calendar.monthrange(a_proc, m_proc)[1]
                            dia_final_calculo = hoje_br.day if (a_proc == hoje_br.year and m_proc == hoje_br.month) else last_day
                            data_lancamento_str = f"{dia_final_calculo:02d}/{m_proc:02d}/{a_proc}"
                            data_ref_plano = date(a_proc, m_proc, 1)
                            
                            registros_calc = []
                            for _, b in df_bens.iterrows():
                                if pd.isna(b.get('taxa_anual_percentual')): continue # Evita falhas caso o grupo esteja corrompido
                                dt_base = b['data_saldo_inicial'] if pd.notnull(b.get('data_saldo_inicial')) else b['data_compra']
                                if a_proc < dt_base.year or (a_proc == dt_base.year and m_proc < dt_base.month): continue
                                
                                cota = 0.0
                                usou_plano = False
                                
                                if not df_planos.empty:
                                    plano_item = df_planos[(df_planos['bem_id'] == b['id']) & (df_planos['mes_referencia'] == data_ref_plano)]
                                    if not plano_item.empty:
                                        cota = float(plano_item.iloc[0]['valor_cota'])
                                        usou_plano = True
                                
                                if not usou_plano:
                                    dia_inicial = dt_base.day if (a_proc == dt_base.year and m_proc == dt_base.month) else 1
                                    base_calc = float(b['valor_compra'])
                                    taxa_anual = float(b['taxa_anual_percentual']) / 100.0
                                    
                                    if metodo_calc == "Mês Comercial (30 Dias)":
                                        dias_comerciais = 30 - dia_inicial + 1 if dia_inicial > 1 else 30
                                        cota = (base_calc * taxa_anual / 360.0) * dias_comerciais
                                    else:
                                        dias_uso = max(0, dia_final_calculo - dia_inicial + 1)
                                        cota = (base_calc * taxa_anual / 365.0) * dias_uso
                                
                                if cota > 0:
                                    c_d_use = b.get('conta_despesa') or b.get('conta_contabil_despesa', '')
                                    c_c_use = b.get('conta_dep_acumulada') or b.get('conta_contabil_dep_acumulada', '')
                                    nome_g_limpo = limpar_texto(b.get('nome_grupo'))
                                    
                                    registros_calc.append({
                                        'c_d_use': c_d_use, 'c_c_use': c_c_use, 'data_lanc': data_lancamento_str, 'cota': cota,
                                        'desc': limpar_texto(b['descricao_item']), 'nf': limpar_texto(b['numero_nota_fiscal']) or b['id'], 'grupo': nome_g_limpo
                                    })
                            
                            if tipo_export == "Sintética (Agrupada por Grupo)":
                                df_calc = pd.DataFrame(registros_calc)
                                if not df_calc.empty:
                                    df_grp = df_calc.groupby(['c_d_use', 'c_c_use', 'grupo', 'data_lanc'])['cota'].sum().reset_index()
                                    for _, r in df_grp.iterrows():
                                        linhas.append(criar_linha_erp(r['c_d_use'], r['c_c_use'], r['data_lanc'], r['cota'], "", f"DEPRECIACAO ACUMULADA - {str(r['grupo']).upper()} NO MES", ""))
                            else:
                                for r in registros_calc:
                                    linhas.append(criar_linha_erp(r['c_d_use'], r['c_c_use'], r['data_lanc'], r['cota'], "", f"DEPRECIACAO REF {m_proc:02d}/{a_proc} - {r['desc']}", r['nf']))
                        
                        df_xlsx = pd.DataFrame(linhas)
                        buffer = io.BytesIO()
                        colunas_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
                        if df_xlsx.empty: df_xlsx = pd.DataFrame(columns=colunas_erp)
                        else: df_xlsx = df_xlsx[colunas_erp]
                        
                        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Depreciacao')
                        st.download_button("Baixar Planilha ERP (XLSX)", data=buffer.getvalue(), file_name=f"DEPREC_{a_proc}.xlsx")

    with tab_inv:
        st.markdown("#### Consultar Inventário Dinâmico")
        mostrar_inativos = st.checkbox("Exibir bens inativos (baixados nos últimos 5 anos)")
        limite_anos = hoje_br.year - 5
        filtro_status = "1=1" if mostrar_inativos else "b.status = 'ativo'"
        if mostrar_inativos: filtro_status += f" AND (b.data_baixa IS NULL OR YEAR(b.data_baixa) >= {limite_anos})"

        conn = get_db_connection()
        df_todos = pd.read_sql(f"SELECT b.*, g.taxa_anual_percentual, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id} AND {filtro_status}", conn)
        df_planos_inv = pd.read_sql(f"SELECT p.* FROM plano_depreciacao_itens p JOIN bens_imobilizado b ON p.bem_id = b.id WHERE b.tenant_id = {emp_id}", conn)
        if not df_planos_inv.empty: df_planos_inv['mes_referencia'] = pd.to_datetime(df_planos_inv['mes_referencia']).dt.date
        conn.close()

        if not df_todos.empty:
            dados_visao = []
            for _, rb in df_todos.iterrows():
                if pd.isna(rb.get('taxa_anual_percentual')): continue
                dt_base = rb['data_saldo_inicial'] if pd.notnull(rb.get('data_saldo_inicial')) else rb['data_compra']
                
                if rb['status'] == 'ativo': dt_ref = hoje_br.date()
                else:
                    dt_ref = rb['data_baixa'] if pd.notnull(rb.get('data_baixa')) else dt_base
                    if isinstance(dt_ref, datetime) or isinstance(dt_ref, pd.Timestamp): dt_ref = dt_ref.date()

                base_calc = float(rb['valor_compra'])
                taxa_anual = float(rb['taxa_anual_percentual']) / 100.0
                saldo_ini = float(rb.get('valor_residual_inicial', 0.0))
                dep_acumulada = 0.0
                
                plano_do_bem = df_planos_inv[df_planos_inv['bem_id'] == rb['id']] if not df_planos_inv.empty else pd.DataFrame()
                
                if not plano_do_bem.empty:
                    dep_acumulada = plano_do_bem[plano_do_bem['mes_referencia'] <= dt_ref]['valor_cota'].sum()
                else:
                    if metodo_calc == "Mês Comercial (30 Dias)":
                        dia_base = min(30, dt_base.day)
                        dia_ref = min(30, dt_ref.day)
                        dias_totais = max(0, (dt_ref.year - dt_base.year) * 360 + (dt_ref.month - dt_base.month) * 30 + (dia_ref - dia_base))
                        dep_acumulada = min(base_calc, (base_calc * taxa_anual / 360.0) * dias_totais)
                    else:
                        dias_totais = max(0, (dt_ref - dt_base).days)
                        dep_acumulada = min(base_calc, (base_calc * taxa_anual / 365.0) * dias_totais)
                
                if pd.notnull(rb.get('data_saldo_inicial')): valor_residual = max(0.0, saldo_ini - dep_acumulada)
                else: valor_residual = max(0.0, base_calc - dep_acumulada)
                
                desc_limpa = limpar_texto(rb.get('descricao_item'))
                marca_limpa = limpar_texto(rb.get('marca_modelo'))
                
                dados_visao.append({"Descrição": f"{desc_limpa} {marca_limpa}".strip(), "Data Ref.": dt_base.strftime('%d/%m/%Y'), "Valor Base": formatar_moeda(rb['valor_compra']), "Taxa (%)": f"{rb['taxa_anual_percentual']}%", "Valor Residual": formatar_moeda(valor_residual), "Situação": rb['status'].upper()})
            
            if dados_visao:
                st.dataframe(pd.DataFrame(dados_visao), use_container_width=True, hide_index=True)

            if st.button("Gerar Relação Geral (PDF)"):
                pdf = RelatorioCrescerePDF()
                pdf.add_page(); pdf.add_cabecalho(row_emp_ativa['nome'], row_emp_ativa['cnpj'], "*** RELACAO GERAL DO ATIVO IMOBILIZADO ***")
                pdf.set_font("Arial", 'B', 8)
                pdf.cell(50, 6, "Descricao", 1); pdf.cell(25, 6, "Data Base", 1); pdf.cell(30, 6, "Valor de Custo", 1); pdf.cell(15, 6, "Taxa", 1); pdf.cell(30, 6, "Valor Residual", 1); pdf.cell(40, 6, "Status", 1, ln=True)
                pdf.set_font("Arial", '', 8)
                for _, r in pd.DataFrame(dados_visao).iterrows():
                    pdf.cell(50, 6, r['Descrição'][:30], 1); pdf.cell(25, 6, r['Data Ref.'], 1); pdf.cell(30, 6, r['Valor Base'], 1); pdf.cell(15, 6, r['Taxa (%)'], 1); pdf.cell(30, 6, r['Valor Residual'], 1); pdf.cell(40, 6, r['Situação'], 1, ln=True)
                st.download_button("Baixar Relação PDF", data=pdf.output(dest='S').encode('latin1'), file_name=f"IMOBILIZADO_{emp_id}.pdf")

        st.markdown("---")
        busca = st.text_input("Pesquisar Item Específico para Simulação de Ganho de Capital / Auditoria:")
        if busca:
            df_res = df_todos[(df_todos['descricao_item'].str.contains(busca, case=False, na=False))]
            if not df_res.empty:
                for _, rb in df_res.iterrows():
                    if pd.isna(rb.get('taxa_anual_percentual')): continue
                    dt_base = rb['data_saldo_inicial'] if pd.notnull(rb.get('data_saldo_inicial')) else rb['data_compra']
                    base_calc = float(rb['valor_compra'])
                    taxa_anual = float(rb['taxa_anual_percentual']) / 100.0
                    saldo_ini = float(rb.get('valor_residual_inicial', 0.0))
                    desc_limpa = limpar_texto(rb.get('descricao_item'))
                    marca_limpa = limpar_texto(rb.get('marca_modelo'))
                    
                    with st.expander(f"Auditoria/Simulação: {desc_limpa} - {marca_limpa}"):
                        plano_do_bem = df_planos_inv[df_planos_inv['bem_id'] == rb['id']] if not df_planos_inv.empty else pd.DataFrame()
                        
                        if not plano_do_bem.empty:
                            st.markdown("###### Plano de Depreciação Ativo (Continuidade)")
                            plano_do_bem['Mês'] = pd.to_datetime(plano_do_bem['mes_referencia']).dt.strftime('%m/%Y')
                            plano_do_bem['Cota (R$)'] = plano_do_bem['valor_cota'].apply(formatar_moeda)
                            st.dataframe(plano_do_bem[['Mês', 'Cota (R$)', 'status_contabil']], use_container_width=True, hide_index=True)

                        dt_simulada = st.date_input("Data para Simulação/Venda", value=hoje_br, key=f"sim_date_{rb['id']}")
                        
                        if pd.notnull(rb.get('data_baixa')):
                            dbx = rb['data_baixa'].date() if isinstance(rb['data_baixa'], datetime) else rb['data_baixa']
                            if dt_simulada > dbx: st.warning(f"O bem já foi baixado em {dbx.strftime('%d/%m/%Y')}.")
                        
                        if dt_simulada < dt_base: st.error("Data de simulação não pode ser anterior à data base.")
                        else:
                            if not plano_do_bem.empty:
                                dep_simulada = plano_do_bem[plano_do_bem['mes_referencia'] <= dt_simulada]['valor_cota'].sum()
                                texto_metodo = "Plano Fixo (Continuidade)"
                            else:
                                if metodo_calc == "Mês Comercial (30 Dias)":
                                    dia_base = min(30, dt_base.day)
                                    dia_sim = min(30, dt_simulada.day)
                                    dias_simulados = max(0, (dt_simulada.year - dt_base.year) * 360 + (dt_simulada.month - dt_base.month) * 30 + (dia_sim - dia_base))
                                    dep_simulada = min(base_calc, (base_calc * taxa_anual / 360.0) * dias_simulados)
                                    texto_metodo = "Mês Comercial - 30 Dias"
                                else:
                                    dias_simulados = max(0, (dt_simulada - dt_base).days)
                                    dep_simulada = min(base_calc, (base_calc * taxa_anual / 365.0) * dias_simulados)
                                    texto_metodo = "Pro Rata Die"

                            if pd.notnull(rb.get('data_saldo_inicial')): valor_res_simulado = max(0.0, saldo_ini - dep_simulada)
                            else: valor_res_simulado = max(0.0, base_calc - dep_simulada)
                            
                            pdf = RelatorioCrescerePDF()
                            pdf.add_page(); pdf.add_cabecalho(row_emp_ativa['nome'], row_emp_ativa['cnpj'], "*** FICHA INDIVIDUAL DE ATIVO IMOBILIZADO ***")
                            pdf.set_font("Arial", 'B', 10); pdf.cell(30, 6, "Bem:"); pdf.set_font("Arial", '', 10); pdf.cell(0, 6, f"{desc_limpa} - {marca_limpa}", ln=True)
                            pdf.set_font("Arial", 'B', 10); pdf.cell(30, 6, "Data Base:"); pdf.set_font("Arial", '', 10); pdf.cell(0, 6, f"{dt_base.strftime('%d/%m/%Y')} (Custo Original: {formatar_moeda(rb['valor_compra'])})", ln=True)
                            pdf.ln(5); pdf.set_font("Arial", 'B', 12); pdf.cell(0, 8, "PROJECAO DE VENDA / GANHO DE CAPITAL", ln=True)
                            pdf.set_font("Arial", '', 10)
                            pdf.cell(0, 6, f"Data da Projecao: {dt_simulada.strftime('%d/%m/%Y')}", ln=True)
                            pdf.cell(0, 6, f"Depreciacao Acumulada Projetada ({texto_metodo}): {formatar_moeda(dep_simulada)}", ln=True)
                            pdf.set_font("Arial", 'B', 11); pdf.cell(0, 8, f"VALOR RESIDUAL (CUSTO DO BEM): {formatar_moeda(valor_res_simulado)}", ln=True)
                            st.download_button("Gerar Ficha de Projeção (PDF)", data=pdf.output(dest='S').encode('latin1'), file_name=f"FICHA_{rb['id']}.pdf", key=f"btn_ficha_{rb['id']}")

    if tab_manut:
        with tab_manut:
            st.markdown("#### Manutenção de Ativos (Edição/Transferência/Exclusão)")
            conn = get_db_connection()
            # Uso do LEFT JOIN para garantir que bens transferidos sem grupo correto apareçam
            df_todos_manut = pd.read_sql(f"SELECT b.*, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id}", conn)
            df_grupos_locais = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id}", conn)
            conn.close()
            
            if df_todos_manut.empty:
                st.info("Nenhum bem cadastrado ou transferido para esta unidade.")
            else:
                bem_sel = st.selectbox("Selecione o Bem para Manutenção", df_todos_manut.apply(lambda r: f"[{r['id']}] {limpar_texto(r['descricao_item'])} - {limpar_texto(r.get('marca_modelo', ''))} ({r['status'].upper()})", axis=1))
                bem_id = int(bem_sel.split("]")[0].replace("[", ""))
                bem_row = df_todos_manut[df_todos_manut['id'] == bem_id].iloc[0]
                
                with st.form("form_manut_bem"):
                    st.markdown("##### Dados do Bem")
                    
                    if df_grupos_locais.empty:
                        st.warning("⚠️ Esta unidade não possui Grupos Contábeis cadastrados. Crie um grupo em Parâmetros Contábeis para poder gerenciar este bem adequadamente.")
                        m_grupo_id = bem_row['grupo_id'] # Mantém o antigo por segurança caso force salvar
                    else:
                        lista_grupos_locais = df_grupos_locais['nome_grupo'].tolist()
                        idx_grp = 0
                        nome_grupo_atual = limpar_texto(bem_row.get('nome_grupo'))
                        if nome_grupo_atual in lista_grupos_locais:
                            idx_grp = lista_grupos_locais.index(nome_grupo_atual)
                        else:
                            st.error("⚠️ Este bem foi transferido de outra unidade e precisa ser vinculado a um Grupo Contábil local.")
                            
                        m_grupo_nome = st.selectbox("Vincular ao Grupo Local", lista_grupos_locais, index=idx_grp)
                        m_grupo_id = int(df_grupos_locais[df_grupos_locais['nome_grupo'] == m_grupo_nome].iloc[0]['id'])
                    
                    m_desc = st.text_input("Descrição", value=limpar_texto(bem_row['descricao_item']))
                    c_m1, c_m2 = st.columns(2)
                    m_marca = c_m1.text_input("Marca/Modelo", value=limpar_texto(bem_row.get('marca_modelo')))
                    m_serie = c_m2.text_input("Nº Série", value=limpar_texto(bem_row.get('num_serie_placa')))
                    c_m3, c_m4 = st.columns(2)
                    m_plaq = c_m3.text_input("Plaqueta", value=limpar_texto(bem_row.get('plaqueta')))
                    m_loc = c_m4.text_input("Localização", value=limpar_texto(bem_row.get('localizacao')))
                    c_m5, c_m6 = st.columns(2)
                    m_nf = c_m5.text_input("Nota Fiscal", value=limpar_texto(bem_row.get('numero_nota_fiscal')))
                    m_forn = c_m6.text_input("Fornecedor", value=limpar_texto(bem_row.get('nome_fornecedor')))
                    
                    c_m7, c_m8 = st.columns(2)
                    m_vaq = c_m7.number_input("Valor Aquisição Base (R$)", value=float(bem_row['valor_compra']), min_value=0.0, step=100.0)
                    m_dtc = c_m8.date_input("Data Compra", value=bem_row['data_compra'])
                    
                    lista_regras = ["NENHUM (Sem Crédito)", "MENSAL (Pela Depreciação)", "INTEGRAL (Mês de Aquisição)"]
                    m_regra = st.selectbox("Regra de Crédito PIS/COFINS", lista_regras, index=lista_regras.index(bem_row['regra_credito']) if bem_row['regra_credito'] in lista_regras else 0)
                    
                    st.markdown("##### Saldo de Implantação e Histórico")
                    tem_saldo = st.checkbox("Este bem possui saldo de implantação / histórico?", value=pd.notnull(bem_row.get('data_saldo_inicial')))
                    
                    c_m9, c_m10 = st.columns(2)
                    if tem_saldo:
                        m_dtsi = c_m9.date_input("Data Saldo Inicial", value=bem_row['data_saldo_inicial'] if pd.notnull(bem_row.get('data_saldo_inicial')) else hoje_br.date())
                        m_vri = c_m10.number_input("Valor Residual Inicial (R$)", value=float(bem_row.get('valor_residual_inicial', 0.0)), min_value=0.0, step=100.0)
                    else:
                        m_dtsi = None
                        m_vri = 0.0
                        st.info("Campos de saldo ocultos. O sistema utilizará a Data e Valor de Compra para calcular a depreciação (Cenário Folha em Branco).")
                    
                    st.markdown("##### Transferência / Status")
                    c_m11, c_m12 = st.columns(2)
                    
                    todas_empresas = df_emp.apply(formatar_nome_empresa, axis=1).tolist()
                    empresa_atual_str = df_emp[df_emp['id'] == emp_id].apply(formatar_nome_empresa, axis=1).iloc[0]
                    idx_emp = todas_empresas.index(empresa_atual_str) if empresa_atual_str in todas_empresas else 0
                    
                    nova_empresa = c_m11.selectbox("Transferir para Unidade", todas_empresas, index=idx_emp)
                    novo_emp_id = int(df_emp.loc[df_emp.apply(formatar_nome_empresa, axis=1) == nova_empresa].iloc[0]['id'])
                    
                    lista_status = ["ativo", "inativo", "baixado"]
                    m_status = c_m12.selectbox("Status", lista_status, index=lista_status.index(bem_row['status']) if bem_row['status'] in lista_status else 0)
                    
                    if st.form_submit_button("Atualizar Bem", type="primary"):
                        conn_m = get_db_connection(); cursor_m = conn_m.cursor()
                        try:
                            val_dtsi = m_dtsi if m_dtsi is not None else None
                            cursor_m.execute("""UPDATE bens_imobilizado SET grupo_id=%s, descricao_item=%s, marca_modelo=%s, num_serie_placa=%s, plaqueta=%s, localizacao=%s, numero_nota_fiscal=%s, nome_fornecedor=%s, valor_compra=%s, data_compra=%s, regra_credito=%s, data_saldo_inicial=%s, valor_residual_inicial=%s, tenant_id=%s, status=%s WHERE id=%s""", (m_grupo_id, m_desc, m_marca, m_serie, m_plaq, m_loc, m_nf, m_forn, float(m_vaq), m_dtc, m_regra, val_dtsi, float(m_vri), novo_emp_id, m_status, bem_id))
                            if m_status != 'ativo': cursor_m.execute("UPDATE bens_imobilizado SET data_baixa = CURDATE() WHERE id=%s AND data_baixa IS NULL", (bem_id,))
                            conn_m.commit(); st.success("Bem atualizado com sucesso!"); st.rerun()
                        except Exception as e:
                            conn_m.rollback(); st.error(f"Erro ao atualizar: {e}")
                        finally:
                            conn_m.close()

# --- 8. MÓDULO PARÂMETROS CONTÁBEIS ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": st.error("Acesso restrito."); return
    st.markdown("### Parâmetros Contábeis e Exportação ERP")
    df_op = carregar_operacoes()
    op_nomes = df_op['nome'].tolist()
    
    tab_edit, tab_novo, tab_fecho, tab_limpeza, tab_imob = st.tabs(["Editar Existente", "Nova Operação", "Fecho por Empresa", "Auditoria/Limpeza", "Grupos Imobilizado"])
    
    with tab_edit:
        sel_op = st.selectbox("Selecione a Operação:", op_nomes)
        row_op = df_op[df_op['nome'] == sel_op].iloc[0]
        oid = row_op['id']
        
        with st.form("form_edit_param"):
            st.markdown("##### Configuração PIS")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
            p_deb = c1.text_input("Débito PIS", value=limpar_texto(row_op.get('conta_deb_pis')), key=f"pd_{oid}")
            p_cred = c2.text_input("Crédito PIS", value=limpar_texto(row_op.get('conta_cred_pis')), key=f"pc_{oid}")
            p_cod = c3.text_input("Cód ERP PIS", value=limpar_texto(row_op.get('pis_h_codigo')), key=f"pcd_{oid}")
            p_txt = c4.text_input("Texto Padrão PIS", value=limpar_texto(row_op.get('pis_h_texto')), key=f"ptx_{oid}")
            
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2])
            c_deb = c5.text_input("Débito COFINS", value=limpar_texto(row_op.get('conta_deb_cof')), key=f"cd_{oid}")
            c_cred = c6.text_input("Crédito COFINS", value=limpar_texto(row_op.get('conta_cred_cof')), key=f"cc_{oid}")
            c_cod = c7.text_input("Cód ERP COFINS", value=limpar_texto(row_op.get('cofins_h_codigo')), key=f"ccd_{oid}")
            c_txt = c8.text_input("Texto Padrão COF", value=limpar_texto(row_op.get('cofins_h_texto')), key=f"ctx_{oid}")
            
            st.markdown("##### Configuração CUSTO/VALOR LÍQUIDO")
            c9, c10, c11, c12 = st.columns([1, 1, 1, 2])
            cu_deb = c9.text_input("Débito Custo", value=limpar_texto(row_op.get('conta_deb_custo')), key=f"cud_{oid}")
            cu_cred = c10.text_input("Crédito Custo", value=limpar_texto(row_op.get('conta_cred_custo')), key=f"cuc_{oid}")
            cu_cod = c11.text_input("Cód ERP Custo", value=limpar_texto(row_op.get('custo_h_codigo')), key=f"cucd_{oid}")
            cu_txt = c12.text_input("Texto Padrão Custo", value=limpar_texto(row_op.get('custo_h_texto')), key=f"cutx_{oid}")

            if row_op['tipo'] == 'RECEITA':
                with st.expander("Configuração de Retenção na Fonte", expanded=False):
                    cr1, cr2, cr3, cr4 = st.columns([1, 1, 1, 2])
                    r_p_deb = cr1.text_input("Débito PIS Ret", value=limpar_texto(row_op.get('ret_pis_conta_deb')), key=f"rpd_{oid}")
                    r_p_cred = cr2.text_input("Crédito PIS Ret", value=limpar_texto(row_op.get('ret_pis_conta_cred')), key=f"rpc_{oid}")
                    r_p_cod = cr3.text_input("Cód ERP PIS Ret", value=limpar_texto(row_op.get('ret_pis_h_codigo')), key=f"rpcd_{oid}")
                    r_p_txt = cr4.text_input("Histórico PIS Ret", value=limpar_texto(row_op.get('ret_pis_h_texto')), key=f"rptx_{oid}")
                    cr5, cr6, cr7, cr8 = st.columns([1, 1, 1, 2])
                    r_c_deb = cr5.text_input("Débito COF Ret", value=limpar_texto(row_op.get('ret_cofins_conta_deb')), key=f"rcd_{oid}")
                    r_c_cred = cr6.text_input("Crédito COF Ret", value=limpar_texto(row_op.get('ret_cofins_conta_cred')), key=f"rcc_{oid}")
                    r_c_cod = cr7.text_input("Cód ERP COF Ret", value=limpar_texto(row_op.get('ret_cofins_h_codigo')), key=f"rccd_{oid}")
                    r_c_txt = cr8.text_input("Histórico COF Ret", value=limpar_texto(row_op.get('ret_cofins_h_texto')), key=f"rctx_{oid}")
            else: r_p_deb=r_p_cred=r_p_cod=r_p_txt=r_c_deb=r_c_cred=r_c_cod=r_c_txt=None
            
            if st.form_submit_button("Atualizar Operação"):
                conn = get_db_connection(); cursor = conn.cursor()
                try:
                    cursor.execute("""UPDATE operacoes SET conta_deb_pis=%s, conta_cred_pis=%s, pis_h_codigo=%s, pis_h_texto=%s, conta_deb_cof=%s, conta_cred_cof=%s, cofins_h_codigo=%s, cofins_h_texto=%s, conta_deb_custo=%s, conta_cred_custo=%s, custo_h_codigo=%s, custo_h_texto=%s, ret_pis_conta_deb=%s, ret_pis_conta_cred=%s, ret_pis_h_codigo=%s, ret_pis_h_texto=%s, ret_cofins_conta_deb=%s, ret_cofins_conta_cred=%s, ret_cofins_h_codigo=%s, ret_cofins_h_texto=%s WHERE id=%s""", (p_deb, p_cred, p_cod, p_txt, c_deb, c_cred, c_cod, c_txt, cu_deb, cu_cred, cu_cod, cu_txt, r_p_deb, r_p_cred, r_p_cod, r_p_txt, r_c_deb, r_c_cred, r_c_cod, r_c_txt, int(oid)))
                    conn.commit(); carregar_operacoes.clear(); st.success("Atualizado!"); st.rerun()
                except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
                finally: conn.close()

    with tab_novo:
        with st.form("form_nova_op", clear_on_submit=True):
            c_nome, c_tipo = st.columns([3, 1])
            novo_nome = c_nome.text_input("Nome da Nova Operação")
            novo_tipo = c_tipo.selectbox("Natureza", ["RECEITA", "DESPESA"])
            st.markdown("##### Configuração PIS")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2]); n_p_deb = c1.text_input("Débito PIS", key="n_pd"); n_p_cred = c2.text_input("Crédito PIS", key="n_pc"); n_p_cod = c3.text_input("Cód ERP PIS", key="n_pcd"); n_p_txt = c4.text_input("Texto Padrão PIS", key="n_ptx")
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2]); n_c_deb = c5.text_input("Débito COFINS", key="n_cd"); n_c_cred = c6.text_input("Crédito COFINS", key="n_cc"); n_c_cod = c7.text_input("Cód ERP COFINS", key="n_ccd"); n_c_txt = c8.text_input("Texto Padrão COF", key="n_ctx")
            st.markdown("##### Configuração CUSTO/VALOR LÍQUIDO")
            c9, c10, c11, c12 = st.columns([1, 1, 1, 2]); n_cu_deb = c9.text_input("Débito Custo", key="n_cud"); n_cu_cred = c10.text_input("Crédito Custo", key="n_cuc"); n_cu_cod = c11.text_input("Cód ERP Custo", key="n_cucd"); n_cu_txt = c12.text_input("Texto Padrão Custo", key="n_cutx")
            st.divider()
            
            if st.form_submit_button("Registar Nova Operação"):
                if not novo_nome: st.error("O nome é obrigatório.")
                else:
                    nome_limpo = novo_nome.strip().lower()
                    if any(o.strip().lower() == nome_limpo for o in op_nomes): st.error(f"Erro: Já existe uma operação chamada '{novo_nome}'. Verifique na aba 'Editar Existente'.")
                    else:
                        conn = get_db_connection(); cursor = conn.cursor()
                        try:
                            query_insert = """INSERT INTO operacoes (nome, tipo, conta_deb_pis, conta_cred_pis, pis_h_codigo, pis_h_texto, conta_deb_cof, conta_cred_cof, cofins_h_codigo, cofins_h_texto, conta_deb_custo, conta_cred_custo, custo_h_codigo, custo_h_texto, ret_pis_conta_deb, ret_pis_conta_cred, ret_pis_h_codigo, ret_pis_h_texto, ret_cofins_conta_deb, ret_cofins_conta_cred, ret_cofins_h_codigo, ret_cofins_h_texto) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"""
                            valores = (novo_nome, novo_tipo, n_p_deb, n_p_cred, n_p_cod, n_p_txt, n_c_deb, n_c_cred, n_c_cod, n_c_txt, n_cu_deb, n_cu_cred, n_cu_cod, n_cu_txt)
                            cursor.execute(query_insert, valores)
                            conn.commit(); carregar_operacoes.clear(); st.success("Nova operação registada com sucesso!"); st.rerun()
                        except Exception as e: conn.rollback(); st.error(f"Erro ao salvar: {e}")
                        finally: conn.close()

    with tab_limpeza:
        st.markdown("#### Verificação de Integridade de Operações")
        st.info("Utilize esta ferramenta para identificar operações duplicadas ou sem utilização.")
        if st.button("Executar Auditoria de Operações"):
            conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT o.id, o.nome, o.tipo, (SELECT COUNT(*) FROM lancamentos l WHERE l.operacao_id = o.id) as total_usado FROM operacoes o ORDER BY o.nome")
            ops = cursor.fetchall(); conn.close()
            st.write("---")
            vistos = {}; duplicados = []
            for o in ops:
                n = o['nome'].strip().lower()
                if n in vistos: duplicados.append((o, vistos[n]))
                else: vistos[n] = o
            if not duplicados: st.success("Nenhuma duplicidade de nome encontrada.")
            else:
                for d, original in duplicados:
                    c1, c2 = st.columns([4, 1])
                    c1.warning(f"DUPLICADO: '{d['nome']}' (ID: {d['id']}) - Usado {d['total_usado']} vezes.")
                    if d['total_usado'] == 0:
                        if c2.button("Excluir", key=f"excl_{d['id']}"):
                            conn = get_db_connection(); cursor = conn.cursor()
                            cursor.execute(f"DELETE FROM operacoes WHERE id={int(d['id'])}")
                            conn.commit(); conn.close(); carregar_operacoes.clear(); st.rerun()

    with tab_fecho:
        st.markdown("##### Contas de Transferência / Fecho (Apuração Mensal)")
        df_emp_f = carregar_empresas_ativas()
        if not df_emp_f.empty:
            with st.form("form_fecho"):
                emp_sel_f = st.selectbox("Selecione a Empresa", df_emp_f.apply(formatar_nome_empresa, axis=1))
                emp_id_f = int(df_emp_f.loc[df_emp_f.apply(formatar_nome_empresa, axis=1) == emp_sel_f].iloc[0]['id'])
                row_emp_f = df_emp_f[df_emp_f['id'] == emp_id_f].iloc[0]
                c1, c2 = st.columns(2)
                t_pis = c1.text_input("Conta Transferência PIS", value=limpar_texto(row_emp_f.get('conta_transf_pis')))
                t_cofins = c2.text_input("Conta Transferência COFINS", value=limpar_texto(row_emp_f.get('conta_transf_cofins')))
                if st.form_submit_button("Salvar Contas de Fecho"):
                    conn = get_db_connection(); cursor = conn.cursor()
                    cursor.execute("UPDATE empresas SET conta_transf_pis=%s, conta_transf_cofins=%s WHERE id=%s", (t_pis, t_cofins, int(emp_id_f)))
                    conn.commit(); carregar_empresas_ativas.clear(); st.success("Atualizado!"); st.rerun()

    with tab_imob:
        df_e = carregar_empresas_ativas()
        e_sel = st.selectbox("Selecione a Empresa para Gerir Grupos", df_e.apply(formatar_nome_empresa, axis=1), key="sel_emp_grp")
        e_id = int(df_e.loc[df_e.apply(formatar_nome_empresa, axis=1) == e_sel].iloc[0]['id'])
        
        conn = get_db_connection()
        df_g = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {e_id}", conn)
        conn.close()
        
        col_edit, col_new = st.columns(2, gap="large")
        
        with col_edit:
            st.markdown("##### Editar Grupo Existente")
            if not df_g.empty:
                g_sel = st.selectbox("Selecione o Grupo", df_g['nome_grupo'].tolist())
                g_row = df_g[df_g['nome_grupo'] == g_sel].iloc[0]
                
                with st.form("ed_grp"):
                    n_g = st.text_input("Nome", value=limpar_texto(g_row['nome_grupo']))
                    tx = st.number_input("Taxa Anual (%)", value=float(g_row['taxa_anual_percentual']))
                    cd = st.text_input("Conta Despesa (ERP)", value=limpar_texto(g_row['conta_contabil_despesa']))
                    cc = st.text_input("Conta Dep. Acumulada (ERP)", value=limpar_texto(g_row['conta_contabil_dep_acumulada']))
                    
                    if st.form_submit_button("Atualizar Grupo"):
                        conn = get_db_connection(); cursor = conn.cursor()
                        cursor.execute("UPDATE grupos_imobilizado SET nome_grupo=%s, taxa_anual_percentual=%s, conta_contabil_despesa=%s, conta_contabil_dep_acumulada=%s WHERE id=%s", (n_g, float(tx), cd, cc, int(g_row['id'])))
                        conn.commit(); conn.close(); st.success("Atualizado!"); st.rerun()
            else: st.info("Nenhum grupo cadastrado.")
                
        with col_new:
            st.markdown("##### Criar Novo Grupo")
            opcoes_rf = {"Livre / Customizado": 0.0, "Computadores e Periféricos (20%)": 20.0, "Veículos de Passageiros (20%)": 20.0, "Máquinas e Equipamentos (10%)": 10.0, "Móveis e Utensílios (10%)": 10.0, "Edificações / Imóveis (4%)": 4.0}
            padrao_sel = st.selectbox("Template RFB", list(opcoes_rf.keys()))
            nome_sugerido = padrao_sel.split(' (')[0] if padrao_sel != "Livre / Customizado" else ""
            
            with st.form("nv_grp"):
                n_g_n = st.text_input("Nome do Grupo", value=nome_sugerido)
                tx_n = st.number_input("Taxa Anual (%)", min_value=0.0, value=opcoes_rf[padrao_sel])
                cd_n = st.text_input("Conta Despesa (D) - ERP")
                cc_n = st.text_input("Conta Dep. Acumulada (C) - ERP")
                if st.form_submit_button("Adicionar Grupo"):
                    if n_g_n:
                        conn = get_db_connection(); cursor = conn.cursor()
                        cursor.execute("INSERT INTO grupos_imobilizado (tenant_id, nome_grupo, taxa_anual_percentual, conta_contabil_despesa, conta_contabil_dep_acumulada) VALUES (%s,%s,%s,%s,%s)", (int(e_id), n_g_n, float(tx_n), cd_n, cc_n))
                        conn.commit(); conn.close(); st.success("Criado!"); st.rerun()

# --- 9. GESTÃO DE UTILIZADORES ---
def modulo_usuarios():
    if st.session_state.nivel_acesso != "SUPER_ADMIN": st.error("Acesso restrito."); return
    
    st.markdown("### Gestão de Utilizadores")
    conn = get_db_connection()
    df_users = pd.read_sql("SELECT id, nome, username, nivel_acesso, status_usuario, data_criacao FROM usuarios ORDER BY nome ASC", conn)
    
    df_empresas = pd.read_sql("SELECT id, nome FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
    
    tab_lista, tab_novo = st.tabs(["Utilizadores Registados", "Adicionar Utilizador"])
    
    with tab_lista:
        st.dataframe(df_users, use_container_width=True, hide_index=True)
        st.markdown("##### Gerir Acesso")
        with st.form("form_gestao_usuario"):
            c1, c2 = st.columns([2, 1])
            usr_sel = c1.selectbox("Selecione o Utilizador", df_users['username'].tolist())
            nova_acao = c2.selectbox("Ação", ["Inativar Acesso", "Reativar Acesso", "Redefinir Palavra-passe"])
            nova_senha = st.text_input("Nova Palavra-passe (se aplicável)", type="password")
            
            if st.form_submit_button("Executar Ação"):
                cursor = conn.cursor()
                try:
                    if nova_acao == "Inativar Acesso":
                        cursor.execute("UPDATE usuarios SET status_usuario = 'INATIVO' WHERE username = %s", (usr_sel,))
                        st.toast(f"Acesso inativado para {usr_sel}.")
                    elif nova_acao == "Reativar Acesso":
                        cursor.execute("UPDATE usuarios SET status_usuario = 'ATIVO' WHERE username = %s", (usr_sel,))
                        st.toast(f"Acesso reativado para {usr_sel}.")
                    elif nova_acao == "Redefinir Palavra-passe":
                        if len(nova_senha) < 6: st.error("A senha deve ter pelo menos 6 caracteres.")
                        else:
                            cursor.execute("UPDATE usuarios SET senha_hash = %s WHERE username = %s", (gerar_hash_senha(nova_senha), usr_sel))
                            st.toast("Palavra-passe atualizada com sucesso!")
                    conn.commit()
                except Exception as e: conn.rollback(); st.error(f"Erro no banco: {e}")
                finally:
                    conn.close() 
                    import time; time.sleep(1.2); st.rerun()
                    
    with tab_novo:
        with st.form("form_novo_usuario"):
            col_nome, col_user = st.columns(2)
            novo_nome = col_nome.text_input("Nome Completo")
            novo_user = col_user.text_input("Nome de Utilizador (Login)")
            
            col_pass, col_nivel = st.columns(2)
            nova_pass = col_pass.text_input("Palavra-passe Inicial", type="password")
            nivel = col_nivel.selectbox("Nível de Acesso", ["CLIENT_OPERATOR", "ADMIN", "SUPER_ADMIN"])
            
            lista_empresas = ["Nenhuma (Acesso Global)"] + df_empresas['nome'].tolist()
            emp_vinculada = st.selectbox("Vincular a uma Unidade/Empresa", lista_empresas)
            
            if st.form_submit_button("Criar Utilizador"):
                if not novo_nome or not novo_user or len(nova_pass) < 6: st.error("Preencha todos os campos corretamente (senha mín. 6 caracteres).")
                elif novo_user in df_users['username'].tolist(): st.error("Este utilizador já existe.")
                else:
                    cursor = conn.cursor()
                    try:
                        empresa_id_db = None
                        if emp_vinculada != "Nenhuma (Acesso Global)": empresa_id_db = int(df_empresas[df_empresas['nome'] == emp_vinculada].iloc[0]['id'])
                        query = """INSERT INTO usuarios (nome, username, senha_hash, nivel_acesso, status_usuario, data_criacao, empresa_id) VALUES (%s, %s, %s, %s, 'ATIVO', NOW(), %s)"""
                        cursor.execute(query, (novo_nome, novo_user, gerar_hash_senha(nova_pass), nivel, empresa_id_db))
                        conn.commit(); st.toast("Utilizador criado com sucesso!", icon="✅")
                    except Exception as e: conn.rollback(); st.error(f"Erro ao inserir no banco: {e}")
                    finally:
                        conn.close() 
                        import time; time.sleep(1.2); st.rerun()
                        
    if conn.is_connected(): conn.close()

# --- 10. MENU LATERAL ---
with st.sidebar:
    dias_pt = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    st.markdown(f"""
        <div style='text-align: center; color: #64748b; font-size: 0.9em; margin-bottom: 10px; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px;'>
            {dias_pt[hoje_br.weekday()]}<br>
            <b style='color: #004b87;'>{hoje_br.strftime('%d/%m/%Y')}</b>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("<h2 style='color: #004b87; text-align: center;'>CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'><b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação", "Parâmetros Contábeis", "Gestão de Utilizadores"])
    st.write("---")
    if st.button("Encerrar Sessão", use_container_width=True): st.session_state.autenticado = False; st.rerun()

# --- 11. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "Imobilizado & Depreciação": modulo_imobilizado()
elif menu == "Parâmetros Contábeis": modulo_parametros()
elif menu == "Gestão de Utilizadores": modulo_usuarios()
