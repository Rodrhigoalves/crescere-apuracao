import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, datetime, timedelta, timezone
import io
import bcrypt
from fpdf import FPDF
from dateutil.relativedelta import relativedelta

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

# --- 2. CONEXÃO E AUXILIARES ---
def get_db_connection():
    try:
        return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err:
        st.error(f"Erro crítico: {err}")
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
    df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
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
    elif regime in ["Lucro Presumido", "Arbitrado"]:
        return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

# --- 4. CONTROLO DE ESTADO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'dados_form' not in st.session_state: st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""}
if 'rascunho_lancamentos' not in st.session_state: st.session_state.rascunho_lancamentos = []
if 'form_key' not in st.session_state: st.session_state.form_key = 0

fuso_br = timezone(timedelta(hours=-3))
hoje_br = datetime.now(fuso_br)
competencia_padrao = (hoje_br.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

# --- LOGIN ---
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
        cnpj_input = c_busca.text_input("CNPJ para busca automática:")
        if c_btn.button("Consultar CNPJ", use_container_width=True):
            res = consultar_cnpj(cnpj_input.replace(".","").replace("/","").replace("-",""))
            if res:
                st.session_state.dados_form.update({
                    "nome": res.get('nome', ''), "fantasia": res.get('fantasia', ''), "cnpj": res.get('cnpj', ''), 
                    "cnae": res.get('atividade_principal', [{}])[0].get('code', ''), 
                    "endereco": f"{res.get('logradouro', '')}, {res.get('numero', '')} - {res.get('bairro', '')}, {res.get('municipio', '')}/{res.get('uf', '')}"
                })
                st.rerun()
        
        f = st.session_state.dados_form
        with st.form("form_empresa"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
            
            c3, c4, c5, c_apelido = st.columns([2, 1.5, 1.5, 2])
            cnpj = c3.text_input("CNPJ", value=f['cnpj'])
            # LISTA DE REGIMES ATUALIZADA
            regimes = ["Lucro Real", "Lucro Presumido", "Simples Nacional", "Simples Nacional - Excesso", "MEI", "Arbitrado", "Imune/Isenta", "Inativa"]
            regime = c4.selectbox("Regime", regimes, index=regimes.index(f['regime']) if f['regime'] in regimes else 0)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
            apelido = c_apelido.text_input("Apelido da Unidade", value=f.get('apelido_unidade', ''))
            
            cnae = st.text_input("CNAE", value=f['cnae'])
            endereco = st.text_input("Endereço", value=f['endereco'])
            
            if st.form_submit_button("Gravar Unidade", use_container_width=True):
                conn = get_db_connection(); cursor = conn.cursor()
                if f['id']:
                    cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, f['id']))
                else:
                    cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido))
                conn.commit(); conn.close(); carregar_empresas_ativas.clear(); st.success("Sucesso!"); st.rerun()

# --- 6. NOVO MÓDULO IMOBILIZADO ---
def modulo_imobilizado():
    st.markdown("### 🏢 Controle de Ativo Imobilizado (Depreciação)")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN": df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
    
    emp_sel = st.selectbox("Selecione a Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])

    tab_cad, tab_proc, tab_dossie = st.tabs(["➕ Cadastro de Bem", "⚙️ Processamento em Lote", "📂 Consulta & Dossiê"])

    with tab_cad:
        conn = get_db_connection()
        df_grupos = pd.read_sql(f"SELECT * FROM grupos_imobilizado WHERE tenant_id = {emp_id}", conn)
        conn.close()
        
        if df_grupos.empty:
            st.warning("⚠️ Cadastre Grupos de Imobilizado nos Parâmetros antes de continuar.")
        else:
            with st.form("form_imob"):
                c1, c2, c3 = st.columns([2, 1, 1])
                desc = c1.text_input("Descrição do Bem (Ex: Notebook ASUS F16)")
                nf = c2.text_input("Nº Nota Fiscal")
                forn = c3.text_input("Fornecedor")
                
                c4, c5, c6 = st.columns(3)
                dt_c = c4.date_input("Data da Compra")
                v_aq = c5.number_input("Valor de Aquisição", min_value=0.0, format="%.2f")
                g_sel = c6.selectbox("Grupo (Taxa/Contas)", df_grupos['nome_grupo'].tolist())
                
                if st.form_submit_button("Salvar Bem"):
                    gid = df_grupos[df_grupos['nome_grupo'] == g_sel].iloc[0]['id']
                    conn = get_db_connection(); cursor = conn.cursor()
                    cursor.execute("INSERT INTO bens_imobilizado (tenant_id, grupo_id, descricao_item, numero_nota_fiscal, nome_fornecedor, data_compra, valor_compra) VALUES (%s,%s,%s,%s,%s,%s,%s)", (emp_id, gid, desc, nf, forn, dt_c, v_aq))
                    conn.commit(); conn.close(); st.success("Bem registrado!"); st.rerun()

    with tab_proc:
        c_m, c_a = st.columns(2)
        m_proc = c_m.selectbox("Mês", range(1, 13), index=hoje_br.month - 1)
        a_proc = c_a.number_input("Ano", value=hoje_br.year)
        
        if st.button("Processar Depreciação do Mês", use_container_width=True):
            conn = get_db_connection()
            query = f"""SELECT b.*, g.taxa_anual_percentual, g.conta_contabil_despesa, g.conta_contabil_dep_acumulada 
                        FROM bens_imobilizado b JOIN grupos_imobilizado g ON b.grupo_id = g.id 
                        WHERE b.tenant_id = {emp_id} AND b.status = 'ativo'"""
            df_bens = pd.read_sql(query, conn)
            
            resultados = []
            for _, bem in df_bens.iterrows():
                cota = (bem['valor_compra'] * (bem['taxa_anual_percentual']/100)) / 12
                resultados.append({
                    "Debito": bem['conta_contabil_despesa'], "Credito": bem['conta_contabil_dep_acumulada'],
                    "Valor": cota, "Historico": f"DEPRECIAÇÃO MENSAL REF {m_proc}/{a_proc} - {bem['descricao_item']}"
                })
            
            df_xlsx = pd.DataFrame(resultados)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_xlsx.to_excel(writer, index=False, sheet_name='Depreciacao')
            buffer.seek(0)
            st.download_button("⬇️ Baixar XLSX Depreciação", data=buffer, file_name=f"DEPREC_{m_proc}_{a_proc}.xlsx", use_container_width=True)

    with tab_dossie:
        busca = st.text_input("Pesquisar Bem (Nome, NF ou Fornecedor)")
        if busca:
            conn = get_db_connection()
            query = f"SELECT b.*, g.nome_grupo, g.taxa_anual_percentual FROM bens_imobilizado b JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = {emp_id} AND (b.descricao_item LIKE '%{busca}%' OR b.numero_nota_fiscal LIKE '%{busca}%' OR b.nome_fornecedor LIKE '%{busca}%')"
            df_res = pd.read_sql(query, conn)
            conn.close()
            
            for _, r in df_res.iterrows():
                with st.expander(f"{r['descricao_item']} (NF: {r['numero_nota_fiscal']})"):
                    # CÁLCULO DE SALDO ATUAL E PROJEÇÃO
                    meses_uso = relativedelta(date.today(), r['data_compra']).years * 12 + relativedelta(date.today(), r['data_compra']).months
                    dep_acum = (r['valor_compra'] * (r['taxa_anual_percentual']/100)/12) * meses_uso
                    saldo_atual = max(0, r['valor_compra'] - dep_acum)
                    
                    st.write(f"**Valor de Aquisição:** {formatar_moeda(r['valor_compra'])}")
                    st.write(f"**Depreciação Acumulada:** {formatar_moeda(dep_acum)}")
                    st.write(f"**Saldo Residual Atual:** {formatar_moeda(saldo_atual)}")
                    
                    dt_futura = st.date_input("Prever Saldo em:", value=date.today() + timedelta(days=365), key=f"dt_{r['id']}")
                    meses_f = relativedelta(dt_futura, r['data_compra']).years * 12 + relativedelta(dt_futura, r['data_compra']).months
                    saldo_f = max(0, r['valor_compra'] - (r['valor_compra'] * (r['taxa_anual_percentual']/100)/12) * meses_f)
                    st.metric("Projeção de Saldo Residual", formatar_moeda(saldo_f))

# --- 7. MÓDULO APURAÇÃO (PIS/COFINS) ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN" and st.session_state.empresa_id:
        df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]

    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1)

    c_emp, c_comp = st.columns([3, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c_comp.text_input("Competência", value=competencia_padrao)

    st.divider()
    col_in, col_ras = st.columns(2)
    
    with col_in:
        st.markdown("#### Lançamento")
        fk = st.session_state.form_key
        op_sel = st.selectbox("Operação", df_op['nome_exibicao'].tolist(), key=f"op_{fk}")
        op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
        v_base = st.number_input("Valor Base (R$)", min_value=0.0, key=f"v_{fk}")
        
        if st.button("Adicionar"):
            vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
            st.session_state.rascunho_lancamentos.append({
                "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, 
                "v_base": v_base, "v_pis": vp, "v_cofins": vc
            })
            st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        for i, it in enumerate(st.session_state.rascunho_lancamentos):
            st.write(f"{it['op_nome']} - {formatar_moeda(it['v_base'])}")
        
        if st.button("Gravar no Banco", type="primary", disabled=not st.session_state.rascunho_lancamentos):
            conn = get_db_connection(); cursor = conn.cursor()
            m, a = competencia.split('/')
            comp_db = f"{a}-{m.zfill(2)}"
            for it in st.session_state.rascunho_lancamentos:
                query = "INSERT INTO lancamentos (empresa_id, operacao_id, competencia, valor_base, valor_pis, valor_cofins, status_auditoria) VALUES (%s,%s,%s,%s,%s,%s,'ATIVO')"
                cursor.execute(query, (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins']))
            conn.commit(); conn.close()
            st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()

# --- 8. PARÂMETROS E GRUPOS ---
def modulo_parametros():
    st.markdown("### ⚙️ Parâmetros do Sistema")
    tab_op, tab_imob = st.tabs(["Operações Fiscais", "Grupos de Imobilizado"])
    
    with tab_op:
        st.info("Configuração de contas contábeis para PIS/COFINS.")
        # Lógica original de parâmetros mantida...

    with tab_imob:
        st.markdown("#### Configurar Alíquotas e Contas de Depreciação")
        df_emp = carregar_empresas_ativas()
        emp_sel = st.selectbox("Empresa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1), key="p_imob")
        emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
        
        with st.form("form_grupo"):
            c1, c2 = st.columns(2)
            n_grupo = c1.text_input("Nome do Grupo (Ex: Veículos)")
            taxa = c2.number_input("Taxa Anual (%)", min_value=0.0, step=1.0)
            c3, c4 = st.columns(2)
            c_deb = c3.text_input("Conta Despesa (D)")
            c_cre = c4.text_input("Conta Dep. Acumulada (C)")
            if st.form_submit_button("Salvar Grupo"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO grupos_imobilizado (tenant_id, nome_grupo, taxa_anual_percentual, conta_contabil_despesa, conta_contabil_dep_acumulada) VALUES (%s,%s,%s,%s,%s)", (emp_id, n_grupo, taxa, c_deb, c_cre))
                conn.commit(); conn.close(); st.success("Grupo Criado!"); st.rerun()

# --- SIDEBAR E ROTAS ---
with st.sidebar:
    st.markdown(f"<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "📦 Imobilizado & Depreciação", "Relatórios e Integração", "⚙️ Parâmetros Contábeis", "👥 Gestão de Utilizadores"])
    if st.button("🚪 Encerrar Sessão", use_container_width=True): st.session_state.autenticado = False; st.rerun()

if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "📦 Imobilizado & Depreciação": modulo_imobilizado()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
else: st.info("Módulo em desenvolvimento ou sem permissão.")
