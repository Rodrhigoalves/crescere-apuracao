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
import time

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
        "Cod. Historico": cod_hist if cod_hist else "",
        "Historico": hist,
        "Ccusto Debito": "",
        "Ccusto Credito": "",
        "Nr.Documento": nr_doc if nr_doc else "",
        "Complemento": ""
    }

# --- 2. CONEXÃO E CACHE ---
def get_db_connection():
    try: return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err: st.error(f"Erro crítico: {err}"); st.stop()

@st.cache_data(ttl=300)
def carregar_operacoes():
    conn = get_db_connection(); df = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn); conn.close(); return df

@st.cache_data(ttl=300)
def carregar_empresas_ativas():
    conn = get_db_connection(); df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO'", conn); conn.close(); return df

def verificar_senha(senha_plana, hash_banco): return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))
def gerar_hash_senha(senha_plana): return bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
def formatar_moeda(valor): return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
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

# LOGICA DE LOGIN E CARREGAMENTO DE EMPRESAS PERMITIDAS
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
                
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.autenticado = True
                    st.session_state.usuario_id = user_data['id']
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']
                    st.session_state.nivel_acesso = user_data['nivel_acesso']
                    
                    # Carrega as empresas vinculadas ao usuário
                    cursor.execute("SELECT empresa_id FROM usuario_empresas WHERE usuario_id = %s", (user_data['id'],))
                    st.session_state.empresas_permitidas = [r['empresa_id'] for r in cursor.fetchall()]
                    conn.close()
                    st.rerun()
                else: 
                    if conn.is_connected(): conn.close()
                    st.error("Credenciais inválidas.")
    st.stop()

# GARANTIA DE SESSÃO: Se já logado, mas sem as permissões na memória (refresh), busca agora
if st.session_state.autenticado and 'empresas_permitidas' not in st.session_state:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT empresa_id FROM usuario_empresas WHERE usuario_id = %s", (st.session_state.usuario_id,))
    st.session_state.empresas_permitidas = [r['empresa_id'] for r in cursor.fetchall()]
    conn.close()

# --- 5. MÓDULO GESTÃO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("### Gestão de Empresas e Unidades")
    df_todas = carregar_empresas_ativas()
    
    # Hierarquia: Super vê todas, Operador só as vinculadas
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        df_emp = df_todas
    else:
        df_emp = df_todas[df_todas['id'].isin(st.session_state.empresas_permitidas)]
    
    tab_cad, tab_lista = st.tabs(["Novo Registo", "Unidades Registadas"])
    with tab_cad:
        if st.session_state.nivel_acesso == "CLIENT_OPERATOR":
            st.warning("Apenas administradores podem cadastrar novas empresas.")
        else:
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
                regime = c4.selectbox("Regime", lista_regimes, index=lista_regimes.index(f.get('regime')) if f.get('regime') in lista_regimes else 0)
                tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f.get('tipo') == "Matriz" else 1)
                apelido = c_apelido.text_input("Apelido da Unidade", value=f.get('apelido_unidade', ''))
                c6, c7 = st.columns([1, 3])
                cnae = c6.text_input("CNAE", value=f['cnae'])
                endereco = c7.text_input("Endereço", value=f['endereco'])
                if st.form_submit_button("Gravar Unidade"):
                    if not nome or not cnpj: st.error("Razão Social e CNPJ são obrigatórios.")
                    else:
                        conn = get_db_connection(); cursor = conn.cursor()
                        try:
                            if f['id']: cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, int(f['id'])))
                            else: cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido))
                            conn.commit(); carregar_empresas_ativas.clear(); st.success("Gravado!"); time.sleep(1); st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""}; st.rerun()
                        except Exception as e: conn.rollback(); st.error(f"Erro: {e}")
                        finally: conn.close()
    
    with tab_lista:
        if df_emp.empty: st.info("Nenhuma empresa disponível.")
        else:
            for _, row in df_emp.iterrows():
                col_info, col_btn = st.columns([5, 1])
                col_info.markdown(f"**{row['nome']}** ({row['apelido_unidade'] or row['tipo']})<br><small>CNPJ: {row['cnpj']}</small>", unsafe_allow_html=True)
                if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                    st.session_state.dados_form = row.to_dict(); st.rerun()
                st.divider()

# --- 6. MÓDULO APURAÇÃO ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")
    df_emp_all = carregar_empresas_ativas()
    
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        df_emp = df_emp_all
    else:
        df_emp = df_emp_all[df_emp_all['id'].isin(st.session_state.empresas_permitidas)]
        if df_emp.empty: st.error("Você não tem acesso a unidades."); st.stop()

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
        v_base = st.number_input("Base (R$)", min_value=0.00, step=100.0, key=f"base_{fk}")
        
        c_retro, c_origem = st.columns([1, 1])
        retro = c_retro.checkbox("Lançamento Extemporâneo", key=f"retro_{fk}")
        comp_origem = c_origem.text_input("Mês Origem", disabled=not retro, key=f"origem_{fk}")
        hist = st.text_input("Histórico", key=f"hist_{fk}")
        
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if v_base <= 0: st.warning("Informe o valor.")
            else:
                vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
                st.session_state.rascunho_lancamentos.append({
                    "id_unico": str(datetime.now().timestamp()), "emp_id": int(emp_id), "op_id": int(op_row['id']), 
                    "op_nome": op_sel, "v_base": float(v_base), "v_pis": float(vp), "v_cofins": float(vc), 
                    "hist": hist, "retro": int(retro), "origem": comp_origem if retro else None
                })
                st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        def remover_item(idx): st.session_state.rascunho_lancamentos.pop(idx)
        with st.container(height=390, border=True):
            for i, it in enumerate(st.session_state.rascunho_lancamentos):
                c_txt, c_del = st.columns([8, 1])
                c_txt.write(f"**{it['op_nome']}** - {formatar_moeda(it['v_base'])}")
                c_del.button("×", key=f"del_{it['id_unico']}", on_click=remover_item, args=(i,))
        
        if st.button("Gravar no Banco", type="primary", use_container_width=True, disabled=not st.session_state.rascunho_lancamentos):
            conn = get_db_connection(); cursor = conn.cursor()
            try:
                m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
                for it in st.session_state.rascunho_lancamentos:
                    query = "INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO')"
                    cursor.execute(query, (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['hist'], st.session_state.username))
                conn.commit(); st.session_state.rascunho_lancamentos = []; st.toast("Gravado!"); time.sleep(1); st.rerun()
            except Exception as e: st.error(f"Erro: {e}")
            finally: conn.close()

# --- 7. MÓDULO RELATÓRIOS ---
def modulo_relatorios():
    st.markdown("### Relatórios e Exportação")
    df_emp_all = carregar_empresas_ativas()
    if st.session_state.nivel_acesso == "SUPER_ADMIN": df_emp = df_emp_all
    else: df_emp = df_emp_all[df_emp_all['id'].isin(st.session_state.empresas_permitidas)]
    
    if df_emp.empty: st.stop()
    
    c1, c2 = st.columns([2, 1])
    emp_sel = c1.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    comp = c2.text_input("Competência", value=competencia_padrao)
    
    if st.button("Analisar Dados"):
        st.info("Funcionalidade de geração em processamento...")

# --- 8. MÓDULO IMOBILIZADO ---
def modulo_imobilizado():
    st.markdown("### Imobilizado & Depreciação")
    df_emp_all = carregar_empresas_ativas()
    if st.session_state.nivel_acesso == "SUPER_ADMIN": df_emp = df_emp_all
    else: df_emp = df_emp_all[df_emp_all['id'].isin(st.session_state.empresas_permitidas)]
    
    if df_emp.empty: st.stop()
    st.write("Módulo pronto para cadastro de bens.")

# --- 9. MÓDULO PARÂMETROS ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": st.error("Acesso negado."); return
    st.markdown("### Parâmetros Contábeis")
    st.write("Configure aqui as contas do ERP.")

# --- 10. GESTÃO DE UTILIZADORES ---
def modulo_usuarios():
    if st.session_state.nivel_acesso != "SUPER_ADMIN": st.error("Acesso restrito."); return
    st.markdown("### Gestão de Utilizadores")
    conn = get_db_connection()
    df_users = pd.read_sql("SELECT id, nome, username, nivel_acesso, status_usuario FROM usuarios ORDER BY nome ASC", conn)
    df_emp_list = pd.read_sql("SELECT id, nome FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
    
    tab_lista, tab_novo = st.tabs(["Lista", "Novo"])
    with tab_lista:
        st.dataframe(df_users, use_container_width=True, hide_index=True)
        with st.form("edit_u"):
            u_sel = st.selectbox("Utilizador", df_users['username'].tolist())
            u_id = int(df_users[df_users['username'] == u_sel].iloc[0]['id'])
            
            cursor = conn.cursor()
            cursor.execute("SELECT empresa_id FROM usuario_empresas WHERE usuario_id = %s", (u_id,))
            atuais = [r[0] for r in cursor.fetchall()]
            
            novas_emps = st.multiselect("Unidades Permitidas", df_emp_list['nome'].tolist(), 
                                        default=[df_emp_list[df_emp_list['id'] == i].iloc[0]['nome'] for i in atuais])
            if st.form_submit_button("Salvar Vínculos"):
                cursor.execute("DELETE FROM usuario_empresas WHERE usuario_id = %s", (u_id,))
                for n in novas_emps:
                    eid = int(df_emp_list[df_emp_list['nome'] == n].iloc[0]['id'])
                    cursor.execute("INSERT INTO usuario_empresas (usuario_id, empresa_id) VALUES (%s, %s)", (u_id, eid))
                conn.commit(); st.toast("Atualizado!"); time.sleep(1); st.rerun()

    with tab_novo:
        with st.form("novo_u"):
            n_nome = st.text_input("Nome")
            n_user = st.text_input("Login")
            n_pass = st.text_input("Senha", type="password")
            n_nivel = st.selectbox("Nível", ["CLIENT_OPERATOR", "ADMIN", "SUPER_ADMIN"])
            n_emps = st.multiselect("Empresas", df_emp_list['nome'].tolist())
            if st.form_submit_button("Criar"):
                cursor = conn.cursor()
                cursor.execute("INSERT INTO usuarios (nome, username, senha_hash, nivel_acesso, status_usuario) VALUES (%s,%s,%s,%s,'ATIVO')", (n_nome, n_user, gerar_hash_senha(n_pass), n_nivel))
                new_id = cursor.lastrowid
                for n in n_emps:
                    eid = int(df_emp_list[df_emp_list['nome'] == n].iloc[0]['id'])
                    cursor.execute("INSERT INTO usuario_empresas (usuario_id, empresa_id) VALUES (%s, %s)", (new_id, eid))
                conn.commit(); st.toast("Criado!"); time.sleep(1); st.rerun()
    conn.close()

# --- 11. SIDEBAR ---
with st.sidebar:
    st.markdown(f"<h2 style='color: #004b87; text-align: center;'>CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'><b>{st.session_state.usuario_logado}</b></p>", unsafe_allow_html=True)
    
    opcoes_menu = ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação", "Parâmetros Contábeis"]
    if st.session_state.nivel_acesso == "SUPER_ADMIN": opcoes_menu.append("Gestão de Utilizadores")
    
    menu = st.radio("Módulos", opcoes_menu)
    if st.button("Sair"): st.session_state.autenticado = False; st.rerun()

# --- 12. ROTEAMENTO (CARREGAMENTO DOS MÓDULOS) ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "Imobilizado & Depreciação": modulo_imobilizado()
elif menu == "Parâmetros Contábeis": modulo_parametros()
elif menu == "Gestão de Utilizadores": modulo_usuarios()
