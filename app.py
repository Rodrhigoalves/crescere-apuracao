import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import io
import bcrypt
from fpdf import FPDF # Requer: pip install fpdf

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
    df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo, apelido_unidade FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
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
if 'dados_form' not in st.session_state: st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": ""}
if 'rascunho_lancamentos' not in st.session_state: st.session_state.rascunho_lancamentos = []

# Estados para limpeza automática do formulário
if 'f_base' not in st.session_state: st.session_state.f_base = 0.0
if 'f_hist' not in st.session_state: st.session_state.f_hist = ""
if 'f_retro' not in st.session_state: st.session_state.f_retro = False
if 'f_origem' not in st.session_state: st.session_state.f_origem = ""
if 'f_nota' not in st.session_state: st.session_state.f_nota = ""
if 'f_forn' not in st.session_state: st.session_state.f_forn = ""

hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

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
                    if user_data['username'].lower() == "rodrhigo": st.session_state.nivel_acesso = "SUPER_ADMIN"
                    else: st.session_state.nivel_acesso = user_data['nivel_acesso']
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
            regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
            apelido = c_apelido.text_input("Apelido da Unidade (Ex: Filial SP)", value=f.get('apelido_unidade', ''))
            
            c6, c7 = st.columns([1, 3])
            cnae = c6.text_input("CNAE Principal", value=f['cnae'])
            endereco = c7.text_input("Endereço Completo", value=f['endereco'])
            
            if st.form_submit_button("Gravar Unidade", use_container_width=True):
                if not nome or not cnpj: st.error("Razão Social e CNPJ são obrigatórios.")
                else:
                    conn = get_db_connection(); cursor = conn.cursor()
                    try:
                        if f['id']: 
                            cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, f['id']))
                        else: 
                            cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido))
                        conn.commit()
                        st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": ""}
                        carregar_empresas_ativas.clear()
                        st.success("Dados gravados com sucesso!")
                    except Exception as e: conn.rollback(); st.error(f"Erro ao gravar: {e}")
                    finally: conn.close()

    with tab_lista:
        df = carregar_empresas_ativas()
        for _, row in df.iterrows():
            col_info, col_btn = st.columns([5, 1])
            unidade_desc = row['apelido_unidade'] if row['apelido_unidade'] else row['tipo']
            col_info.markdown(f"**{row['nome']}** ({unidade_desc})<br><small>CNPJ: {row['cnpj']} | Regime: {row['regime']}</small>", unsafe_allow_html=True)
            if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                conn = get_db_connection()
                df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                conn.close()
                st.session_state.dados_form = df_edit.iloc[0].to_dict()
                st.rerun()
            st.divider()

# --- 6. MÓDULO APURAÇÃO (SIMETRIA, LIMPEZA E EXTRATO) ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    df_emp = carregar_empresas_ativas()
    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{'DÉBITO' if x['tipo'] == 'RECEITA' else 'CRÉDITO'}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Selecione a Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] if r['apelido_unidade'] else r['tipo']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] if r['apelido_unidade'] else r['tipo']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Operador (Audit)", value=st.session_state.usuario_logado, disabled=True)

    st.divider()
    col_in, col_ras = st.columns([1, 1], gap="large")

    with col_in:
        st.markdown("#### Novo Lançamento")
        op_sel = st.selectbox("Classificação da Operação", df_op['nome_exibicao'].tolist())
        v_base = st.number_input("Valor da Base de Cálculo (R$)", min_value=0.00, step=100.0, key="f_base")
        hist = st.text_input("Histórico / Observação", key="f_hist")
        
        c_retro, c_origem = st.columns([1, 1])
        retro = c_retro.checkbox("Lançamento Extemporâneo", key="f_retro")
        comp_origem = c_origem.text_input("Mês de Origem (MM/AAAA)", disabled=not st.session_state.f_retro, key="f_origem")
        
        if st.session_state.f_retro:
            c_nota, c_forn = st.columns([1, 2])
            num_nota = c_nota.text_input("Nº da Nota Fiscal", key="f_nota")
            fornecedor = c_forn.text_input("Fornecedor", key="f_forn")
        else: num_nota = fornecedor = None
        
        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if st.session_state.f_base <= 0: st.warning("A base de cálculo deve ser maior que zero.")
            elif st.session_state.f_retro and (not st.session_state.f_origem or not st.session_state.f_nota or not st.session_state.f_forn): 
                st.error("Preencha todos os dados do documento retroativo para a auditoria.")
            else:
                op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
                vp, vc = calcular_impostos(regime, op_row['nome'], st.session_state.f_base)
                st.session_state.rascunho_lancamentos.append({
                    "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, 
                    "v_base": st.session_state.f_base, "v_pis": vp, "v_cofins": vc, "hist": st.session_state.f_hist, 
                    "retro": st.session_state.f_retro, "origem": st.session_state.f_origem if st.session_state.f_retro else None,
                    "nota": st.session_state.f_nota, "fornecedor": st.session_state.f_forn
                })
                # Limpeza automática dos campos
                st.session_state.f_base = 0.0; st.session_state.f_hist = ""; st.session_state.f_retro = False
                st.session_state.f_origem = ""; st.session_state.f_nota = ""; st.session_state.f_forn = ""
                st.rerun()

    with col_ras:
        st.markdown("#### Rascunho de Apuração")
        with st.container(height=390, border=True): 
            if not st.session_state.rascunho_lancamentos:
                st.markdown("<p style='text-align: center; color: #94a3b8; margin-top: 50px;'>Nenhum lançamento pendente.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    retro_badge = f" <span style='color:red; font-size:10px;'>(EXTEMP: {it['origem']})</span>" if it['retro'] else ""
                    c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}<br>PIS: {formatar_moeda(it['v_pis'])} | COF: {formatar_moeda(it['v_cofins'])}</small>", unsafe_allow_html=True)
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base'])}</span>", unsafe_allow_html=True)
                    if c_del.button("×", key=f"del_{i}"): st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                    st.markdown("<hr style='margin: 5px 0;'>", unsafe_allow_html=True)

        if st.button("Gravar Registos na Base de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos)==0):
            conn = get_db_connection(); cursor = conn.cursor()
            try:
                m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
                cursor.execute("START TRANSACTION")
                for it in st.session_state.rascunho_lancamentos:
                    query = """INSERT INTO lancamentos 
                               (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, 
                                historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor) 
                               VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)"""
                    valores = (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], 
                               it['hist'], st.session_state.username, it['retro'], it['origem'], it['nota'], it['fornecedor'])
                    cursor.execute(query, valores)
                conn.commit()
                st.session_state.rascunho_lancamentos = []
                st.success("Lançamentos auditados e gravados com sucesso!"); st.rerun()
            except Exception as e: conn.rollback(); st.error(f"Erro ao gravar: {e}")
            finally: conn.close()

    # Extrato e Auditoria Restaurados
    st.divider()
    st.markdown("#### Extrato Consolidado e Retificação de Auditoria")
    conn = get_db_connection()
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        query_ext = f"""
            SELECT l.id as ID, o.nome as Operacao, l.valor_base, l.valor_pis, l.valor_cofins, l.historico 
            FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id 
            WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO' ORDER BY l.id DESC
        """
        df_ext = pd.read_sql(query_ext, conn)
        if not df_ext.empty:
            df_view = df_ext.copy()
            df_view['valor_base'] = df_view['valor_base'].apply(formatar_moeda)
            df_view['valor_pis'] = df_view['valor_pis'].apply(formatar_moeda)
            df_view['valor_cofins'] = df_view['valor_cofins'].apply(formatar_moeda)
            st.dataframe(df_view, use_container_width=True, hide_index=True)
            
            with st.expander("⚠️ Retificar Lançamento (Gera Trilha de Auditoria)", expanded=False):
                with st.form("form_retifica"):
                    c_id, c_nv, c_mot = st.columns([1, 2, 4])
                    id_ret = c_id.number_input("ID", min_value=0, step=1)
                    n_val = c_nv.number_input("Novo Valor Base", min_value=0.0, step=50.0)
                    motivo = c_mot.text_input("Justificativa Legal/Contabilística")
                    
                    if st.form_submit_button("Confirmar Retificação SAP"):
                        if not motivo.strip(): st.error("A justificativa é obrigatória.")
                        elif id_ret not in df_ext['ID'].values: st.error("ID não encontrado ou inativo.")
                        else:
                            cursor = conn.cursor(dictionary=True)
                            try:
                                cursor.execute("START TRANSACTION")
                                cursor.execute("SELECT * FROM lancamentos WHERE id = %s", (id_ret,))
                                old = cursor.fetchone()
                                cursor.execute("UPDATE lancamentos SET status_auditoria='INATIVO', motivo_alteracao=%s WHERE id=%s", (f"RETIFICADO. Motivo: {motivo}", id_ret))
                                
                                cursor.execute("SELECT nome FROM operacoes WHERE id = %s", (old['operacao_id'],))
                                novo_pis, novo_cofins = calcular_impostos(regime, cursor.fetchone()['nome'], n_val)
                                
                                query_insert = """INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)"""
                                cursor.execute(query_insert, (old['empresa_id'], old['operacao_id'], old['competencia'], n_val, novo_pis, novo_cofins, f"[RETIFICA ID {id_ret}] {old['historico']}", st.session_state.username, old['origem_retroativa'], old['competencia_origem'], old['num_nota'], old['fornecedor']))
                                conn.commit(); st.success("Retificação concluída!"); st.rerun()
                            except Exception as e: conn.rollback(); st.error(f"Erro transacional: {e}")
        else: st.info("Nenhum lançamento ativo para esta competência.")
    except Exception as e: pass
    finally: conn.close()

# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO (LIMPEZA E PDF RICO) ---
def modulo_relatorios():
    st.markdown("### Integração ERP e PDF Analítico")
    df_emp = carregar_empresas_ativas()
    
    with st.form("form_export"):
        c1, c2 = st.columns([2, 1])
        emp_sel = c1.selectbox("Selecione a Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
        emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
        emp_row = df_emp[df_emp['id'] == emp_id].iloc[0]
        competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)
        
        submit_export = st.form_submit_button("Gerar Ficheiros de Exportação")
        
    if submit_export:
        conn = get_db_connection()
        try:
            m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            
            query = f"""
                SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, o.conta_debito, o.conta_credito, o.codigo_historico, o.texto_padrao_historico 
                FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id 
                WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'
            """
            df_export = pd.read_sql(query, conn)
            
            if df_export.empty: st.warning("Nenhum dado encontrado para exportação nesta competência.")
            else:
                # 1. Geração XLSX (Sem pontos nas contas)
                linhas_excel = []
                for _, row in df_export.iterrows():
                    data_str = row['data_lancamento'].strftime('%d/%m/%Y') if pd.notnull(row['data_lancamento']) else ''
                    texto_hist = row['texto_padrao_historico'].replace("{operacao}", row['op_nome']).replace("{competencia}", competencia) if row['texto_padrao_historico'] else f"VLR REF {row['op_nome']} COMP {competencia}"
                    
                    # Limpeza das contas
                    c_deb = str(row['conta_debito']).replace('.', '').replace('-', '') if pd.notnull(row['conta_debito']) else ""
                    c_cred = str(row['conta_credito']).replace('.', '').replace('-', '') if pd.notnull(row['conta_credito']) else ""
                    
                    linhas_excel.append({"Lancto Aut.": "", "Debito": c_deb, "Credito": c_cred, "Data": data_str, "Valor": row['valor_pis'], "Cod. Historico": row['codigo_historico'] or "", "Historico": f"PIS - {texto_hist}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                    linhas_excel.append({"Lancto Aut.": "", "Debito": c_deb, "Credito": c_cred, "Data": data_str, "Valor": row['valor_cofins'], "Cod. Historico": row['codigo_historico'] or "", "Historico": f"COF - {texto_hist}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                
                df_xlsx = pd.DataFrame(linhas_excel)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Lançamentos')
                buffer.seek(0)
                
                # 2. Geração PDF Analítico (Cabeçalho Rico e Sem Instruções)
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", 'B', 12)
                pdf.cell(190, 10, "DEMONSTRATIVO DE APURACAO - PIS E COFINS", ln=True, align='C')
                pdf.set_font("Arial", '', 10)
                pdf.cell(190, 6, f"Empresa: {emp_row['nome']} | CNPJ: {emp_row['cnpj']} | Competencia: {competencia}", ln=True, align='C')
                pdf.ln(5)
                
                pdf.set_font("Arial", 'B', 10)
                pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True)
                pdf.set_font("Arial", 'B', 9)
                pdf.cell(90, 6, "Natureza da Operacao", 1)
                pdf.cell(35, 6, "Base", 1)
                pdf.cell(30, 6, "PIS", 1)
                pdf.cell(35, 6, "COFINS", 1, ln=True)
                
                pdf.set_font("Arial", '', 9)
                deb_pis = deb_cof = cred_pis = cred_cof = 0
                for _, r in df_export[df_export['op_tipo'] == 'RECEITA'].iterrows():
                    extemp_txt = f" [EXTEMP: {r['competencia_origem']}]" if r['origem_retroativa'] else ""
                    pdf.cell(90, 6, f"{r['op_nome']}{extemp_txt}"[:50], 1)
                    pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1)
                    pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1)
                    pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    deb_pis += r['valor_pis']; deb_cof += r['valor_cofins']
                
                pdf.ln(5)
                pdf.set_font("Arial", 'B', 10)
                pdf.cell(190, 8, "2. BASE DE CALCULO DOS INSUMOS E CREDITOS", ln=True)
                pdf.set_font("Arial", 'B', 9)
                pdf.cell(90, 6, "Natureza da Operacao", 1)
                pdf.cell(35, 6, "Base", 1)
                pdf.cell(30, 6, "PIS", 1)
                pdf.cell(35, 6, "COFINS", 1, ln=True)

                pdf.set_font("Arial", '', 9)
                for _, r in df_export[df_export['op_tipo'] == 'DESPESA'].iterrows():
                    extemp_txt = f" [EXTEMP: {r['competencia_origem']}]" if r['origem_retroativa'] else ""
                    pdf.cell(90, 6, f"{r['op_nome']}{extemp_txt}"[:50], 1)
                    pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1)
                    pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1)
                    pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    cred_pis += r['valor_pis']; cred_cof += r['valor_cofins']

                pdf.ln(10)
                pdf.set_font("Arial", 'B', 10)
                pdf.cell(190, 8, "3. QUADRO DE APURACAO FINAL", ln=True)
                pdf.set_font("Arial", '', 10)
                
                res_pis = deb_pis - cred_pis
                res_cof = deb_cof - cred_cof
                
                # Textos limpos executivos
                pdf.cell(120, 6, "Total Imposto a Recolher:", 0)
                pdf.cell(35, 6, formatar_moeda(max(0, res_pis)), 0)
                pdf.cell(35, 6, formatar_moeda(max(0, res_cof)), 0, ln=True)
                
                pdf.cell(120, 6, "Saldo Credor para o Mes Seguinte:", 0)
                pdf.cell(35, 6, formatar_moeda(abs(min(0, res_pis))), 0)
                pdf.cell(35, 6, formatar_moeda(abs(min(0, res_cof))), 0, ln=True)

                query_ext = f"SELECT num_nota, fornecedor, competencia FROM lancamentos WHERE empresa_id={emp_id} AND competencia_origem='{competencia}' AND status_auditoria='ATIVO'"
                df_notas = pd.read_sql(query_ext, conn)
                if not df_notas.empty:
                    pdf.ln(10)
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(190, 6, "AVISO DE AUDITORIA: LANCAMENTOS EXTEMPORANEOS", ln=True)
                    pdf.set_font("Arial", '', 8)
                    pdf.multi_cell(190, 5, "Os documentos abaixo pertencem a esta competencia, mas foram rececionados com atraso e os seus creditos aproveitados em competencias posteriores:")
                    for _, n in df_notas.iterrows():
                        pdf.cell(190, 5, f"- NF-e: {n['num_nota']} | Fornecedor: {n['fornecedor']} | Aproveitado em: {n['competencia']}", ln=True)

                pdf_bytes = pdf.output(dest='S').encode('latin1')
                
                st.success("Ficheiros processados com sucesso!")
                c_btn1, c_btn2, _ = st.columns([1, 1, 2])
                c_btn1.download_button("⬇️ XLSX (ERP - Sem pontos)", data=buffer, file_name=f"LCTOS_{comp_db}.xlsx")
                c_btn2.download_button("⬇️ PDF (Conferência Analítica)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
        except Exception as e: st.error(f"Erro na geração: {e}")
        finally: conn.close()

# --- 8. MÓDULO PARÂMETROS CONTÁBEIS (CRUD) ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR":
        st.error("Acesso restrito aos Administradores.")
        return
    st.markdown("### ⚙️ Parâmetros Contábeis e Integração ERP")
    
    df_op = carregar_operacoes()
    op_nomes = df_op['nome'].tolist()
    
    with st.expander("✏️ Editar Parâmetros de uma Operação", expanded=True):
        with st.form("form_edit_param"):
            sel_op = st.selectbox("Selecione a Operação para configurar:", op_nomes)
            row_op = df_op[df_op['nome'] == sel_op].iloc[0]
            
            c1, c2 = st.columns(2)
            n_deb = c1.text_input("Conta Débito (Sem pontos)", value=row_op['conta_debito'] if pd.notnull(row_op['conta_debito']) else "")
            n_cred = c2.text_input("Conta Crédito (Sem pontos)", value=row_op['conta_credito'] if pd.notnull(row_op['conta_credito']) else "")
            
            c3, c4 = st.columns([1, 3])
            n_cod = c3.text_input("Código Histórico ERP", value=row_op['codigo_historico'] if pd.notnull(row_op['codigo_historico']) else "")
            n_txt = c4.text_input("Texto Padrão (Ex: VLR REF {operacao} COMP {competencia})", value=row_op['texto_padrao_historico'] if pd.notnull(row_op['texto_padrao_historico']) else "")
            
            if st.form_submit_button("Atualizar Operação"):
                if not n_cod and not n_txt:
                    st.error("Erro: O sistema ERP exige o Código do Histórico ou o Texto Padrão. Preencha pelo menos um.")
                else:
                    conn = get_db_connection(); cursor = conn.cursor()
                    try:
                        cursor.execute("UPDATE operacoes SET conta_debito=%s, conta_credito=%s, codigo_historico=%s, texto_padrao_historico=%s WHERE id=%s", (n_deb, n_cred, n_cod, n_txt, row_op['id']))
                        conn.commit()
                        carregar_operacoes.clear()
                        st.success("Parâmetros atualizados com sucesso!"); st.rerun()
                    except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
                    finally: conn.close()
                    
    st.divider()
    st.markdown("#### Lista de Operações Mapeadas")
    df_view = df_op[['nome', 'tipo', 'conta_debito', 'conta_credito', 'codigo_historico', 'texto_padrao_historico']].copy()
    df_view.columns = ["Operação", "Tipo", "Débito", "Crédito", "Cód. Histórico", "Texto Padrão"]
    st.dataframe(df_view, use_container_width=True, hide_index=True)

# --- 9. MENU LATERAL ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos do Sistema", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])
    st.write("---")
    st.link_button("🔗 Auditoria de Vendas", "https://conciliador-contabil-hsppms6xpbjstvmmfktgkc.streamlit.app/", use_container_width=True)
    st.write("---")
    if st.button("🚪 Encerrar Sessão", use_container_width=True):
        st.session_state.autenticado = False; st.rerun()

# --- 10. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
