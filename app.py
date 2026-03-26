import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import io
import bcrypt
from fpdf import FPDF

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
            regime = c4.selectbox("Regime", ["Lucro Real", "Lucro Presumido"], index=0 if f.get('regime') == "Lucro Real" else 1)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f.get('tipo') == "Matriz" else 1)
            apelido = c_apelido.text_input("Apelido da Unidade", value=f.get('apelido_unidade', ''))
            
            c6, c7 = st.columns([1, 3])
            cnae = c6.text_input("CNAE", value=f['cnae'])
            endereco = c7.text_input("Endereço", value=f['endereco'])
            
            # NOTA: Os campos de Conta Transferência foram movidos para o Módulo Parâmetros.

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
        
        if op_row['tipo'] == 'RETENÇÃO':
            v_base = 0.0
            st.info("Para Retenções na Fonte, informe os valores exatos retidos na nota.")
            c_p, c_c = st.columns(2)
            v_pis_ret = c_p.number_input("Valor PIS Retido (R$)", min_value=0.00, step=10.0, key=f"p_ret_{fk}")
            v_cof_ret = c_c.number_input("Valor COFINS Retido (R$)", min_value=0.00, step=10.0, key=f"c_ret_{fk}")
        else:
            v_base = st.number_input("Valor Total da Nota / Base (R$)", min_value=0.00, step=100.0, key=f"base_{fk}")
            v_pis_ret = v_cof_ret = 0.0

        hist = st.text_input("Histórico / Observação", key=f"hist_{fk}")
        c_retro, c_origem = st.columns([1, 1])
        retro = c_retro.checkbox("Lançamento Extemporâneo", key=f"retro_{fk}")
        comp_origem = c_origem.text_input("Mês de Origem (MM/AAAA)", disabled=not retro, key=f"origem_{fk}")
        
        if retro:
            c_nota, c_forn = st.columns([1, 2])
            num_nota = c_nota.text_input("Nº da Nota Fiscal", key=f"nota_{fk}")
            fornecedor = c_forn.text_input("Fornecedor", key=f"forn_{fk}")
        else: 
            num_nota = fornecedor = None
        
        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if op_row['tipo'] != 'RETENÇÃO' and v_base <= 0: 
                st.warning("A base de cálculo deve ser maior que zero.")
            elif op_row['tipo'] == 'RETENÇÃO' and v_pis_ret == 0 and v_cof_ret == 0: 
                st.warning("Informe os valores retidos.")
            elif retro and (not comp_origem or not num_nota or not fornecedor): 
                st.error("Preencha todos os dados retroativos.")
            else:
                if op_row['tipo'] == 'RETENÇÃO': 
                    vp, vc = v_pis_ret, v_cof_ret
                else: 
                    vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
                
                st.session_state.rascunho_lancamentos.append({
                    "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, 
                    "v_base": v_base, "v_pis": vp, "v_cofins": vc, "hist": hist, 
                    "retro": retro, "origem": comp_origem if retro else None,
                    "nota": num_nota, "fornecedor": fornecedor
                })
                st.session_state.form_key += 1
                st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        with st.container(height=390, border=True): 
            if not st.session_state.rascunho_lancamentos: 
                st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    retro_badge = f" <span style='color:red;font-size:10px;'>(EXTEMP: {it['origem']})</span>" if it['retro'] else ""
                    c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}<br>PIS: {formatar_moeda(it['v_pis'])} | COF: {formatar_moeda(it['v_cofins'])}</small>", unsafe_allow_html=True)
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base'])}</span>", unsafe_allow_html=True)
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
                    query = """INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)"""
                    cursor.execute(query, (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['hist'], st.session_state.username, it['retro'], it['origem'], it['nota'], it['fornecedor']))
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
                
                # QUERY ATUALIZADA: Puxando os novos campos independentes de histórico
                query = f"""SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, 
                            o.conta_deb_pis, o.conta_cred_pis, o.pis_h_codigo, o.pis_h_texto,
                            o.conta_deb_cof, o.conta_cred_cof, o.cofins_h_codigo, o.cofins_h_texto,
                            o.conta_deb_custo, o.conta_cred_custo, o.custo_h_codigo, o.custo_h_texto
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
                        
                        # Acumular para transferência
                        if row['op_tipo'] == 'DESPESA': 
                            total_pis_rec += row['valor_pis']
                            total_cof_rec += row['valor_cofins']
                        
                        # Linha PIS (Agora usa o texto e código independente do PIS)
                        if pd.notnull(row['conta_deb_pis']) and pd.notnull(row['conta_cred_pis']):
                            t_hist_pis = processar_texto(row.get('pis_h_texto'), row['op_nome'])
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_pis']).replace('.', ''), "Credito": str(row['conta_cred_pis']).replace('.', ''), "Data": data_str, "Valor": row['valor_pis'], "Cod. Historico": row.get('pis_h_codigo', ''), "Historico": f"PIS - {t_hist_pis}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                        
                        # Linha COFINS (Usa o texto e código independente da COFINS)
                        if pd.notnull(row['conta_deb_cof']) and pd.notnull(row['conta_cred_cof']):
                            t_hist_cof = processar_texto(row.get('cofins_h_texto'), row['op_nome'])
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_cof']).replace('.', ''), "Credito": str(row['conta_cred_cof']).replace('.', ''), "Data": data_str, "Valor": row['valor_cofins'], "Cod. Historico": row.get('cofins_h_codigo', ''), "Historico": f"COF - {t_hist_cof}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                        
                        # Linha Custo Líquido (Usa o texto e código independente do Custo)
                        if row['op_tipo'] != 'RETENÇÃO' and pd.notnull(row['conta_deb_custo']) and pd.notnull(row['conta_cred_custo']):
                            t_hist_custo = processar_texto(row.get('custo_h_texto'), row['op_nome'])
                            v_custo = row['valor_base'] - row['valor_pis'] - row['valor_cofins']
                            linhas_excel.append({"Lancto Aut.": "", "Debito": str(row['conta_deb_custo']).replace('.', ''), "Credito": str(row['conta_cred_custo']).replace('.', ''), "Data": data_str, "Valor": v_custo, "Cod. Historico": row.get('custo_h_codigo', ''), "Historico": f"CUSTO LIQ - {t_hist_custo}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": row['num_nota'] or row['id'], "Complemento": ""})
                    
                    # Linhas de Transferência Automática
                    if total_pis_rec > 0 and emp_row['conta_transf_pis']:
                        linhas_excel.append({"Lancto Aut.": "", "Debito": str(emp_row['conta_transf_pis']).replace('.', ''), "Credito": "", "Data": data_str, "Valor": total_pis_rec, "Cod. Historico": "", "Historico": f"Vr. transferido para apuração do PIS n/ mês {competencia}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": "", "Complemento": ""})
                    if total_cof_rec > 0 and emp_row['conta_transf_cofins']:
                        linhas_excel.append({"Lancto Aut.": "", "Debito": str(emp_row['conta_transf_cofins']).replace('.', ''), "Credito": "", "Data": data_str, "Valor": total_cof_rec, "Cod. Historico": "", "Historico": f"Vr. transferido para apuração da COFINS n/ mês {competencia}", "Ccusto Debito": "", "Ccusto Credito": "", "Nr.Documento": "", "Complemento": ""})

                    df_xlsx = pd.DataFrame(linhas_excel)
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer: 
                        df_xlsx.to_excel(writer, index=False, sheet_name='Lançamentos')
                    buffer.seek(0)
                    
                    # PDF REPORT (Lógica original 100% mantida)
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Arial", 'B', 12)
                    pdf.cell(190, 8, "DEMONSTRATIVO DE APURACAO - PIS E COFINS", ln=True, align='C')
                    pdf.ln(3)
                    
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(25, 6, "Competencia:")
                    pdf.set_font("Arial", '', 9)
                    pdf.cell(165, 6, f"{competencia}", ln=True)
                    
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(25, 6, "Razao Social:")
                    pdf.set_font("Arial", '', 9)
                    pdf.cell(105, 6, f"{emp_row['nome']}")
                    
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(15, 6, "CNPJ:")
                    pdf.set_font("Arial", '', 9)
                    pdf.cell(45, 6, f"{emp_row['cnpj']}", ln=True)
                    
                    pdf.ln(5)
                    
                    # 1. DÉBITOS
                    pdf.set_font("Arial", 'B', 10)
                    pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True)
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(90, 6, "Operacao", 1)
                    pdf.cell(35, 6, "Base", 1)
                    pdf.cell(30, 6, "PIS", 1)
                    pdf.cell(35, 6, "COFINS", 1, ln=True)
                    
                    pdf.set_font("Arial", '', 9)
                    deb_pis = deb_cof = cred_pis = cred_cof = ret_pis = ret_cof = 0
                    for _, r in df_export[df_export['op_tipo'] == 'RECEITA'].iterrows():
                        pdf.cell(90, 6, f"{r['op_nome']}"[:50], 1)
                        pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1)
                        pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1)
                        pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                        deb_pis += r['valor_pis']
                        deb_cof += r['valor_cofins']
                    
                    # 2. CRÉDITOS
                    pdf.ln(5)
                    pdf.set_font("Arial", 'B', 10)
                    pdf.cell(190, 8, "2. BASE DE CALCULO DOS INSUMOS E CREDITOS", ln=True)
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(90, 6, "Operacao", 1)
                    pdf.cell(35, 6, "Base", 1)
                    pdf.cell(30, 6, "PIS", 1)
                    pdf.cell(35, 6, "COFINS", 1, ln=True)
                    
                    pdf.set_font("Arial", '', 9)
                    for _, r in df_export[df_export['op_tipo'] == 'DESPESA'].iterrows():
                        pdf.cell(90, 6, f"{r['op_nome']}"[:50], 1)
                        pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1)
                        pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1)
                        pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                        cred_pis += r['valor_pis']
                        cred_cof += r['valor_cofins']

                    # 3. RETENÇÕES
                    pdf.ln(5)
                    pdf.set_font("Arial", 'B', 10)
                    pdf.cell(190, 8, "3. RETENCOES NA FONTE", ln=True)
                    pdf.set_font("Arial", 'B', 9)
                    pdf.cell(125, 6, "Operacao Retida", 1)
                    pdf.cell(30, 6, "PIS Retido", 1)
                    pdf.cell(35, 6, "COF Retida", 1, ln=True)
                    
                    pdf.set_font("Arial", '', 9)
                    for _, r in df_export[df_export['op_tipo'] == 'RETENÇÃO'].iterrows():
                        pdf.cell(125, 6, f"{r['op_nome']}"[:70], 1)
                        pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1)
                        pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                        ret_pis += r['valor_pis']
                        ret_cof += r['valor_cofins']

                    # 4. APURAÇÃO FINAL
                    pdf.ln(10)
                    pdf.set_font("Arial", 'B', 10)
                    pdf.cell(190, 8, "4. QUADRO DE APURACAO FINAL", ln=True)
                    
                    pdf.set_font("Arial", '', 10)
                    res_pis = deb_pis - cred_pis - ret_pis
                    res_cof = deb_cof - cred_cof - ret_cof
                    
                    pdf.cell(120, 6, "Total Imposto a Recolher:", 0)
                    pdf.cell(35, 6, formatar_moeda(max(0, res_pis)), 0)
                    pdf.cell(35, 6, formatar_moeda(max(0, res_cof)), 0, ln=True)
                    
                    pdf.cell(120, 6, "Saldo Credor para o Mes Seguinte:", 0)
                    pdf.cell(35, 6, formatar_moeda(abs(min(0, res_pis))), 0)
                    pdf.cell(35, 6, formatar_moeda(abs(min(0, res_cof))), 0, ln=True)

                    pdf_bytes = pdf.output(dest='S').encode('latin1')
                    
                    st.success("Ficheiros processados com sucesso!")
                    c_btn1, c_btn2, _ = st.columns([1, 1, 2])
                    c_btn1.download_button("⬇️ XLSX (ERP - 6 Contas e Transferência)", data=buffer, file_name=f"LCTOS_{comp_db}.xlsx")
                    c_btn2.download_button("⬇️ PDF (Conferência Analítica)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
            except Exception as e: 
                st.error(f"Erro na geração: {e}")
            finally: 
                conn.close()

# --- 8. MÓDULO PARÂMETROS CONTÁBEIS ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": 
        st.error("Acesso restrito.")
        return
        
    st.markdown("### ⚙️ Parâmetros Contábeis e Integração ERP")
    
    df_op = carregar_operacoes()
    op_nomes = df_op['nome'].tolist()
    
    tab_edit, tab_novo, tab_fecho = st.tabs(["✏️ Editar Existente", "➕ Nova Operação", "🏢 Fecho por Empresa"])
    
    with tab_edit:
        with st.form("form_edit_param"):
            sel_op = st.selectbox("Selecione a Operação:", op_nomes)
            row_op = df_op[df_op['nome'] == sel_op].iloc[0]
            
            st.markdown("##### Configuração PIS")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
            p_deb = c1.text_input("Débito PIS", value=row_op['conta_deb_pis'] if pd.notnull(row_op['conta_deb_pis']) else "")
            p_cred = c2.text_input("Crédito PIS", value=row_op['conta_cred_pis'] if pd.notnull(row_op['conta_cred_pis']) else "")
            p_cod = c3.text_input("Cód ERP PIS", value=row_op.get('pis_h_codigo', ''))
            p_txt = c4.text_input("Texto Padrão PIS", value=row_op.get('pis_h_texto', ''))
            
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2])
            c_deb = c5.text_input("Débito COFINS", value=row_op['conta_deb_cof'] if pd.notnull(row_op['conta_deb_cof']) else "")
            c_cred = c6.text_input("Crédito COFINS", value=row_op['conta_cred_cof'] if pd.notnull(row_op['conta_cred_cof']) else "")
            c_cod = c7.text_input("Cód ERP COFINS", value=row_op.get('cofins_h_codigo', ''))
            c_txt = c8.text_input("Texto Padrão COF", value=row_op.get('cofins_h_texto', ''))
            
            st.markdown("##### Configuração CUSTO/VALOR LÍQUIDO (Opcional)")
            c9, c10, c11, c12 = st.columns([1, 1, 1, 2])
            cu_deb = c9.text_input("Débito Custo", value=row_op['conta_deb_custo'] if pd.notnull(row_op['conta_deb_custo']) else "")
            cu_cred = c10.text_input("Crédito Custo", value=row_op['conta_cred_custo'] if pd.notnull(row_op['conta_cred_custo']) else "")
            cu_cod = c11.text_input("Cód ERP Custo", value=row_op.get('custo_h_codigo', ''))
            cu_txt = c12.text_input("Texto Padrão Custo", value=row_op.get('custo_h_texto', ''))
            
            if st.form_submit_button("Atualizar Operação"):
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        UPDATE operacoes 
                        SET conta_deb_pis=%s, conta_cred_pis=%s, pis_h_codigo=%s, pis_h_texto=%s, 
                            conta_deb_cof=%s, conta_cred_cof=%s, cofins_h_codigo=%s, cofins_h_texto=%s, 
                            conta_deb_custo=%s, conta_cred_custo=%s, custo_h_codigo=%s, custo_h_texto=%s 
                        WHERE id=%s
                    """, (p_deb, p_cred, p_cod, p_txt, c_deb, c_cred, c_cod, c_txt, cu_deb, cu_cred, cu_cod, cu_txt, row_op['id']))
                    conn.commit()
                    carregar_operacoes.clear()
                    st.success("Atualizado!")
                    st.rerun()
                except Exception as e: 
                    conn.rollback()
                    st.error(f"Erro: {e}")
                finally: 
                    conn.close()
                    
    with tab_novo:
        with st.form("form_nova_op"):
            c_nome, c_tipo = st.columns([3, 1])
            novo_nome = c_nome.text_input("Nome da Nova Operação")
            novo_tipo = c_tipo.selectbox("Natureza", ["RECEITA", "DESPESA", "RETENÇÃO"])
            
            st.markdown("##### Configuração PIS")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
            nn_p_deb = c1.text_input("Débito PIS ")
            nn_p_cred = c2.text_input("Crédito PIS ")
            nn_p_cod = c3.text_input("Cód ERP PIS ")
            nn_p_txt = c4.text_input("Texto Padrão PIS ")
            
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2])
            nn_c_deb = c5.text_input("Débito COFINS ")
            nn_c_cred = c6.text_input("Crédito COFINS ")
            nn_c_cod = c7.text_input("Cód ERP COFINS ")
            nn_c_txt = c8.text_input("Texto Padrão COF ")
            
            st.markdown("##### Configuração CUSTO/VALOR LÍQUIDO")
            c9, c10, c11, c12 = st.columns([1, 1, 1, 2])
            nn_cu_deb = c9.text_input("Débito Custo ")
            nn_cu_cred = c10.text_input("Crédito Custo ")
            nn_cu_cod = c11.text_input("Cód ERP Custo ")
            nn_cu_txt = c12.text_input("Texto Padrão Custo ")
            
            if st.form_submit_button("Registar Nova Operação"):
                if not novo_nome: 
                    st.error("O nome é obrigatório.")
                else:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute("""
                            INSERT INTO operacoes (
                                nome, tipo, 
                                conta_deb_pis, conta_cred_pis, pis_h_codigo, pis_h_texto,
                                conta_deb_cof, conta_cred_cof, cofins_h_codigo, cofins_h_texto,
                                conta_deb_custo, conta_cred_custo, custo_h_codigo, custo_h_texto
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """, (novo_nome, novo_tipo, nn_p_deb, nn_p_cred, nn_p_cod, nn_p_txt, nn_c_deb, nn_c_cred, nn_c_cod, nn_c_txt, nn_cu_deb, nn_cu_cred, nn_cu_cod, nn_cu_txt))
                        conn.commit()
                        carregar_operacoes.clear()
                        st.success("Registado!")
                        st.rerun()
                    except Exception as e: 
                        conn.rollback()
                        st.error(f"Erro: {e}")
                    finally: 
                        conn.close()

    with tab_fecho:
        st.markdown("##### Contas de Transferência / Fecho (Apuração Mensal)")
        df_emp = carregar_empresas_ativas()
        if not df_emp.empty:
            with st.form("form_fecho"):
                emp_sel_fecho = st.selectbox("Selecione a Empresa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
                emp_id_fecho = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel_fecho].iloc[0]['id'])
                row_emp = df_emp[df_emp['id'] == emp_id_fecho].iloc[0]

                c1, c2 = st.columns(2)
                t_pis = c1.text_input("Conta Transferência PIS", value=row_emp.get('conta_transf_pis', ''))
                t_cofins = c2.text_input("Conta Transferência COFINS", value=row_emp.get('conta_transf_cofins', ''))

                if st.form_submit_button("Salvar Contas de Fecho"):
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute("UPDATE empresas SET conta_transf_pis=%s, conta_transf_cofins=%s WHERE id=%s", (t_pis, t_cofins, emp_id_fecho))
                        conn.commit()
                        carregar_empresas_ativas.clear()
                        st.success("Contas de fecho atualizadas para esta empresa!")
                        st.rerun()
                    except Exception as e:
                        conn.rollback()
                        st.error(f"Erro: {e}")
                    finally:
                        conn.close()

# --- 9. NOVO MÓDULO: GESTÃO DE UTILIZADORES ---
def modulo_usuarios():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": 
        st.error("Acesso restrito.")
        return
        
    st.markdown("### 👥 Gestão de Utilizadores e Acessos")
    df_emp = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso != "SUPER_ADMIN": 
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

    tab_novo, tab_lista = st.tabs(["➕ Novo Utilizador", "Equipa Registada"])
    
    with tab_novo:
        with st.form("form_novo_user"):
            c_emp, c_nivel = st.columns([2, 1])
            
            if st.session_state.nivel_acesso == "SUPER_ADMIN":
                emp_sel = c_emp.selectbox("Vincular à Empresa:", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
                emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
            else:
                c_emp.text_input("Vincular à Empresa:", value=df_emp.iloc[0]['nome'], disabled=True)
                emp_id = st.session_state.empresa_id

            nivel = c_nivel.selectbox("Perfil de Acesso", ["CLIENT_OPERATOR", "CLIENT_ADMIN"])
            
            c_nome, c_user, c_pass = st.columns([2, 1.5, 1.5])
            nome_user = c_nome.text_input("Nome Completo")
            login_user = c_user.text_input("Login")
            senha_user = c_pass.text_input("Palavra-passe", type="password")

            if st.form_submit_button("Criar Acesso"):
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    hash_senha = bcrypt.hashpw(senha_user.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    cursor.execute("INSERT INTO usuarios (nome, username, senha_hash, nivel_acesso, empresa_id, status_usuario) VALUES (%s, %s, %s, %s, %s, 'ATIVO')", (nome_user, login_user, hash_senha, nivel, emp_id))
                    conn.commit()
                    st.success("Utilizador criado!")
                    st.rerun()
                except Exception as e: 
                    conn.rollback()
                    st.error(f"Erro: {e}")
                finally: 
                    conn.close()

    with tab_lista:
        conn = get_db_connection()
        query = "SELECT u.nome, u.username, u.nivel_acesso, e.nome as empresa FROM usuarios u LEFT JOIN empresas e ON u.empresa_id = e.id"
        if st.session_state.nivel_acesso != "SUPER_ADMIN": 
            query += f" WHERE u.empresa_id = {st.session_state.empresa_id}"
        
        st.dataframe(pd.read_sql(query, conn), use_container_width=True, hide_index=True)
        conn.close()

# --- 10. MENU LATERAL ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis", "👥 Gestão de Utilizadores"])
    st.write("---")
    st.link_button("🔗 Auditoria de Vendas", "https://conciliador-contabil-hsppms6xpbjstvmmfktgkc.streamlit.app/", use_container_width=True)
    st.write("---")
    if st.button("🚪 Encerrar Sessão", use_container_width=True): 
        st.session_state.autenticado = False
        st.rerun()

# --- 11. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
elif menu == "👥 Gestão de Utilizadores": modulo_usuarios()
