import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, datetime, timedelta, timezone # Adicionado timezone
import io
import bcrypt
from fpdf import FPDF
from dateutil.relativedelta import relativedelta # Adicionado para cálculos de depreciação

# --- 1. CONFIGURAÇÕES VISUAIS E INJEÇÃO CSS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; transition: all 0.2s; }
    .stButton>button:hover { background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    div[data-testid="stForm"], .css-1d391kg, .stExpander, div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] { 
        background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;
    }
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

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

# AJUSTE DE FUSO HORÁRIO (Brasília UTC-3)
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
                    st.session_state.dados_form.update({
                        "nome": res.get('nome', ''), "fantasia": res.get('fantasia', ''), "cnpj": res.get('cnpj', ''), 
                        "cnae": res.get('atividade_principal', [{}])[0].get('code', ''), 
                        "endereco": f"{res.get('logradouro', '')}, {res.get('numero', '')} - {res.get('bairro', '')}, {res.get('municipio', '')}/{res.get('uf', '')}"
                    })
                    st.rerun()
        st.divider()
        
        f = st.session_state.dados_form
        with st.form("form_empresa"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
            
            c3, c4, c5, c_apelido = st.columns([2, 1.5, 1.5, 2])
            cnpj = c3.text_input("CNPJ", value=f['cnpj'])
            
            # --- MUDANÇA 1: TODOS OS REGIMES INCLUÍDOS ---
            lista_regimes = ["Lucro Real", "Lucro Presumido", "Simples Nacional", "Simples Nacional - Excesso", "MEI", "Arbitrado", "Imune/Isenta", "Inativa"]
            idx_regime = lista_regimes.index(f.get('regime')) if f.get('regime') in lista_regimes else 0
            regime = c4.selectbox("Regime", lista_regimes, index=idx_regime)
            
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f.get('tipo') == "Matriz" else 1)
            apelido = c_apelido.text_input("Apelido da Unidade", value=f.get('apelido_unidade', ''))
            
            c6, c7 = st.columns([1, 3])
            cnae = c6.text_input("CNAE", value=f['cnae'])
            endereco = c7.text_input("Endereço", value=f['endereco'])
            
            if st.form_submit_button("Gravar Unidade", use_container_width=True):
                if not nome or not cnpj: 
                    st.error("Razão Social e CNPJ são obrigatórios.")
                else:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        if f['id']: 
                            cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, f['id']))
                        else: 
                            cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido))
                        conn.commit()
                        carregar_empresas_ativas.clear()
                        st.success("Gravado com sucesso!")
                        st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""}
                    except Exception as e: 
                        conn.rollback()
                        st.error(f"Erro: {e}")
                    finally: 
                        conn.close()

    with tab_lista:
        df = carregar_empresas_ativas()
        for _, row in df.iterrows():
            col_info, col_btn = st.columns([5, 1])
            col_info.markdown(f"**{row['nome']}** ({row['apelido_unidade'] or row['tipo']})<br><small>CNPJ: {row['cnpj']}</small>", unsafe_allow_html=True)
            if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                conn = get_db_connection()
                df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                conn.close()
                st.session_state.dados_form = df_edit.iloc[0].to_dict()
                st.rerun()
            st.divider()

# --- 6. MÓDULO APURAÇÃO ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
        if df_emp.empty: 
            st.warning("Nenhuma unidade vinculada a este utilizador.")
            return

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
        
        v_base = st.number_input("Valor Total da Nota / Base (R$)", min_value=0.00, step=100.0, key=f"base_{fk}")
        v_pis_ret = v_cof_ret = 0.0
        teve_retencao = False

        if op_row['tipo'] == 'RECEITA':
            teve_retencao = st.checkbox("☑️ Houve Retenção na Fonte nesta nota?", key=f"check_ret_{fk}")
            if teve_retencao:
                st.info("Informe os valores exatos retidos no documento.")
                c_p, c_c = st.columns(2)
                v_pis_ret = c_p.number_input("Valor PIS Retido (R$)", min_value=0.00, step=10.0, key=f"p_ret_{fk}")
                v_cof_ret = c_c.number_input("Valor COFINS Retido (R$)", min_value=0.00, step=10.0, key=f"c_ret_{fk}")

        hist = st.text_input("Histórico / Observação", key=f"hist_{fk}")
        c_retro, c_origem = st.columns([1, 1])
        retro = c_retro.checkbox("Lançamento Extemporâneo", key=f"retro_{fk}")
        comp_origem = c_origem.text_input("Mês de Origem (MM/AAAA)", disabled=not retro, key=f"origem_{fk}")
        
        exige_doc = retro or teve_retencao
        
        if exige_doc:
            c_nota, c_forn = st.columns([1, 2])
            num_nota = c_nota.text_input("Nº da Nota Fiscal", key=f"nota_{fk}")
            fornecedor = c_forn.text_input("Tomador / Fornecedor", key=f"forn_{fk}")
        else: 
            num_nota = fornecedor = None
        
        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if v_base <= 0: 
                st.warning("A base de cálculo deve ser maior que zero.")
            elif teve_retencao and v_pis_ret == 0 and v_cof_ret == 0: 
                st.warning("Informe os valores retidos.")
            elif exige_doc and (not num_nota or not fornecedor or (retro and not comp_origem)): 
                st.error("Preencha todos os dados do documento (Nota, Tomador e Competência de Origem se aplicável).")
            else:
                vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
                
                st.session_state.rascunho_lancamentos.append({
                    "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, 
                    "v_base": v_base, "v_pis": vp, "v_cofins": vc, 
                    "v_pis_ret": v_pis_ret, "v_cof_ret": v_cof_ret,
                    "hist": hist, "retro": retro, "origem": comp_origem if retro else None,
                    "nota": num_nota, "fornecedor": fornecedor
                })
                st.session_state.form_key += 1
                st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        
        altura_dinamica = 390
        if teve_retencao: altura_dinamica += 135  
        if exige_doc: altura_dinamica += 85        
        
        with st.container(height=altura_dinamica, border=True): 
            if not st.session_state.rascunho_lancamentos: 
                st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    retro_badge = f" <span style='color:red;font-size:10px;'>(EXTEMP: {it['origem']})</span>" if it['retro'] else ""
                    ret_badge = f" <span style='color:orange;font-size:10px;'>(RETENÇÃO)</span>" if it.get('v_pis_ret', 0) > 0 or it.get('v_cof_ret', 0) > 0 else ""
                    c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}{ret_badge}<br>PIS: {formatar_moeda(it['v_pis']).replace('$', '&#36;')} | COF: {formatar_moeda(it['v_cofins']).replace('$', '&#36;')}</small>", unsafe_allow_html=True)
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base']).replace('$', '&#36;')}</span>", unsafe_allow_html=True)
                    if c_del.button("×", key=f"del_{i}"): 
                        st.session_state.rascunho_lancamentos.pop(i)
                        st.rerun()
                    st.markdown("<hr style='margin: 5px 0;'>", unsafe_allow_html=True)

        if st.button("Gravar na Base de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos)==0):
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                m, a = competencia.split('/')
                comp_db = f"{a}-{m.zfill(2)}"
                cursor.execute("START TRANSACTION")
                for it in st.session_state.rascunho_lancamentos:
                    query = """INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, valor_pis_retido, valor_cofins_retido, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)"""
                    cursor.execute(query, (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it.get('v_pis_ret', 0), it.get('v_cof_ret', 0), it['hist'], st.session_state.username, it['retro'], it['origem'], it['nota'], it['fornecedor']))
                conn.commit()
                st.session_state.rascunho_lancamentos = []
                st.success("Sucesso!")
                st.rerun()
            except Exception as e: 
                conn.rollback()
                st.error(f"Erro: {e}")
            finally: 
                conn.close()

# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO ---
def modulo_relatorios():
    st.markdown("### Integração ERP e PDF Analítico")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id: 
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

    with st.form("form_export"):
        c1, c2 = st.columns([2, 1])
        emp_sel = c1.selectbox("Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
        emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
        emp_row = df_emp[df_emp['id'] == emp_id].iloc[0]
        competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)
        
        if st.form_submit_button("Gerar Ficheiros"):
            conn = get_db_connection()
            try:
                m, a = competencia.split('/')
                comp_db = f"{a}-{m.zfill(2)}"
                
                query = f"""SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, 
                            o.conta_deb_pis, o.conta_cred_pis, o.pis_h_codigo, o.pis_h_texto,
                            o.conta_deb_cof, o.conta_cred_cof, o.cofins_h_codigo, o.cofins_h_texto,
                            o.conta_deb_custo, o.conta_cred_custo, o.custo_h_codigo, o.custo_h_texto,
                            o.ret_pis_conta_deb, o.ret_pis_conta_cred, o.ret_pis_h_codigo, o.ret_pis_h_texto,
                            o.ret_cofins_conta_deb, o.ret_cofins_conta_cred, o.ret_cofins_h_codigo, o.ret_cofins_h_texto
                            FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id 
                            WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'"""
                df_export = pd.read_sql(query, conn)
                
                if df_export.empty: 
                    st.warning("Sem dados para exportar.")
                else:
                    linhas_excel = []
                    total_pis_rec = 0
                    total_cof_rec = 0
                    
                    def processar_texto(txt, op_nome):
                        if not txt: return f"VLR REF {op_nome} COMP {competencia}"
                        return txt.replace("{operacao}", op_nome).replace("{competencia}", competencia)

                    for _, row in df_export.iterrows():
                        data_str = row['data_lancamento'].strftime('%d/%m/%Y') if pd.notnull(row['data_lancamento']) else ''
                        
                        if row['op_tipo'] == 'DESPESA': 
                            total_pis_rec += row['valor_pis']
                            total_cof_rec += row['valor_cofins']
                        
                        # Linhas do Excel ERP... (mantido conforme anterior)
                        if pd.notnull(row['conta_deb_pis']) and pd.notnull(row['conta_cred_pis']):
                            t_hist_pis = processar_texto(row.get('pis_h_texto'), row['op_nome'])
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_pis']).replace('.', ''), "Credito": str(row['conta_cred_pis']).replace('.', ''), "Data": data_str, "Valor": row['valor_pis'], "Cod. Historico": row.get('pis_h_codigo', ''), "Historico": f"PIS - {t_hist_pis}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                        
                        if pd.notnull(row['conta_deb_cof']) and pd.notnull(row['conta_cred_cof']):
                            t_hist_cof = processar_texto(row.get('cofins_h_texto'), row['op_nome'])
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_cof']).replace('.', ''), "Credito": str(row['conta_cred_cof']).replace('.', ''), "Data": data_str, "Valor": row['valor_cofins'], "Cod. Historico": row.get('cofins_h_codigo', ''), "Historico": f"COF - {t_hist_cof}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                        
                        if pd.notnull(row['conta_deb_custo']) and pd.notnull(row['conta_cred_custo']):
                            t_hist_custo = processar_texto(row.get('custo_h_texto'), row['op_nome'])
                            v_custo = row['valor_base'] - row['valor_pis'] - row['valor_cofins']
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_custo']).replace('.', ''), "Credito": str(row['conta_cred_custo']).replace('.', ''), "Data": data_str, "Valor": v_custo, "Cod. Historico": row.get('custo_h_codigo', ''), "Historico": f"CUSTO LIQ - {t_hist_custo}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                        
                        if row.get('valor_pis_retido', 0) > 0 and pd.notnull(row.get('ret_pis_conta_deb')) and pd.notnull(row.get('ret_pis_conta_cred')):
                            t_hist_ret_pis = processar_texto(row.get('ret_pis_h_texto'), row['op_nome'])
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['ret_pis_conta_deb']).replace('.', ''), "Credito": str(row['ret_pis_conta_cred']).replace('.', ''), "Data": data_str, "Valor": row['valor_pis_retido'], "Cod. Historico": row.get('ret_pis_h_codigo', ''), "Historico": f"RET PIS - {t_hist_ret_pis}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                        
                        if row.get('valor_cofins_retido', 0) > 0 and pd.notnull(row.get('ret_cofins_conta_deb')) and pd.notnull(row.get('ret_cofins_conta_cred')):
                            t_hist_ret_cof = processar_texto(row.get('ret_cofins_h_texto'), row['op_nome'])
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['ret_cofins_conta_deb']).replace('.', ''), "Credito": str(row['ret_cofins_conta_cred']).replace('.', ''), "Data": data_str, "Valor": row['valor_cofins_retido'], "Cod. Historico": row.get('ret_cofins_h_codigo', ''), "Historico": f"RET COF - {t_hist_ret_cof}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})

                    if total_pis_rec > 0 and emp_row['conta_transf_pis']:
                        linhas_excel.append({"Lancto Aut.": "", "Debito": str(emp_row['conta_transf_pis']).replace('.', ''), "Credito": "", "Data": data_str, "Valor": total_pis_rec, "Cod. Historico": "", "Historico": f"Vr. transferido para apuração do PIS n/ mês {competencia}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": "", "Complemento": ""})
                    if total_cof_rec > 0 and emp_row['conta_transf_cofins']:
                        linhas_excel.append({"Lancto Aut.": "", "Debito": str(emp_row['conta_transf_cofins']).replace('.', ''), "Credito": "", "Data": data_str, "Valor": total_cof_rec, "Cod. Historico": "", "Historico": f"Vr. transferido para apuração da COFINS n/ mês {competencia}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": "", "Complemento": ""})

                    df_xlsx = pd.DataFrame(linhas_excel)
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer: 
                        df_xlsx.to_excel(writer, index=False, sheet_name='Lançamentos')
                    buffer.seek(0)
                    
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Arial", 'B', 12)
                    pdf.cell(190, 8, "DEMONSTRATIVO DE APURACAO - PIS E COFINS", ln=True, align='C')
                    pdf.ln(3)
                    pdf.set_font("Arial", 'B', 9); pdf.cell(25, 6, "Competencia:"); pdf.set_font("Arial", '', 9); pdf.cell(165, 6, f"{competencia}", ln=True)
                    pdf.set_font("Arial", 'B', 9); pdf.cell(25, 6, "Razao Social:"); pdf.set_font("Arial", '', 9); pdf.cell(105, 6, f"{emp_row['nome']}"); pdf.set_font("Arial", 'B', 9); pdf.cell(15, 6, "CNPJ:"); pdf.set_font("Arial", '', 9); pdf.cell(45, 6, f"{emp_row['cnpj']}", ln=True)
                    pdf.ln(5)
                    pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True)
                    pdf.set_font("Arial", '', 9)
                    deb_pis = deb_cof = cred_pis = cred_cof = ret_pis = ret_cof = 0
                    for _, r in df_export[df_export['op_tipo'] == 'RECEITA'].iterrows():
                        pdf.cell(90, 6, f"{r['op_nome']}"[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                        deb_pis += r['valor_pis']; deb_cof += r['valor_cofins']
                    
                    pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "2. BASE DE CALCULO DOS INSUMOS E CREDITOS", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True)
                    pdf.set_font("Arial", '', 9)
                    for _, r in df_export[df_export['op_tipo'] == 'DESPESA'].iterrows():
                        pdf.cell(90, 6, f"{r['op_nome']}"[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                        cred_pis += r['valor_pis']; cred_cof += r['valor_cofins']

                    pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "3. RETENCOES NA FONTE (ORIGEM EM RECEITAS)", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(125, 6, "Documento / Operacao", 1); pdf.cell(30, 6, "PIS Retido", 1); pdf.cell(35, 6, "COF Retida", 1, ln=True)
                    pdf.set_font("Arial", '', 9)
                    df_ret = df_export[(df_export['op_tipo'] == 'RECEITA') & ((df_export['valor_pis_retido'] > 0) | (df_export['valor_cofins_retido'] > 0))]
                    for _, r in df_ret.iterrows():
                        nome_doc = f"Nota: {r['num_nota']} - {r['op_nome']}" if r['num_nota'] else r['op_nome']
                        pdf.cell(125, 6, nome_doc[:70], 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis_retido']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins_retido']), 1, ln=True)
                        ret_pis += r['valor_pis_retido']; ret_cof += r['valor_cofins_retido']

                    pdf.ln(10); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "4. QUADRO DE APURACAO FINAL", ln=True); pdf.set_font("Arial", '', 10)
                    res_pis = deb_pis - cred_pis - ret_pis; res_cof = deb_cof - cred_cof - ret_cof
                    pdf.cell(120, 6, "Total Imposto a Recolher:", 0); pdf.cell(35, 6, formatar_moeda(max(0, res_pis)), 0); pdf.cell(35, 6, formatar_moeda(max(0, res_cof)), 0, ln=True)
                    pdf.cell(120, 6, "Saldo Credor para o Mes Seguinte:", 0); pdf.cell(35, 6, formatar_moeda(abs(min(0, res_pis))), 0); pdf.cell(35, 6, formatar_moeda(abs(min(0, res_cof))), 0, ln=True)
                    pdf_bytes = pdf.output(dest='S').encode('latin1')
                    st.success("Ficheiros processados com sucesso!")
                    c_btn1, c_btn2, _ = st.columns([1, 1, 2]); c_btn1.download_button("⬇️ XLSX (ERP)", data=buffer, file_name=f"LCTOS_{comp_db}.xlsx"); c_btn2.download_button("⬇️ PDF (Resumo)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
            except Exception as e: st.error(f"Erro na geração: {e}")
            finally: conn.close()


# --- 7.5 MÓDULO IMOBILIZADO E DEPRECIAÇÃO (NOVO E PLANO) ---
def modulo_imobilizado():
    st.markdown("### 🏢 Gestão de Ativo Imobilizado")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
        if df_emp.empty: 
            st.warning("Nenhuma unidade vinculada a este utilizador.")
            return

    c_emp, c_vazio = st.columns([2, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1), key="imo_emp")
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1) == emp_sel].iloc[0]['id'])

    st.divider()
    # Layout plano com duas colunas, idêntico à Apuração
    col_in, col_ras = st.columns([1, 1], gap="large")
    
    conn = get_db_connection()
    df_g = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id}", conn)
    conn.close()

    with col_in:
        st.markdown("#### Cadastro do Bem")
        if df_g.empty:
            st.warning("Cadastre os Grupos em Parâmetros Contábeis primeiro.")
        else:
            with st.form("form_novo_bem"):
                g_sel = st.selectbox("Grupo / Espécie", df_g['nome_grupo'].tolist())
                g_row = df_g[df_g['nome_grupo'] == g_sel].iloc[0]
                
                desc = st.text_input("Descrição do Bem (Ex: Notebook ASUS F16)")
                c_n, c_f = st.columns(2)
                nf = c_n.text_input("Nº da Nota Fiscal")
                forn = c_f.text_input("Fornecedor")
                
                c_v, c_d = st.columns(2)
                v_aq = c_v.number_input("Valor de Aquisição (R$)", min_value=0.0, step=100.0)
                dt_c = c_d.date_input("Data da Compra")
                
                st.markdown("##### Contas Contábeis da Operação")
                st.info("Estas contas foram puxadas do grupo, mas você pode editá-las para este bem específico.")
                c_cd, c_cc = st.columns(2)
                c_desp = c_cd.text_input("Conta Despesa (D)", value=g_row['conta_contabil_despesa'])
                c_dep = c_cc.text_input("Conta Dep. Acumulada (C)", value=g_row['conta_contabil_dep_acumulada'])

                st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
                if st.form_submit_button("Registrar no Inventário", use_container_width=True):
                    if not desc or v_aq <= 0:
                        st.error("Descrição e Valor de Aquisição são obrigatórios e maiores que zero.")
                    else:
                        conn = get_db_connection(); cursor = conn.cursor()
                        cursor.execute("""INSERT INTO bens_imobilizado 
                            (tenant_id, grupo_id, descricao_item, numero_nota_fiscal, nome_fornecedor, data_compra, valor_compra, conta_despesa, conta_dep_acumulada) 
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", 
                            (emp_id, int(g_row['id']), desc, nf, forn, dt_c, v_aq, c_desp, c_dep))
                        conn.commit(); conn.close()
                        st.success("Bem registrado com sucesso!")
                        st.rerun()

    with col_ras:
        st.markdown("#### Processamento em Lote")
        with st.container(height=180, border=True):
            st.write("Gere o arquivo do Alterdata contendo a cota mensal de depreciação de todos os bens ativos da empresa.")
            c_m, c_a = st.columns(2)
            m_proc = c_m.selectbox("Mês de Processamento", range(1, 13), index=hoje_br.month - 1)
            a_proc = c_a.number_input("Ano de Processamento", value=hoje_br.year)
            
            if st.button("Gerar Exportação de Lançamentos (XLSX)", type="primary", use_container_width=True):
                conn = get_db_connection()
                query = f"""SELECT b.*, g.taxa_anual_percentual 
                            FROM bens_imobilizado b JOIN grupos_imobilizado g ON b.grupo_id = g.id 
                            WHERE b.tenant_id = {emp_id} AND b.status = 'ativo'"""
                df_bens = pd.read_sql(query, conn)
                conn.close()
                
                if df_bens.empty:
                    st.warning("Nenhum bem ativo encontrado para esta unidade.")
                else:
                    linhas = []
                    for _, b in df_bens.iterrows():
                        cota = (b['valor_compra'] * (b['taxa_anual_percentual']/100)) / 12
                        c_d_use = b.get('conta_despesa') or b.get('conta_contabil_despesa', '')
                        c_c_use = b.get('conta_dep_acumulada') or b.get('conta_contabil_dep_acumulada', '')
                        
                        linhas.append({
                            "Lancto Aut.": "", "Debito": str(c_d_use).replace('.', ''), "Credito": str(c_c_use).replace('.', ''),
                            "Data": f"01/{m_proc:02d}/{a_proc}", "Valor": cota, "Cod. Historico": "", 
                            "Historico": f"DEPRECIACAO REF {m_proc:02d}/{a_proc} - {b['descricao_item']}",
                            "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": b['numero_nota_fiscal'] or b['id'], "Complemento": ""
                        })
                    
                    df_xlsx = pd.DataFrame(linhas)
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        df_xlsx.to_excel(writer, index=False, sheet_name='Depreciacao')
                    buffer.seek(0)
                    st.download_button("⬇️ Baixar XLSX (ERP Alterdata)", data=buffer, file_name=f"DEPREC_{m_proc:02d}_{a_proc}.xlsx", use_container_width=True)

        st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
        st.markdown("#### Consultar Inventário")
        busca = st.text_input("Buscar Item (Nome, NF ou Fornecedor)")
        if st.button("Pesquisar", use_container_width=True):
            conn = get_db_connection()
            q_busca = f"SELECT b.*, g.taxa_anual_percentual FROM bens_imobilizado b JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id} AND (b.descricao_item LIKE '%{busca}%' OR b.numero_nota_fiscal LIKE '%{busca}%' OR b.nome_fornecedor LIKE '%{busca}%')"
            df_res = pd.read_sql(q_busca, conn)
            conn.close()
            
            if not df_res.empty:
                st.dataframe(df_res[['descricao_item', 'numero_nota_fiscal', 'data_compra', 'valor_compra']], use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum bem encontrado com este filtro.")


# --- 8. MÓDULO PARÂMETROS CONTÁBEIS (COM FERRAMENTAS ADM) ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": 
        st.error("Acesso restrito.")
        return
        
    st.markdown("### ⚙️ Parâmetros Contábeis e Integração ERP")
    df_op = carregar_operacoes()
    op_nomes = df_op['nome'].tolist()
    
    # --- MUDANÇA 2: 5ª ABA ADICIONADA PRESERVANDO AS 4 ORIGINAIS ---
    tab_edit, tab_novo, tab_fecho, tab_limpeza, tab_imob = st.tabs(["✏️ Editar Existente", "➕ Nova Operação", "🏢 Fecho por Empresa", "🧹 Auditoria/Limpeza", "📦 Grupos Imobilizado"])
    
    with tab_edit:
        sel_op = st.selectbox("Selecione a Operação:", op_nomes)
        row_op = df_op[df_op['nome'] == sel_op].iloc[0]
        oid = row_op['id']
        
        with st.form("form_edit_param"):
            st.markdown("##### Configuração PIS")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
            p_deb = c1.text_input("Débito PIS", value=row_op['conta_deb_pis'] or "", key=f"pd_{oid}")
            p_cred = c2.text_input("Crédito PIS", value=row_op['conta_cred_pis'] or "", key=f"pc_{oid}")
            p_cod = c3.text_input("Cód ERP PIS", value=row_op.get('pis_h_codigo', ''), key=f"pcd_{oid}")
            p_txt = c4.text_input("Texto Padrão PIS", value=row_op.get('pis_h_texto', ''), key=f"ptx_{oid}")
            
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2])
            c_deb = c5.text_input("Débito COFINS", value=row_op['conta_deb_cof'] or "", key=f"cd_{oid}")
            c_cred = c6.text_input("Crédito COFINS", value=row_op['conta_cred_cof'] or "", key=f"cc_{oid}")
            c_cod = c7.text_input("Cód ERP COFINS", value=row_op.get('cofins_h_codigo', ''), key=f"ccd_{oid}")
            c_txt = c8.text_input("Texto Padrão COF", value=row_op.get('cofins_h_texto', ''), key=f"ctx_{oid}")
            
            st.markdown("##### Configuração CUSTO/VALOR LÍQUIDO")
            c9, c10, c11, c12 = st.columns([1, 1, 1, 2])
            cu_deb = c9.text_input("Débito Custo", value=row_op['conta_deb_custo'] or "", key=f"cud_{oid}")
            cu_cred = c10.text_input("Crédito Custo", value=row_op['conta_cred_custo'] or "", key=f"cuc_{oid}")
            cu_cod = c11.text_input("Cód ERP Custo", value=row_op.get('custo_h_codigo', ''), key=f"cucd_{oid}")
            cu_txt = c12.text_input("Texto Padrão Custo", value=row_op.get('custo_h_texto', ''), key=f"cutx_{oid}")

            if row_op['tipo'] == 'RECEITA':
                with st.expander("Configuração de Retenção na Fonte", expanded=False):
                    cr1, cr2, cr3, cr4 = st.columns([1, 1, 1, 2])
                    r_p_deb = cr1.text_input("Débito PIS Ret", value=row_op.get('ret_pis_conta_deb', ''), key=f"rpd_{oid}")
                    r_p_cred = cr2.text_input("Crédito PIS Ret", value=row_op.get('ret_pis_conta_cred', ''), key=f"rpc_{oid}")
                    r_p_cod = cr3.text_input("Cód ERP PIS Ret", value=row_op.get('ret_pis_h_codigo', ''), key=f"rpcd_{oid}")
                    r_p_txt = cr4.text_input("Histórico PIS Ret", value=row_op.get('ret_pis_h_texto', ''), key=f"rptx_{oid}")
                    cr5, cr6, cr7, cr8 = st.columns([1, 1, 1, 2])
                    r_c_deb = cr5.text_input("Débito COF Ret", value=row_op.get('ret_cofins_conta_deb', ''), key=f"rcd_{oid}")
                    r_c_cred = cr6.text_input("Crédito COF Ret", value=row_op.get('ret_cofins_conta_cred', ''), key=f"rcc_{oid}")
                    r_c_cod = cr7.text_input("Cód ERP COF Ret", value=row_op.get('ret_cofins_h_codigo', ''), key=f"rccd_{oid}")
                    r_c_txt = cr8.text_input("Histórico COF Ret", value=row_op.get('ret_cofins_h_texto', ''), key=f"rctx_{oid}")
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
            st.divider()
            if st.form_submit_button("Registar Nova Operação"):
                if not novo_nome: st.error("O nome é obrigatório.")
                else:
                    nome_limpo = novo_nome.strip().lower()
                    if any(o.strip().lower() == nome_limpo for o in op_nomes):
                        st.error(f"Erro: Já existe uma operação chamada '{novo_nome}'. Verifique na aba 'Editar Existente'.")
                    else:
                        conn = get_db_connection(); cursor = conn.cursor()
                        try:
                            cursor.execute("INSERT INTO operacoes (nome, tipo) VALUES (%s,%s)", (novo_nome, novo_tipo))
                            conn.commit(); carregar_operacoes.clear(); st.success("Registada com sucesso!"); st.rerun()
                        except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
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
                            cursor.execute(f"DELETE FROM operacoes WHERE id={d['id']}")
                            conn.commit(); conn.close(); carregar_operacoes.clear(); st.rerun()

    with tab_fecho:
        st.markdown("##### Contas de Transferência / Fecho (Apuração Mensal)")
        df_emp_f = carregar_empresas_ativas()
        if not df_emp_f.empty:
            with st.form("form_fecho"):
                emp_sel_f = st.selectbox("Selecione a Empresa", df_emp_f.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
                emp_id_f = int(df_emp_f.loc[df_emp_f.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel_f].iloc[0]['id'])
                row_emp_f = df_emp_f[df_emp_f['id'] == emp_id_f].iloc[0]
                c1, c2 = st.columns(2)
                t_pis = c1.text_input("Conta Transferência PIS", value=row_emp_f.get('conta_transf_pis') or "")
                t_cofins = c2.text_input("Conta Transferência COFINS", value=row_emp_f.get('conta_transf_cofins') or "")
                if st.form_submit_button("Salvar Contas de Fecho"):
                    conn = get_db_connection(); cursor = conn.cursor()
                    cursor.execute("UPDATE empresas SET conta_transf_pis=%s, conta_transf_cofins=%s WHERE id=%s", (t_pis, t_cofins, emp_id_f))
                    conn.commit(); carregar_empresas_ativas.clear(); st.success("Atualizado!"); st.rerun()

    # --- MUDANÇA 3: ABA DE GRUPOS DO IMOBILIZADO PLANO SEM DESENHOS ---
    with tab_imob:
        df_e = carregar_empresas_ativas()
        e_sel = st.selectbox("Selecione a Empresa para Gerir Grupos", df_e.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1), key="sel_emp_grp")
        e_id = int(df_e.loc[df_e.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == e_sel].iloc[0]['id'])
        
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
                    n_g = st.text_input("Nome", value=g_row['nome_grupo'])
                    tx = st.number_input("Taxa Anual (%)", value=float(g_row['taxa_anual_percentual']))
                    cd = st.text_input("Conta Despesa", value=g_row['conta_contabil_despesa'])
                    cc = st.text_input("Conta Dep. Acumulada", value=g_row['conta_contabil_dep_acumulada'])
                    
                    if st.form_submit_button("Atualizar Grupo"):
                        conn = get_db_connection(); cursor = conn.cursor()
                        cursor.execute("UPDATE grupos_imobilizado SET nome_grupo=%s, taxa_anual_percentual=%s, conta_contabil_despesa=%s, conta_contabil_dep_acumulada=%s WHERE id=%s", (n_g, tx, cd, cc, g_row['id']))
                        conn.commit(); conn.close(); st.success("Atualizado com sucesso!"); st.rerun()
            else:
                st.info("Nenhum grupo cadastrado para esta empresa.")
                
        with col_new:
            st.markdown("##### Criar Novo Grupo")
            with st.form("nv_grp"):
                n_g_n = st.text_input("Nome do Grupo (Ex: Máquinas)")
                tx_n = st.number_input("Taxa Anual (%)", min_value=0.0)
                cd_n = st.text_input("Conta Despesa (D)")
                cc_n = st.text_input("Conta Dep. Acumulada (C)")
                
                if st.form_submit_button("Adicionar Grupo"):
                    if n_g_n:
                        conn = get_db_connection(); cursor = conn.cursor()
                        cursor.execute("INSERT INTO grupos_imobilizado (tenant_id, nome_grupo, taxa_anual_percentual, conta_contabil_despesa, conta_contabil_dep_acumulada) VALUES (%s,%s,%s,%s,%s)", (e_id, n_g_n, tx_n, cd_n, cc_n))
                        conn.commit(); conn.close(); st.success("Criado com sucesso!"); st.rerun()
                    else:
                        st.error("O Nome do grupo é obrigatório.")

# --- 9. GESTÃO DE UTILIZADORES ---
def modulo_usuarios():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": 
        st.error("Acesso restrito.")
        return
    st.markdown("### 👥 Gestão de Utilizadores e Acessos")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN": df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
    tab_novo, tab_lista = st.tabs(["➕ Novo Utilizador", "Equipa Registada"])
    with tab_novo:
        with st.form("form_novo_user"):
            c_emp, c_nivel = st.columns([2, 1])
            if st.session_state.nivel_acesso == "SUPER_ADMIN":
                emp_sel = c_emp.selectbox("Vincular à Empresa:", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
                emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
            else: c_emp.text_input("Vincular à Empresa:", value=df_emp.iloc[0]['nome'], disabled=True); emp_id = st.session_state.empresa_id
            nivel = c_nivel.selectbox("Perfil de Acesso", ["CLIENT_OPERATOR", "CLIENT_ADMIN"])
            c_nome, c_user, c_pass = st.columns([2, 1.5, 1.5]); nome_u = c_nome.text_input("Nome Completo"); log_u = c_user.text_input("Login"); pass_u = c_pass.text_input("Palavra-passe", type="password")
            if st.form_submit_button("Criar Acesso"):
                conn = get_db_connection(); cursor = conn.cursor()
                try:
                    hash_s = bcrypt.hashpw(pass_u.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    cursor.execute("INSERT INTO usuarios (nome, username, senha_hash, nivel_acesso, empresa_id, status_usuario) VALUES (%s, %s, %s, %s, %s, 'ATIVO')", (nome_u, log_u, hash_s, nivel, emp_id))
                    conn.commit(); st.success("Utilizador criado!"); st.rerun()
                except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
                finally: conn.close()
    with tab_lista:
        conn = get_db_connection(); query = "SELECT u.nome, u.username, u.nivel_acesso, e.nome as empresa FROM usuarios u LEFT JOIN empresas e ON u.empresa_id = e.id"
        if st.session_state.nivel_acesso != "SUPER_ADMIN": query += f" WHERE u.empresa_id = {st.session_state.empresa_id}"
        st.dataframe(pd.read_sql(query, conn), use_container_width=True, hide_index=True); conn.close()

# --- 10. MENU LATERAL (PRESERVADO INTACTO COM DATA E RELÓGIO) ---
with st.sidebar:
    # DATA E DIA DA SEMANA (Fuso Brasília UTC-3)
    dias = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    st.markdown(f"""
        <div style='text-align: center; color: #64748b; font-size: 0.9em; margin-bottom: 10px; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px;'>
            {dias[hoje_br.weekday()]}<br>
            <b style='color: #004b87;'>{hoje_br.strftime('%d/%m/%Y')}</b>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    
    # --- MUDANÇA 4: IMOBILIZADO INSERIDO APÓS RELATÓRIOS ---
    menu = st.radio("Módulos", [
        "Gestão de Empresas", 
        "Apuração Mensal", 
        "Relatórios e Integração", 
        "📦 Imobilizado & Depreciação",
        "⚙️ Parâmetros Contábeis", 
        "👥 Gestão de Utilizadores"
    ])
    st.write("---")
    st.link_button("🔗 Auditoria de Vendas", "https://conciliador-contabil-hsppms6xpbjstvmmfktgkc.streamlit.app/", use_container_width=True)
    st.write("---")
    if st.button("🚪 Encerrar Sessão", use_container_width=True): st.session_state.autenticado = False; st.rerun()

# --- 11. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "📦 Imobilizado & Depreciação": modulo_imobilizado() # Nova Rota
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
elif menu == "👥 Gestão de Utilizadores": modulo_usuarios()
