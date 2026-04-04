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
st.set_page_config(
    page_title="Crescere - Apuração Fiscal",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.stApp { background-color: #f4f6f9; }
.stButton>button, .stDownloadButton>button {
    background-color: #004b87; color: white; border-radius: 4px; border: none;
    font-weight: 500; height: 45px; width: 100%; transition: all 0.2s;
}
.stButton>button:hover, .stDownloadButton>button:hover {
    background-color: #003366; color: white; transform: translateY(-1px);
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}
div[data-testid="stForm"], .css-1d391kg, .stExpander,
div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] {
    background-color: #ffffff; padding: 20px; border-radius: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;
}
h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
.stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] {
    background-color: #f8fafc; border: 1px solid #cbd5e1;
}
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
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
        self.cell(0, 5, 'Desenvolvido por Rodrhigo Alves \n Conciliacao e Auditoria Contabil', 0, 1, 'C')
        self.cell(0, 5, f'Pagina {self.page_no()}', 0, 0, 'C')

# --- FUNÇÃO PADRÃO PARA EXPORTAÇÃO ERP (MANTÉM AS 11 COLUNAS EXATAS) ---
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
    df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO' ORDER BY nome ASC", conn)
    conn.close()
    return df

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def gerar_hash_senha(senha_plana):
    return bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def consultar_cnpj(cnpj_limpo):
    try:
        res = requests.get(f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}", timeout=10)
        return res.json() if res.status_code == 200 else None
    except:
        return None

# --- 2.1 NOVO: EMPRESAS VISÍVEIS (REGRA CENTRAL) ---
@st.cache_data(ttl=120)
def carregar_empresas_visiveis(contabilidade_id: int | None,
                               usuario_id: int | None,
                               nivel_acesso: str | None,
                               empresa_id_legacy: int | None = None) -> pd.DataFrame:
    """
    Regra:
      - SUPER_ADMIN: todas as empresas ativas
      - Outros (ADMIN/CLIENT_OPERATOR): empresas liberadas em usuario_empresas com status='ATIVO'
    Fallback:
      - se não houver permissões ainda, usa empresa_id legado (se existir)
    """
    # SUPER_ADMIN vê tudo
    if (nivel_acesso or "").upper() == "SUPER_ADMIN":
        return carregar_empresas_ativas()

    # Se ainda não tem IDs de sessão, tenta fallback
    if not contabilidade_id or not usuario_id:
        if empresa_id_legacy:
            df_all = carregar_empresas_ativas()
            return df_all[df_all["id"] == int(empresa_id_legacy)]
        return pd.DataFrame()

    conn = get_db_connection()
    try:
        sql = """
            SELECT e.*
            FROM empresas e
            JOIN usuario_empresas ue ON ue.empresa_id = e.id
            WHERE ue.contabilidade_id = %s
              AND ue.usuario_id = %s
              AND ue.status = 'ATIVO'
              AND e.status_assinatura = 'ATIVO'
            ORDER BY e.nome ASC
        """
        df = pd.read_sql(sql, conn, params=(int(contabilidade_id), int(usuario_id)))
    finally:
        conn.close()

    # fallback legado se não existir permissões
    if df.empty and empresa_id_legacy:
        df_all = carregar_empresas_ativas()
        df = df_all[df_all["id"] == int(empresa_id_legacy)]
    return df

# --- 3. MOTOR DE CÁLCULO ---
def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome:
            return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido":
        return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

# --- 4. CONTROLO DE ESTADO E AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

# NOVO: ids necessários para permissões
if 'usuario_id' not in st.session_state:
    st.session_state.usuario_id = None
if 'contabilidade_id' not in st.session_state:
    st.session_state.contabilidade_id = None

# legado (se ainda existe no schema)
if 'empresa_id_legacy' not in st.session_state:
    st.session_state.empresa_id_legacy = None

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {
        "id": None, "nome": "", "fantasia": "", "cnpj": "",
        "regime": "Lucro Real", "tipo": "Matriz", "cnae": "",
        "endereco": "", "apelido_unidade": "", "conta_transf_pis": "",
        "conta_transf_cofins": ""
    }

if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

if 'form_key' not in st.session_state:
    st.session_state.form_key = 0

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
                cursor.execute(
                    "SELECT u.* FROM usuarios u WHERE u.username = %s AND u.status_usuario = 'ATIVO'",
                    (user_input,)
                )
                user_data = cursor.fetchone()
                conn.close()

                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.autenticado = True

                    # existentes no seu app
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']

                    # NOVO: ids essenciais
                    st.session_state.usuario_id = user_data.get('id')
                    st.session_state.contabilidade_id = user_data.get('contabilidade_id')

                    # legado (se ainda usa empresa_id em algum módulo)
                    st.session_state.empresa_id_legacy = user_data.get('empresa_id')

                    # nível acesso (seu “rodrhigo = SUPER_ADMIN”)
                    st.session_state.nivel_acesso = (
                        "SUPER_ADMIN"
                        if user_data['username'].lower() == "rodrhigo"
                        else user_data['nivel_acesso']
                    )

                    st.rerun()
                else:
                    st.error("Credenciais inválidas.")

    st.stop()
# ==========================================================
# ========================= PART 2 ==========================
# ==========================================================

# --- 5. MÓDULO GESTÃO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("### Gestão de Empresas e Unidades")

    # REGRAS:
    # - SUPER_ADMIN: pode ver/cadastrar tudo
    # - ADMIN: pode ver/cadastrar tudo (dentro da contabilidade do admin, se você aplicar esse filtro depois)
    # - CLIENT_OPERATOR: geralmente NÃO deveria cadastrar empresa (mantive como está, mas você pode bloquear se quiser)
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR":
        st.error("Acesso restrito.")
        return

    tab_cad, tab_lista = st.tabs(["Novo Registo", "Unidades Registadas"])

    with tab_cad:
        c_busca, c_btn = st.columns([3, 1])
        with c_busca:
            cnpj_input = st.text_input("CNPJ para busca automática na Receita Federal:")
        with c_btn:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            if st.button("Consultar CNPJ", use_container_width=True):
                res = consultar_cnpj(cnpj_input.replace(".", "").replace("/", "").replace("-", ""))
                if res and res.get('status') != 'ERROR':
                    st.session_state.dados_form.update({
                        "nome": res.get('nome', ''),
                        "fantasia": res.get('fantasia', ''),
                        "cnpj": res.get('cnpj', ''),
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

            lista_regimes = [
                "Lucro Real", "Lucro Presumido", "Simples Nacional", "Simples Nacional - Excesso",
                "MEI", "Arbitrado", "Imune/Isenta", "Inativa"
            ]
            regime = c4.selectbox(
                "Regime", lista_regimes,
                index=lista_regimes.index(f.get('regime')) if f.get('regime') in lista_regimes else 0
            )
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
                            cursor.execute(
                                "UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s",
                                (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, int(f['id']))
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)",
                                (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido)
                            )
                        conn.commit()
                        carregar_empresas_ativas.clear()
                        st.success("Gravado com sucesso!")
                        st.session_state.dados_form = {
                            "id": None, "nome": "", "fantasia": "", "cnpj": "",
                            "regime": "Lucro Real", "tipo": "Matriz", "cnae": "",
                            "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""
                        }
                    except Exception as e:
                        conn.rollback()
                        st.error(f"Erro: {e}")
                    finally:
                        conn.close()

    with tab_lista:
        df = carregar_empresas_ativas()
        for _, row in df.iterrows():
            col_info, col_btn = st.columns([5, 1])
            col_info.markdown(
                f"**{row['nome']}** ({row['apelido_unidade'] or row['tipo']})<br><small>CNPJ: {row['cnpj']}</small>",
                unsafe_allow_html=True
            )
            if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                conn = get_db_connection()
                df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={int(row['id'])}", conn)
                conn.close()
                st.session_state.dados_form = df_edit.iloc[0].to_dict()
                st.rerun()

        st.divider()


# --- 6. MÓDULO APURAÇÃO ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos (PIS/COFINS)")

    # NOVO: empresas visíveis pelo ACL (usuario_empresas)
    df_emp = carregar_empresas_visiveis(
        st.session_state.contabilidade_id,
        st.session_state.usuario_id,
        st.session_state.nivel_acesso,
        st.session_state.empresa_id_legacy
    )

    if df_emp.empty:
        st.warning("Nenhuma unidade/empresa liberada para este utilizador.")
        return

    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox(
        "Unidade",
        df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1)
    )
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
        else:
            num_nota = fornecedor = None

        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if v_base <= 0:
                st.warning("A base de cálculo deve ser maior que zero.")
            elif teve_retencao and v_pis_ret == 0 and v_cof_ret == 0:
                st.warning("Informe os valores retidos.")
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
                st.session_state.form_key += 1
                st.rerun()

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
                    doc_str = f"<br>Doc: {it['nota']}" if it.get('nota') else ""
                    forn_str = f"<br>Forn: {it['fornecedor']}" if it.get('fornecedor') else ""
                    hist_str = f"<br>Histórico: {it['hist']}" if it.get('hist') else ""

                    c_txt.markdown(
                        f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}{ret_badge}"
                        f"<br>PIS: {formatar_moeda(it['v_pis'])} | COF: {formatar_moeda(it['v_cofins'])}"
                        f"<br><span style='color:#64748b;'>{doc_str}{forn_str}{hist_str}</span></small>",
                        unsafe_allow_html=True
                    )
                    c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base'])}</span>", unsafe_allow_html=True)
                    c_del.button("×", key=f"del_{it['id_unico']}", on_click=remover_do_rascunho, args=(i,))
                    st.markdown("<hr style='margin: 5px 0;'>", unsafe_allow_html=True)

        if st.button("Gravar na Base de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos) == 0):
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                m, a = competencia.split('/')
                comp_db = f"{a}-{m.zfill(2)}"
                cursor.execute("START TRANSACTION")

                for it in st.session_state.rascunho_lancamentos:
                    query = """
                    INSERT INTO lancamentos (
                        empresa_id, operacao_id, competencia, data_lancamento,
                        valor_base, valor_pis, valor_cofins, valor_pis_retido, valor_cofins_retido,
                        historico, usuario_registro, status_auditoria,
                        origem_retroativa, competencia_origem, num_nota, fornecedor
                    )
                    VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s)
                    """
                    c_origem_db = None
                    if it['origem']:
                        mo, ao = it['origem'].split('/')
                        c_origem_db = f"{ao}-{mo.zfill(2)}"

                    cursor.execute(query, (
                        int(it['emp_id']), int(it['op_id']), comp_db,
                        float(it['v_base']), float(it['v_pis']), float(it['v_cofins']),
                        float(it.get('v_pis_ret', 0)), float(it.get('v_cof_ret', 0)),
                        it['hist'], st.session_state.username, int(it['retro']),
                        c_origem_db, it['nota'], it['fornecedor']
                    ))

                conn.commit()
                st.session_state.rascunho_lancamentos = []
                st.success("Gravado com sucesso no banco de dados!")
                st.rerun()

            except Exception as e:
                conn.rollback()
                st.error(f"Erro no banco: {e}")
            finally:
                conn.close()

    # --- AUDITORIA DB (opcional manter como estava, não mexi aqui para evitar quebrar) ---


# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO ---
def modulo_relatorios():
    st.markdown("### Exportação para ERP e PDF Analítico")

    # NOVO: empresas visíveis pelo ACL (usuario_empresas)
    df_emp = carregar_empresas_visiveis(
        st.session_state.contabilidade_id,
        st.session_state.usuario_id,
        st.session_state.nivel_acesso,
        st.session_state.empresa_id_legacy
    )

    if df_emp.empty:
        st.warning("Nenhuma unidade/empresa liberada para este utilizador.")
        return

    c1, c2 = st.columns([2, 1])
    emp_sel = c1.selectbox("Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    emp_row = df_emp[df_emp['id'] == emp_id].iloc[0]

    competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    consolidar = st.checkbox("Consolidar apuração com Filiais (mesma Raiz CNPJ)")

    # (Aqui você mantém o seu código de exportação/geração de PDF exatamente como já estava)
    st.info("⚠️ Nesta Parte 2, mantive apenas a seleção de empresa e parâmetros.\n"
            "Cole sua lógica de exportação original aqui (igual já funcionava), usando emp_id e emp_row.")


# --- 7.5 MÓDULO IMOBILIZADO E DEPRECIAÇÃO ---
def modulo_imobilizado():
    st.markdown("### Gestão de Ativo Imobilizado")

    # NOVO: empresas visíveis pelo ACL (usuario_empresas)
    df_emp = carregar_empresas_visiveis(
        st.session_state.contabilidade_id,
        st.session_state.usuario_id,
        st.session_state.nivel_acesso,
        st.session_state.empresa_id_legacy
    )

    if df_emp.empty:
        st.warning("Nenhuma unidade/empresa liberada para este utilizador.")
        return

    c_emp, _ = st.columns([2, 1])
    emp_sel = c_emp.selectbox(
        "Unidade",
        df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1),
        key="imo_emp"
    )
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['apelido_unidade'] or r['tipo']}", axis=1) == emp_sel].iloc[0]['id'])

    # (Aqui você mantém a sua lógica original do imobilizado, usando tenant_id = emp_id como você já faz)
    st.info("⚠️ Nesta Parte 2, mantive apenas a seleção de empresa.\n"
            "Cole sua lógica original do Imobilizado aqui (igual já funcionava), usando emp_id como tenant_id.")


# --- 8. MÓDULO PARÂMETROS CONTÁBEIS ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR":
        st.error("Acesso restrito.")
        return

    st.markdown("### Parâmetros Contábeis e Exportação ERP")
    st.info("⚠️ Cole aqui o seu módulo de parâmetros original (sem mudanças).")
    
# ==========================================================
# ========================= PART 3 ==========================
# ==========================================================

def modulo_usuarios():
    """
    Atualizado:
      - ADMIN e SUPER_ADMIN podem acessar
      - Admin gerencia usuários/permissões do próprio contabilidade_id
      - Tela Permissões por Empresa: concede/reativa/inativa sem mexer no banco manualmente
    """
    if st.session_state.nivel_acesso not in ("SUPER_ADMIN", "ADMIN"):
        st.error("Acesso restrito.")
        return

    contab_id = st.session_state.contabilidade_id
    meu_user_id = st.session_state.usuario_id

    st.markdown("### Gestão de Utilizadores")

    conn = get_db_connection()

    # SUPER_ADMIN vê todos, ADMIN vê só do tenant dele
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        df_users = pd.read_sql(
            "SELECT id, nome, username, nivel_acesso, status_usuario, data_criacao, contabilidade_id FROM usuarios ORDER BY nome ASC",
            conn
        )
    else:
        df_users = pd.read_sql(
            "SELECT id, nome, username, nivel_acesso, status_usuario, data_criacao, contabilidade_id "
            "FROM usuarios WHERE contabilidade_id = %s ORDER BY nome ASC",
            conn,
            params=(int(contab_id),)
        )

    df_empresas = pd.read_sql(
        "SELECT id, nome, cnpj FROM empresas WHERE status_assinatura = 'ATIVO' ORDER BY nome ASC",
        conn
    )

    tab_lista, tab_novo, tab_perms = st.tabs(["Utilizadores Registados", "Adicionar Utilizador", "Permissões por Empresa"])

    # ---------------- TAB LISTA ----------------
    with tab_lista:
        st.dataframe(df_users, use_container_width=True, hide_index=True)

        st.markdown("##### Gerir Acesso (Conta)")
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
                    else:
                        if len(nova_senha) < 6:
                            st.error("A senha deve ter pelo menos 6 caracteres.")
                            conn.close()
                            return
                        cursor.execute("UPDATE usuarios SET senha_hash = %s WHERE username = %s",
                                      (gerar_hash_senha(nova_senha), usr_sel))
                        st.toast("Palavra-passe atualizada com sucesso!")
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    st.error(f"Erro no banco: {e}")
                finally:
                    conn.close()
                st.rerun()

    # ---------------- TAB NOVO USUÁRIO ----------------
    with tab_novo:
        with st.form("form_novo_usuario"):
            col_nome, col_user = st.columns(2)
            novo_nome = col_nome.text_input("Nome Completo")
            novo_user = col_user.text_input("Nome de Utilizador (Login)")

            col_pass, col_nivel = st.columns(2)
            nova_pass = col_pass.text_input("Palavra-passe Inicial", type="password")
            nivel = col_nivel.selectbox("Nível de Acesso", ["CLIENT_OPERATOR", "ADMIN", "SUPER_ADMIN"])

            # Para manter compatibilidade: empresa_id legado opcional
            lista_empresas = ["Nenhuma (Sem Empresa Padrão)"] + df_empresas.apply(lambda r: f"{r['id']} - {r['nome']}", axis=1).tolist()
            emp_vinculada = st.selectbox("Empresa padrão (opcional)", lista_empresas)

            if st.form_submit_button("Criar Utilizador"):
                if not novo_nome or not novo_user or len(nova_pass) < 6:
                    st.error("Preencha todos os campos corretamente (senha mín. 6 caracteres).")
                    conn.close()
                    return
                if novo_user in df_users['username'].tolist():
                    st.error("Este utilizador já existe.")
                    conn.close()
                    return

                cursor = conn.cursor()
                try:
                    empresa_id_db = None
                    if emp_vinculada != "Nenhuma (Sem Empresa Padrão)":
                        empresa_id_db = int(emp_vinculada.split(" - ")[0])

                    # ADMIN cria usuários sempre no tenant dele
                    contab_insert = contab_id if st.session_state.nivel_acesso == "ADMIN" else contab_id

                    cursor.execute("""
                        INSERT INTO usuarios (nome, username, senha_hash, nivel_acesso, status_usuario, data_criacao, empresa_id, contabilidade_id)
                        VALUES (%s, %s, %s, %s, 'ATIVO', NOW(), %s, %s)
                    """, (novo_nome, novo_user, gerar_hash_senha(nova_pass), nivel, empresa_id_db, contab_insert))

                    novo_usuario_id = cursor.lastrowid

                    # Se selecionou empresa padrão, já concede acesso ATIVO via usuario_empresas
                    if empresa_id_db is not None:
                        cursor.execute("""
                            INSERT INTO usuario_empresas (contabilidade_id, usuario_id, empresa_id, status, concedido_por)
                            VALUES (%s, %s, %s, 'ATIVO', %s)
                            ON DUPLICATE KEY UPDATE status='ATIVO', concedido_por=VALUES(concedido_por)
                        """, (int(contab_insert), int(novo_usuario_id), int(empresa_id_db), int(meu_user_id)))

                    conn.commit()
                    st.toast("Utilizador criado com sucesso!", icon="✅")
                except Exception as e:
                    conn.rollback()
                    st.error(f"Erro ao inserir no banco: {e}")
                finally:
                    conn.close()
                st.rerun()

    # ---------------- TAB PERMISSÕES POR EMPRESA ----------------
    with tab_perms:
        st.markdown("#### Permissões por Empresa (Multiempresa)")

        # Reabre conexão (a anterior pode ter sido fechada em outros fluxos)
        conn2 = get_db_connection()

        # Para admin: lista só usuários do tenant dele
        if st.session_state.nivel_acesso == "SUPER_ADMIN":
            df_users2 = pd.read_sql(
                "SELECT id, nome, username, nivel_acesso, status_usuario FROM usuarios WHERE status_usuario='ATIVO' ORDER BY nome ASC",
                conn2
            )
        else:
            df_users2 = pd.read_sql(
                "SELECT id, nome, username, nivel_acesso, status_usuario FROM usuarios WHERE status_usuario='ATIVO' AND contabilidade_id=%s ORDER BY nome ASC",
                conn2, params=(int(contab_id),)
            )

        if df_users2.empty:
            st.info("Nenhum usuário ATIVO encontrado.")
            conn2.close()
            return

        # Seleciona usuário
        labels = df_users2.apply(lambda r: f"{r['nome']} ({r['username']})", axis=1).tolist()
        sel = st.selectbox("Escolha o usuário", labels)
        usuario_alvo_id = int(df_users2.loc[df_users2.apply(lambda r: f"{r['nome']} ({r['username']})", axis=1) == sel].iloc[0]["id"])

        # Empresas ativas
        df_emp2 = pd.read_sql("SELECT id, nome FROM empresas WHERE status_assinatura='ATIVO' ORDER BY nome ASC", conn2)
        if df_emp2.empty:
            st.info("Nenhuma empresa ATIVA cadastrada.")
            conn2.close()
            return

        # Acessos atuais (ATIVO/INATIVO)
        df_acl = pd.read_sql("""
            SELECT empresa_id, status
            FROM usuario_empresas
            WHERE contabilidade_id=%s AND usuario_id=%s
        """, conn2, params=(int(contab_id), int(usuario_alvo_id)))

        status_map = {int(r["empresa_id"]): r["status"] for _, r in df_acl.iterrows()} if not df_acl.empty else {}

        # Multi-select: mostra ATIVAS como padrão
        opcoes = df_emp2.apply(lambda r: f"{r['id']} - {r['nome']}", axis=1).tolist()
        defaults = [f"{eid} - {df_emp2[df_emp2['id']==eid].iloc[0]['nome']}"
                    for eid, stt in status_map.items()
                    if stt == "ATIVO" and not df_emp2[df_emp2["id"] == eid].empty]

        novas_ativas = st.multiselect("Empresas ATIVAS para este usuário", options=opcoes, default=defaults)

        novas_set = set(int(x.split(" - ")[0]) for x in novas_ativas)
        atuais_set = set(int(eid) for eid, stt in status_map.items() if stt == "ATIVO")

        adicionar = sorted(list(novas_set - atuais_set))
        remover = sorted(list(atuais_set - novas_set))

        c1, c2 = st.columns(2)
        c1.write("**Conceder:** " + (", ".join(map(str, adicionar)) if adicionar else "—"))
        c2.write("**Revogar (inativar):** " + (", ".join(map(str, remover)) if remover else "—"))

        if st.button("Salvar permissões", type="primary", use_container_width=True):
            cur = conn2.cursor()
            try:
                # Conceder (insert / reativar)
                for emp_id in adicionar:
                    cur.execute("""
                        INSERT INTO usuario_empresas (contabilidade_id, usuario_id, empresa_id, status, concedido_por)
                        VALUES (%s,%s,%s,'ATIVO',%s)
                        ON DUPLICATE KEY UPDATE status='ATIVO', concedido_por=VALUES(concedido_por)
                    """, (int(contab_id), int(usuario_alvo_id), int(emp_id), int(meu_user_id)))

                # Revogar (inativar)
                for emp_id in remover:
                    cur.execute("""
                        UPDATE usuario_empresas
                        SET status='INATIVO', concedido_por=%s
                        WHERE contabilidade_id=%s AND usuario_id=%s AND empresa_id=%s
                    """, (int(meu_user_id), int(contab_id), int(usuario_alvo_id), int(emp_id)))

                conn2.commit()
                st.success("Permissões atualizadas!")
            except Exception as e:
                conn2.rollback()
                st.error(f"Erro ao atualizar permissões: {e}")
            finally:
                conn2.close()
            st.rerun()


# --- 10. MENU LATERAL (atualizado) ---
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

    # Menu dinâmico (esconde o que não deve)
    opcoes = ["Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação"]

    if st.session_state.nivel_acesso in ("ADMIN", "SUPER_ADMIN"):
        opcoes = ["Gestão de Empresas"] + opcoes + ["Parâmetros Contábeis", "Gestão de Utilizadores"]
    else:
        # CLIENT_OPERATOR
        opcoes = ["Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação"]

    menu = st.radio("Módulos", opcoes)
    st.write("---")

    if st.button("Encerrar Sessão", use_container_width=True):
        # limpa os principais campos
        for k in ["autenticado", "usuario_id", "contabilidade_id", "empresa_id_legacy", "username", "usuario_logado", "nivel_acesso"]:
            if k in st.session_state:
                st.session_state[k] = None
        st.session_state.autenticado = False
        st.rerun()


# --- 11. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas":
    modulo_empresas()
elif menu == "Apuração Mensal":
    modulo_apuracao()
elif menu == "Relatórios e Integração":
    modulo_relatorios()
elif menu == "Imobilizado & Depreciação":
    modulo_imobilizado()
elif menu == "Parâmetros Contábeis":
    modulo_parametros()
elif menu == "Gestão de Utilizadores":
    modulo_usuarios()
