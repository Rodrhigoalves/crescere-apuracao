import streamlit as st

import mysql.connector

import pandas as pd

import requests

from datetime import date, datetime, timedelta, timezone

import io

import bcrypt

from fpdf import FPDF

import calendar



# --- 1. CONFIGURAÇÕES VISUAIS E INJEÇÃO CSS ---

st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")



st.markdown("""

<style>

    .stApp { background-color: #f4f6f9; }

    .stButton>button, .stDownloadButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; width: 100%; transition: all 0.2s; }

    .stButton>button:hover, .stDownloadButton>button:hover { background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }

    

    /* Botão de Excluir Vermelho */

    .btn-excluir button { background-color: #dc2626 !important; color: white !important; }

    .btn-excluir button:hover { background-color: #b91c1c !important; }

    

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



# --- 4. CONTROLE DE ESTADO E AUTENTICAÇÃO ---

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



    conn = get_db_connection()

    df_g = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id}", conn)

    conn.close()



    # --- FUNÇÃO DE FRAGMENTO (MANUTENÇÃO) ---

    if len(tabs) > 2:

        @st.fragment

        def fragmento_manutencao(emp_id_param):

            st.markdown("#### Manutenção de Ativos (Edição/Transferência/Exclusão)")

            conn_f = get_db_connection()

            df_todos_manut = pd.read_sql(f"SELECT b.*, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id_param}", conn_f)

            df_grupos_locais = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id_param}", conn_f)

            df_plano_existe = pd.read_sql(f"SELECT DISTINCT bem_id FROM plano_depreciacao_itens", conn_f)

            conn_f.close()

            

            bens_com_plano = df_plano_existe['bem_id'].tolist() if not df_plano_existe.empty else []

            

            if df_todos_manut.empty:

                st.info("Nenhum bem cadastrado ou transferido para esta unidade.")

            else:

                lista_formatada_itens = []

                for _, r in df_todos_manut.iterrows():

                    desc = limpar_texto(r['descricao_item'])

                    marca = limpar_texto(r.get('marca_modelo', ''))

                    grp = limpar_texto(r.get('nome_grupo'))

                    aviso = "" if grp else " ⚠️ (GRUPO INVÁLIDO)"

                    

                    is_reclass = r['id'] in bens_com_plano or pd.notnull(r.get('data_saldo_inicial'))

                    prefix = "✓ " if is_reclass else ""

                    

                    # --- FILTRO INTELIGENTE / BUSCA MULTIFUNCIONAL ---

                    nf_str = f" | NF: {r['numero_nota_fiscal']}" if pd.notnull(r.get('numero_nota_fiscal')) and str(r.get('numero_nota_fiscal')).strip() else ""

                    plaq_str = f" | Plq: {r['plaqueta']}" if pd.notnull(r.get('plaqueta')) and str(r.get('plaqueta')).strip() else ""

                    val_str = f" | {formatar_moeda(r['valor_compra'])}" if pd.notnull(r.get('valor_compra')) else ""

                    

                    nome_display = f"{prefix}[{r['id']}] {desc} {marca}{nf_str}{plaq_str}{val_str} ({r['status'].upper()}){aviso}"

                    lista_formatada_itens.append({'id': r['id'], 'display': nome_display, 'is_reclass': 1 if is_reclass else 0})

                

                lista_formatada_itens.sort(key=lambda x: (x['is_reclass'], x['display']))

                opcoes_selectbox = [x['display'] for x in lista_formatada_itens]



                bem_sel = st.selectbox("Busque o Bem (Digite o Nome, Nota Fiscal, Plaqueta ou Valor)", opcoes_selectbox, key="select_manutencao_bem")

                bem_id = int(bem_sel.split("]")[0].replace("[", "").replace("✓ ", ""))

                bem_row = df_todos_manut[df_todos_manut['id'] == bem_id].iloc[0]

                

                with st.container(border=True):

                    col_fisico, col_estrategia = st.columns([1, 1], gap="large")

                    

                    with col_fisico:

                        st.markdown("##### Dados Físicos e Base")

                        if df_grupos_locais.empty:

                            st.warning("⚠️ Crie um Grupo em Parâmetros Contábeis primeiro.")

                            m_grupo_id = bem_row['grupo_id']

                        else:

                            lista_grupos_locais = df_grupos_locais['nome_grupo'].tolist()

                            nome_grupo_atual = limpar_texto(bem_row.get('nome_grupo'))

                            idx_grp = lista_grupos_locais.index(nome_grupo_atual) if nome_grupo_atual in lista_grupos_locais else 0

                            if nome_grupo_atual not in lista_grupos_locais:

                                st.error("⚠️ Este bem foi transferido e está órfão. Selecione um Grupo Local:")

                            m_grupo_nome = st.selectbox("Vincular ao Grupo Local", lista_grupos_locais, index=idx_grp, key=f"grp_m_{bem_id}")

                            m_grupo_id = int(df_grupos_locais[df_grupos_locais['nome_grupo'] == m_grupo_nome].iloc[0]['id'])

                        

                        m_desc = st.text_input("Descrição", value=limpar_texto(bem_row['descricao_item']), key=f"desc_m_{bem_id}")

                        c_f1, c_f2 = st.columns(2)

                        m_marca = c_f1.text_input("Marca/Modelo", value=limpar_texto(bem_row.get('marca_modelo')), key=f"marca_m_{bem_id}")

                        m_serie = c_f2.text_input("Nº Série", value=limpar_texto(bem_row.get('num_serie_placa')), key=f"serie_m_{bem_id}")

                        c_f3, c_f4 = st.columns(2)

                        m_plaq = c_f3.text_input("Plaqueta", value=limpar_texto(bem_row.get('plaqueta')), key=f"plaq_m_{bem_id}")

                        m_loc = c_f4.text_input("Localização", value=limpar_texto(bem_row.get('localizacao')), key=f"loc_m_{bem_id}")

                        c_f5, c_f6 = st.columns(2)

                        m_nf = c_f5.text_input("Nota Fiscal", value=limpar_texto(bem_row.get('numero_nota_fiscal')), key=f"nf_m_{bem_id}")

                        m_forn = c_f6.text_input("Fornecedor", value=limpar_texto(bem_row.get('nome_fornecedor')), key=f"forn_m_{bem_id}")

                        c_f7, c_f8 = st.columns(2)

                        m_vaq = c_f7.number_input("Valor Aquisição Base (R$)", value=float(bem_row['valor_compra']), min_value=0.0, step=100.0, key=f"vaq_m_{bem_id}")

                        m_dtc = c_f8.date_input("Data Compra", value=bem_row['data_compra'], key=f"dtc_m_{bem_id}")



                    with col_estrategia:

                        st.markdown("##### Estratégia Contábil")

                        c_e1, c_e2 = st.columns(2)

                        lista_regras = ["NENHUM (Sem Crédito)", "MENSAL (Pela Depreciação)", "INTEGRAL (Mês de Aquisição)"]

                        

                        m_regra = c_e1.selectbox("Regra de Crédito PIS/COFINS", lista_regras, index=lista_regras.index(bem_row['regra_credito']) if bem_row['regra_credito'] in lista_regras else 0, key=f"regra_m_{bem_id}")

                        

                        m_taxa_cust = c_e2.number_input("Taxa Custom (%) ", value=float(bem_row.get('taxa_customizada', 0.0) or 0.0), min_value=0.0, step=1.0, key=f"taxa_m_{bem_id}")

                        

                        idx_cenario_atual = 0

                        if pd.notnull(bem_row.get('data_saldo_inicial')):

                            idx_cenario_atual = 2 if bem_id in bens_com_plano else 1

                        

                        cenario_manut = st.selectbox("Cenário de Depreciação", [

                            "1. Bem Novo (Cálculo Automático)", 

                            "2. Cliente Novo (Sem Histórico Mensal)", 

                            "3. Continuidade (Memória Cota Fixa)"

                        ], index=idx_cenario_atual, key=f"cenario_m_{bem_id}")



                        confirmacao_manut = True

                        

                        if "1" not in cenario_manut:

                            c_e3, c_e4 = st.columns(2)

                            data_padrao_saldo = date(hoje_br.year - 1, 12, 31)

                            valor_dtsi_atual = bem_row['data_saldo_inicial'] if pd.notnull(bem_row.get('data_saldo_inicial')) else data_padrao_saldo

                            m_dtsi = c_e3.date_input("Data Saldo Inicial", value=valor_dtsi_atual, key=f"dtsi_m_{bem_id}")

                            

                            v_res_inicial_db = float(bem_row.get('valor_residual_inicial', 0.0))

                            dep_ac_calc = float(m_vaq) - v_res_inicial_db if pd.notnull(bem_row.get('data_saldo_inicial')) else 0.0

                            

                            m_dep_ac = c_e4.number_input("Deprec. Acumulada Anterior (R$)", value=float(max(0, dep_ac_calc)), min_value=0.0, step=100.0, key=f"depac_m_{bem_id}")

                            m_vri_calculado = max(0.0, float(m_vaq) - float(m_dep_ac))

                            

                            st.markdown(f"<small>Valor Residual Atual: <b>{formatar_moeda(m_vri_calculado)}</b></small>", unsafe_allow_html=True)

                            

                            if m_vri_calculado <= 0:

                                st.info("ℹ️ Este item atingiu a depreciação máxima (Valor Zero) e será salvo apenas para controle de Inventário Físico.")

                                cota_sugerida_m = 0.0

                            elif "3" in cenario_manut:

                                taxa_usada_m = float(m_taxa_cust) if m_taxa_cust > 0 else float(df_grupos_locais[df_grupos_locais['id']==m_grupo_id]['taxa_anual_percentual'].iloc[0]) if not df_grupos_locais.empty else 10.0

                                cota_sugerida_m = round((float(m_vaq) * (taxa_usada_m / 100.0)) / 12.0, 2)

                                st.info(f"Cota Mensal Padrão projetada: **{formatar_moeda(cota_sugerida_m)}**")

                            else: cota_sugerida_m = 0.0

                        else:

                            m_dtsi = None; m_vri_calculado = 0.0; cota_sugerida_m = 0.0

                            st.info("Campos de saldo ocultos. Utilizará Data/Valor de Compra para calcular.")

                    

                    if "3" in cenario_manut and cota_sugerida_m > 0 and m_vri_calculado > 0:

                        st.markdown("##### Grade de Conferência")

                        primeira_cota_calc_m = cota_sugerida_m

                        mes_inicio_plan_m = m_dtsi.month + 1 if m_dtsi.month < 12 else 1

                        ano_inicio_plan_m = m_dtsi.year if m_dtsi.month < 12 else m_dtsi.year + 1



                        primeira_cota_manual_m = st.number_input("Ajuste da 1ª Parcela (Opcional - R$)", min_value=0.0, max_value=float(m_vri_calculado), value=float(primeira_cota_calc_m), step=10.0, key=f"cota_manut_{bem_id}")

                        

                        with st.expander("👀 Ver Prévia Dinâmica do Plano de Voo (Resumido)", expanded=True):

                            preview_data_m = []

                            s_rest_m = m_vri_calculado

                            d_plan_m = date(ano_inicio_plan_m, mes_inicio_plan_m, 1)

                            

                            c_at_1_m = min(s_rest_m, float(primeira_cota_manual_m))

                            if c_at_1_m > 0:

                                preview_data_m.append({"Mês": d_plan_m.strftime('%m/%Y'), "Cota Projetada": formatar_moeda(c_at_1_m), "Saldo Restante": formatar_moeda(s_rest_m - c_at_1_m)})

                                s_rest_m -= c_at_1_m

                                m_plan_m = d_plan_m.month + 1 if d_plan_m.month < 12 else 1

                                a_plan_m = d_plan_m.year if d_plan_m.month < 12 else d_plan_m.year + 1

                                d_plan_m = date(a_plan_m, m_plan_m, 1)

                            

                            while s_rest_m > 0.009 and len(preview_data_m) < 6:

                                c_at_m = min(s_rest_m, float(cota_sugerida_m))

                                preview_data_m.append({"Mês": d_plan_m.strftime('%m/%Y'), "Cota Projetada": formatar_moeda(c_at_m), "Saldo Restante": formatar_moeda(s_rest_m - c_at_m)})

                                s_rest_m -= c_at_m

                                m_plan_m = d_plan_m.month + 1 if d_plan_m.month < 12 else 1

                                a_plan_m = d_plan_m.year if d_plan_m.month < 12 else d_plan_m.year + 1

                                d_plan_m = date(a_plan_m, m_plan_m, 1)

                            

                            if preview_data_m:

                                st.dataframe(pd.DataFrame(preview_data_m), hide_index=True, use_container_width=True)

                                if s_rest_m > 0.009: 

                                    st.markdown(f"<small style='color:gray;'>*... e assim sucessivamente até zerar.*</small>", unsafe_allow_html=True)



                        confirmacao_manut = st.checkbox("Confirmo que a memória de cálculo acima está correta.", key=f"conf_manut_{bem_id}")

                    else:

                        primeira_cota_manual_m = 0.0

                        if "3" in cenario_manut and m_vri_calculado > 0: confirmacao_manut = False

                    

                    st.markdown("---")

                    

                    with st.expander("⚙️ Gestão Administrativa e Exclusão (Área de Risco)", expanded=False):

                        st.warning("⚠️ **Aviso:** Alterar a unidade, o status, ou excluir um bem impacta diretamente os relatórios gerenciais e balancetes.")

                        c_a1, c_a2 = st.columns(2)

                        

                        todas_empresas = df_emp.apply(formatar_nome_empresa, axis=1).tolist()

                        empresa_atual_str = df_emp[df_emp['id'] == emp_id_param].apply(formatar_nome_empresa, axis=1).iloc[0]

                        idx_emp = todas_empresas.index(empresa_atual_str) if empresa_atual_str in todas_empresas else 0

                        

                        nova_empresa = c_a1.selectbox("Transferir para Unidade", todas_empresas, index=idx_emp, key=f"emp_m_{bem_id}")

                        novo_emp_id = int(df_emp.loc[df_emp.apply(formatar_nome_empresa, axis=1) == nova_empresa].iloc[0]['id'])

                        

                        lista_status = ["ativo", "inativo", "baixado"]

                        m_status = c_a2.selectbox("Status Físico", lista_status, index=lista_status.index(bem_row['status']) if bem_row['status'] in lista_status else 0, key=f"status_m_{bem_id}")

                        

                        st.markdown("---")

                        st.error("🔴 **ZONA CRÍTICA: Exclusão Definitiva**")

                        confirm_excluir = st.checkbox("Desejo excluir este ativo e todo o seu histórico do banco de dados permanentemente.", key=f"chk_del_m_{bem_id}")

                        texto_confirma = st.text_input("Para salvar alterações administrativas ou Excluir o bem, digite **CONFIRMO** em maiúsculo:", placeholder="Digite CONFIRMO", key=f"conf_admin_{bem_id}")



                    st.markdown("<br>", unsafe_allow_html=True)

                    

                    btn_disabled_m = ("3" in cenario_manut and m_vri_calculado > 0 and not confirmacao_manut)

                    if btn_disabled_m: st.warning("⚠️ Confirme a memória de cálculo para habilitar os botões de ação.")

                    

                    c_bt_update, c_bt_delete = st.columns([3, 1])

                    

                    with c_bt_update:

                        if st.button("Atualizar Bem", type="primary", use_container_width=True, disabled=btn_disabled_m):

                            mudou_admin = (novo_emp_id != emp_id_param) or (m_status != bem_row['status'])

                            

                            if mudou_admin and texto_confirma.strip().upper() != "CONFIRMO":

                                st.error("🔒 ERRO: Para transferir ou alterar status, você deve digitar CONFIRMO na aba de Gestão Administrativa.")

                            elif ("1" not in cenario_manut) and m_vri_calculado <= 0 and "3" in cenario_manut: 

                                st.error("O Valor Residual é zero. Não é possível usar 'Continuidade' para bens totalmente depreciados. Use o 'Cenário 2'.")

                            elif "3" in cenario_manut and cota_sugerida_m <= 0 and m_vri_calculado > 0: 

                                st.error("Erro na base de cálculo. O Valor de Aquisição e a Taxa devem ser maiores que zero.")

                            else:

                                conn_upd = get_db_connection(); cursor_upd = conn_upd.cursor()

                                try:

                                    val_dtsi = m_dtsi if ("1" not in cenario_manut) else None

                                    val_tx_cust = m_taxa_cust if m_taxa_cust > 0 else None

                                    

                                    cursor_upd.execute("""UPDATE bens_imobilizado SET grupo_id=%s, descricao_item=%s, marca_modelo=%s, num_serie_placa=%s, plaqueta=%s, localizacao=%s, numero_nota_fiscal=%s, nome_fornecedor=%s, valor_compra=%s, data_compra=%s, regra_credito=%s, data_saldo_inicial=%s, valor_residual_inicial=%s, taxa_customizada=%s, tenant_id=%s, status=%s WHERE id=%s""", (m_grupo_id, m_desc, m_marca, m_serie, m_plaq, m_loc, m_nf, m_forn, float(m_vaq), m_dtc, m_regra, val_dtsi, float(m_vri_calculado), val_tx_cust, novo_emp_id, m_status, bem_id))

                                    

                                    if m_status != 'ativo' and bem_row['status'] == 'ativo': 

                                        cursor_upd.execute("UPDATE bens_imobilizado SET data_baixa = CURDATE() WHERE id=%s AND data_baixa IS NULL", (bem_id,))

                                    

                                    cursor_upd.execute("DELETE FROM plano_depreciacao_itens WHERE bem_id = %s AND status_contabil = 'PENDENTE'", (bem_id,))

                                    

                                    if "3" in cenario_manut and cota_sugerida_m > 0 and float(m_vri_calculado) > 0:

                                        saldo_restante = float(m_vri_calculado)

                                        mes_plan = val_dtsi.month + 1 if val_dtsi.month < 12 else 1

                                        ano_plan = val_dtsi.year if val_dtsi.month < 12 else val_dtsi.year + 1

                                        data_plan = date(ano_plan, mes_plan, 1)



                                        is_first_m = True

                                        while saldo_restante > 0.009:

                                            cota_atual = min(saldo_restante, float(primeira_cota_manual_m) if is_first_m else float(cota_sugerida_m))

                                            cursor_upd.execute("INSERT INTO plano_depreciacao_itens (bem_id, mes_referencia, valor_cota, tipo_registro, status_contabil) VALUES (%s, %s, %s, 'PROJETADO', 'PENDENTE')", (bem_id, data_plan.strftime('%Y-%m-%d'), cota_atual))

                                            saldo_restante -= cota_atual

                                            is_first_m = False

                                            if data_plan.month == 12: data_plan = date(data_plan.year + 1, 1, 1)

                                            else: data_plan = date(data_plan.year, data_plan.month + 1, 1)



                                    conn_upd.commit(); st.success("Bem atualizado com sucesso!"); st.rerun()

                                except Exception as e:

                                    conn_upd.rollback(); st.error(f"Erro ao atualizar: {e}")

                                finally: conn_upd.close()



                    with c_bt_delete:

                        st.markdown('<div class="btn-excluir">', unsafe_allow_html=True)

                        if st.button("Excluir Ativo", use_container_width=True, disabled=not confirm_excluir):

                            if texto_confirma.strip().upper() == "CONFIRMO":

                                conn_del = get_db_connection(); cursor_del = conn_del.cursor()

                                try:

                                    cursor_del.execute("DELETE FROM plano_depreciacao_itens WHERE bem_id = %s", (bem_id,))

                                    cursor_del.execute("DELETE FROM bens_imobilizado WHERE id = %s", (bem_id,))

                                    conn_del.commit(); st.success("Ativo e plano de depreciação excluídos com sucesso!"); st.rerun()

                                except Exception as e:

                                    conn_del.rollback(); st.error(f"Erro ao excluir: {e}")

                                finally: conn_del.close()

                            else:

                                st.error("🔒 Digite CONFIRMO para validar a exclusão.")

                        st.markdown('</div>', unsafe_allow_html=True)



    with tabs[0]:

        col_in, col_ras = st.columns([1, 1], gap="large")

        with col_in:

            st.markdown("#### Cadastro do Bem")

            if df_g.empty: 

                st.warning("Cadastre os Grupos em Parâmetros Contábeis primeiro nesta empresa para realizar novos registros.")

            else:

                cenario = st.selectbox("Cenário de Implantação (Estratégia de Depreciação)", [

                    "1. Bem Novo (Folha em Branco - Cálculo Automático)", 

                    "2. Cliente Novo (Saldo de Partida - Sem Histórico Mensal)", 

                    "3. Continuidade (Memória de Cálculo - Cota Fixa Histórica)"

                ], key="cenario_cad")

                

                with st.container(border=True):

                    g_sel = st.selectbox("Grupo / Espécie", df_g['nome_grupo'].tolist())

                    g_row = df_g[df_g['nome_grupo'] == g_sel].iloc[0]

                    desc = st.text_input("Descrição Básica do Bem")

                    c_m, c_p = st.columns(2)

                    marca = c_m.text_input("Marca / Modelo (Opcional)")

                    num_serie = c_p.text_input("Nº Série / Placa (Opcional)")

                    c_pl, c_loc = st.columns(2)

                    plaqueta = c_pl.text_input("Plaqueta / Patrimônio (Opcional)")

                    localizacao = c_loc.text_input("Localização / Depto (Opcional)")

                    c_n, c_f = st.columns(2)

                    nf = c_n.text_input("Nº da Nota Fiscal (Opcional)")

                    forn = c_f.text_input("Fornecedor (Opcional)")

                    c_v, c_d = st.columns(2)

                    

                    v_aq = c_v.number_input("Valor de Aquisição Base (R$)", min_value=0.0, value=0.0, step=100.0)

                    dt_c = c_d.date_input("Data da Compra Original")

                    

                    st.markdown("##### Regras Específicas")

                    c_r1, c_r2 = st.columns(2)

                    regra_cred = c_r1.selectbox("Regra de Crédito PIS/COFINS", ["NENHUM (Sem Crédito)", "MENSAL (Pela Depreciação)", "INTEGRAL (Mês de Aquisição)"])

                    taxa_custom = c_r2.number_input("Taxa Customizada (% - Opcional)", min_value=0.0, value=0.0, step=1.0, help="Se preenchido, ignora a taxa do grupo.")



                    confirmacao_cad = True

                    

                    if "1" not in cenario:

                        st.markdown("---")

                        st.markdown("##### Saldo de Implantação / Histórico Contábil")

                        c_si, c_da = st.columns(2)

                        

                        data_padrao_saldo = date(hoje_br.year - 1, 12, 31)

                        dt_saldo = c_si.date_input("Data Base do Balancete (Última Posição)", value=data_padrao_saldo)

                        v_dep_acumulada = c_da.number_input("Depreciação Acumulada Anterior (R$)", min_value=0.0, max_value=float(v_aq) if float(v_aq)>0 else 10000000.0, value=0.0, step=100.0)

                        

                        v_residual_atual = max(0.0, float(v_aq) - float(v_dep_acumulada))

                        st.markdown(f"<small>Valor Residual Atual (Custo - Acumulada): <b>{formatar_moeda(v_residual_atual)}</b></small>", unsafe_allow_html=True)



                        if "3" in cenario:
