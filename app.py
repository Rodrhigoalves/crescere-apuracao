import streamlit as st
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool
import pandas as pd
import requests
from datetime import date, datetime, timedelta, timezone
import io
import bcrypt
from fpdf import FPDF
import calendar
import uuid
import re
from contextlib import contextmanager

# --- 1. CONFIGURAÇÕES VISUAIS E INJEÇÃO CSS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button, .stDownloadButton>button, a[data-testid="stLinkButton"]>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; width: 100%; transition: all 0.2s; }
    .stButton>button:hover, .stDownloadButton>button:hover, a[data-testid="stLinkButton"]>button:hover { background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
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

def validar_competencia(comp):
    if not re.match(r"^(0[1-9]|1[0-2])\/\d{4}$", comp):
        return None
    m, a = comp.split('/')
    return f"{a}-{m.zfill(2)}"

def formatar_historico_erp(texto_base, competencia):
    base = limpar_texto(texto_base)
    return f"{base} - {competencia}" if base else f"LANCAMENTO CONTABIL - {competencia}"

# --- CLASSE DE PDF PADRONIZADA ---
class RelatorioCrescerePDF(FPDF):
    def add_cabecalho(self, empresa_nome, empresa_cnpj, titulo_relatorio, periodo=""):
        self.set_font("Arial", 'B', 14)
        self.cell(0, 6, empresa_nome, ln=True, align='L')
        self.set_font("Arial", '', 10)
        self.cell(0, 6, f"CNPJ: {empresa_cnpj}", ln=True, align='L')
        self.ln(5)
        self.set_font("Arial", 'B', 12)
        for linha_titulo in titulo_relatorio.split('\n'):
            self.cell(0, 8, linha_titulo, ln=True, align='C')
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

# --- 2. CONEXÃO, POOL E CACHE ---
@st.cache_resource
def init_connection_pool():
    try:
        return MySQLConnectionPool(
            pool_name="crescere_pool",
            pool_size=10, 
            pool_reset_session=True,
            **st.secrets["mysql"]
        )
    except mysql.connector.Error as err:
        st.error(f"Erro crítico ao iniciar Pool do banco de dados: {err}")
        st.stop()

db_pool = init_connection_pool()

@contextmanager
def get_db_connection():
    conn = db_pool.get_connection()
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def get_db_cursor(commit=False, dictionary=False):
    conn = db_pool.get_connection()
    cursor = conn.cursor(dictionary=dictionary)
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()

@st.cache_data(ttl=300)
def carregar_operacoes():
    with get_db_connection() as conn:
        return pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)

@st.cache_data(ttl=300)
def carregar_empresas_ativas():
    with get_db_connection() as conn:
        df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
        if not df.empty:
            df['id'] = df['id'].astype(int)
        return df

@st.cache_data(ttl=120)
def carregar_empresas_visiveis():
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        return carregar_empresas_ativas()
    
    with get_db_connection() as conn:
        query = """
            SELECT e.* FROM empresas e
            JOIN usuario_empresas ue ON e.id = ue.empresa_id
            WHERE ue.contabilidade_id = %s
              AND ue.usuario_id = %s
              AND ue.status = 'ATIVO'
              AND e.status_assinatura = 'ATIVO'
        """
        df = pd.read_sql(query, conn, params=(int(st.session_state.contabilidade_id), int(st.session_state.usuario_id)))
        
        if df.empty and st.session_state.empresa_id_legacy:
            query_fallback = "SELECT * FROM empresas WHERE id = %s AND status_assinatura = 'ATIVO'"
            df = pd.read_sql(query_fallback, conn, params=(int(st.session_state.empresa_id_legacy),))
            
        if not df.empty:
            df['id'] = df['id'].astype(int)
        return df

def verificar_senha(senha_plana, hash_banco): return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))
def gerar_hash_senha(senha_plana): return bcrypt.hashpw(senha_plana.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

@st.cache_data(ttl=86400)
def consultar_cnpj(cnpj_limpo):
    try: 
        headers = {'Accept': 'application/json'}
        res = requests.get(f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}", headers=headers, timeout=10)
        return res.json() if res.status_code == 200 else None
    except: return None

# --- 3. MOTOR DE CÁLCULO E ASSISTENTE ---
def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido": return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

def buscar_sugestao_imobilizado(emp_id, competencia_str):
    comp_db = validar_competencia(competencia_str)
    if not comp_db: return 0.0

    ano_alvo, mes_alvo = map(int, comp_db.split('-'))
    ultimo_dia = calendar.monthrange(ano_alvo, mes_alvo)[1]
    data_inicio_mes = date(ano_alvo, mes_alvo, 1)

    total_sugerido = 0.0

    query_bens = """
        SELECT b.*, g.taxa_anual_percentual
        FROM bens_imobilizado b
        LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id
        WHERE b.tenant_id = %s AND b.status = 'ativo' AND b.regra_credito != 'NENHUM (Sem Crédito)'
    """

    with get_db_connection() as conn:
        df_bens = pd.read_sql(query_bens, conn, params=(emp_id,))
        df_planos = pd.read_sql("SELECT * FROM plano_depreciacao_itens WHERE bem_id IN (SELECT id FROM bens_imobilizado WHERE tenant_id = %s)", conn, params=(emp_id,))

    if df_bens.empty:
        return 0.0

    if not df_planos.empty:
        df_planos['mes_referencia'] = pd.to_datetime(df_planos['mes_referencia']).dt.date

    for _, b in df_bens.iterrows():
        # 1. Regra de Crédito INTEGRAL
        if "INTEGRAL" in b['regra_credito']:
            dt_compra = b['data_compra']
            if dt_compra.year == ano_alvo and dt_compra.month == mes_alvo:
                total_sugerido += float(b['valor_compra'])
            continue

        # 2. Regra MENSAL
        dt_base = b['data_saldo_inicial'] if pd.notnull(b.get('data_saldo_inicial')) else b['data_compra']
        if ano_alvo < dt_base.year or (ano_alvo == dt_base.year and mes_alvo < dt_base.month):
            continue # Bem ainda não tinha sido comprado nesta competência

        # Tem plano de continuidade gravado (Cenário 3)?
        plano_do_bem = df_planos[df_planos['bem_id'] == b['id']] if not df_planos.empty else pd.DataFrame()
        if not plano_do_bem.empty:
            plano_mes = plano_do_bem[plano_do_bem['mes_referencia'] == data_inicio_mes]
            if not plano_mes.empty:
                total_sugerido += float(plano_mes.iloc[0]['valor_cota'])
            continue

        # Se não tem plano, o Assistente calcula a cota dinamicamente (Cenário 1 ou 2)
        base_calc = float(b['valor_compra'])
        taxa_anual = float(b['taxa_customizada']) / 100.0 if (pd.notnull(b.get('taxa_customizada')) and float(b['taxa_customizada']) > 0) else (float(b['taxa_anual_percentual']) / 100.0 if pd.notnull(b.get('taxa_anual_percentual')) else 0.0)

        if taxa_anual == 0: continue

        dt_ref_calc_ant = data_inicio_mes - timedelta(days=1)
        dias_totais_ant = max(0, (dt_ref_calc_ant - dt_base).days)
        dep_acumulada_ant = min(base_calc, (base_calc * taxa_anual / 365.0) * dias_totais_ant)

        saldo_ini = float(b.get('valor_residual_inicial', 0.0))
        residual_ant = max(0.0, saldo_ini - dep_acumulada_ant) if pd.notnull(b.get('data_saldo_inicial')) else max(0.0, base_calc - dep_acumulada_ant)

        if residual_ant <= 0.009: continue

        dia_inicial = dt_base.day if (ano_alvo == dt_base.year and mes_alvo == dt_base.month) else 1
        dias_uso = max(0, ultimo_dia - dia_inicial + 1)
        cota = (base_calc * taxa_anual / 365.0) * dias_uso

        total_sugerido += min(cota, residual_ant)

    return float(total_sugerido)

# --- 4. CONTROLE DE ESTADO E AUTENTICAÇÃO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'usuario_id' not in st.session_state: st.session_state.usuario_id = None
if 'contabilidade_id' not in st.session_state: st.session_state.contabilidade_id = None
if 'empresa_id_legacy' not in st.session_state: st.session_state.empresa_id_legacy = None
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
                with get_db_cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT u.* FROM usuarios u WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                    user_data = cursor.fetchone()
                
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']
                    st.session_state.usuario_id = user_data.get('id')
                    st.session_state.contabilidade_id = user_data.get('contabilidade_id')
                    st.session_state.empresa_id_legacy = user_data.get('empresa_id')
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
                    try:
                        with get_db_cursor(commit=True) as cursor:
                            if f['id']: 
                                cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, apelido_unidade=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido, int(f['id'])))
                            else: 
                                cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura, apelido_unidade) VALUES (%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s)", (nome, fanta, cnpj, regime, tipo, cnae, endereco, apelido))
                        carregar_empresas_ativas.clear()
                        carregar_empresas_visiveis.clear()
                        st.success("Gravado com sucesso!")
                        st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": "", "conta_transf_pis": "", "conta_transf_cofins": ""}
                    except Exception as e: st.error(f"Erro: {e}")
    with tab_lista:
        df = carregar_empresas_ativas()
        for _, row in df.iterrows():
            col_info, col_btn = st.columns([5, 1])
            nome_display = formatar_nome_empresa(row)
            col_info.markdown(f"**{nome_display}**<br><small>CNPJ: {row['cnpj']}</small>", unsafe_allow_html=True)
            if col_btn.button("Editar", key=f"btn_emp_{row['id']}"):
                with get_db_connection() as conn:
                    df_edit = pd.read_sql("SELECT * FROM empresas WHERE id=%s", conn, params=(int(row['id']),))
                st.session_state.dados_form = df_edit.iloc[0].to_dict(); st.rerun()
            st.divider()

# --- 6. MÓDULO APURAÇÃO ---
def modulo_apuracao():
    st.markdown("### Apuração de Impostos e Apropriação de Custo")
    df_emp = carregar_empresas_visiveis()
    
    if df_emp.empty:
        st.warning("Nenhuma unidade liberada para este utilizador. Solicite o acesso ao seu Gestor.")
        return

    df_op = carregar_operacoes()
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(formatar_nome_empresa, axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(formatar_nome_empresa, axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Operador", value=st.session_state.usuario_logado, disabled=True)

    with get_db_connection() as conn_dest:
        df_destinos_custo = pd.read_sql("SELECT * FROM destinos_custo WHERE empresa_id = %s", conn_dest, params=(emp_id,))

    st.divider()
    col_in, col_ras = st.columns([1, 1], gap="large")

    with col_in:
        fk = st.session_state.form_key
        tab_fiscal, tab_custo = st.tabs(["1. Notas Fiscais (PDF / Impostos)", "2. Custo Avulso (CMV / CSV)"])

        with tab_fiscal:
            op_sel = st.selectbox("Operação Fiscal", df_op['nome_exibicao'].tolist(), key=f"op_{fk}")
            op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
            
            # --- INÍCIO DO ASSISTENTE CRESCERE ---
            # .lower() converte tudo para minúsculo, e buscando só "deprecia" a gente ignora erro de acento ou 'ç'
            if "deprecia" in op_row['nome'].lower():
                valor_sugerido = buscar_sugestao_imobilizado(emp_id, competencia)
                if valor_sugerido > 0:
                    st.info(f"💡 **Assistente Crescere:** Identificamos **{formatar_moeda(valor_sugerido)}** de base de crédito (depreciação mensal/integral) validada para este mês no Imobilizado.")
                    if st.button("✨ Usar valor sugerido", key=f"btn_sug_{fk}"):
                        st.session_state[f"valor_sugerido_{fk}"] = valor_sugerido
                        st.rerun()
            # --- FIM DO ASSISTENTE ---

            val_default = st.session_state.get(f"valor_sugerido_{fk}", 0.0)
            
            v_base = st.number_input("Valor Total da Fatura / Base (R$)", min_value=0.00, step=100.0, value=val_default, key=f"base_{fk}")
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
            if st.button("Adicionar Lançamento Fiscal", use_container_width=True, type="primary"):
                comp_valida = validar_competencia(competencia)
                origem_valida = validar_competencia(comp_origem) if retro else True
                
                if not comp_valida: st.error("O formato da Competência deve ser MM/AAAA válido.")
                elif retro and not origem_valida: st.error("O formato do Mês de Origem deve ser MM/AAAA válido.")
                elif v_base <= 0: st.warning("A base de cálculo deve ser maior que zero.")
                elif exige_doc and (not num_nota or not fornecedor or (retro and not comp_origem) or (retro and not hist)):
                    st.error("Para Retenções e Extemporâneos, o Nº do Documento, Fornecedor, Mês Origem e Histórico são obrigatórios.")
                else:
                    vp_calc, vc_calc = calcular_impostos(regime, op_row['nome'], v_base)
                    st.session_state.rascunho_lancamentos.append({
                        "id_unico": uuid.uuid4().hex, "emp_id": int(emp_id), "op_id": int(op_row['id']),
                        "op_nome": op_sel, "v_base": float(v_base), "v_pis": float(vp_calc), "v_cofins": float(vc_calc),
                        "v_pis_ret": float(v_pis_ret), "v_cof_ret": float(v_cof_ret), "hist": hist, "retro": int(retro),
                        "origem": comp_origem if retro else None, "nota": num_nota, "fornecedor": fornecedor,
                        "is_custo_avulso": 0, "custo_liq": 0.0, "c_deb": None, "c_cred": None, "c_cod": None, "c_txt": None
                    })
                    # Limpa a sugestão após adicionar
                    if f"valor_sugerido_{fk}" in st.session_state: del st.session_state[f"valor_sugerido_{fk}"]
                    st.session_state.form_key += 1; st.rerun()

        with tab_custo:
            st.info("Utilize esta aba para apropriar o custo cheio do estoque ou serviço consumido no mês. O sistema extrairá os impostos recuperáveis para gerar a linha contábil ERP, sem afetar o PDF de apuração.")
            if df_destinos_custo.empty:
                st.error("Nenhum Destino de Custo configurado para esta unidade. Vá na aba 'Parâmetros Contábeis' > 'Destinos de Custo'.")
            else:
                destino_sel = st.selectbox("Selecione o Destino Contábil", df_destinos_custo['nome_destino'].tolist(), key=f"dest_sel_{fk}")
                v_consumido = st.number_input("Valor Bruto Consumido (R$)", min_value=0.00, step=100.0, key=f"v_cons_{fk}")
                
                vp_c, vc_c = calcular_impostos(regime, "Despesa", v_consumido)
                custo_liquido_projecao = v_consumido - vp_c - vc_c
                
                if v_consumido > 0:
                    st.markdown(f"<small>Impostos Extraídos: PIS ({formatar_moeda(vp_c)}) | COFINS ({formatar_moeda(vc_c)})</small><br><b>Custo Líquido a Contabilizar: {formatar_moeda(custo_liquido_projecao)}</b>", unsafe_allow_html=True)

                hist_c = st.text_input("Observação Opcional (Será anexada ao histórico)", key=f"hist_c_{fk}")
                nota_c = st.text_input("Documento/Requisição (Opcional)", key=f"nota_c_{fk}")

                st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
                if st.button("Adicionar Custo Avulso", use_container_width=True, type="secondary"):
                    comp_valida = validar_competencia(competencia)
                    if not comp_valida: st.error("O formato da Competência deve ser MM/AAAA válido.")
                    elif v_consumido <= 0: st.warning("O valor consumido deve ser maior que zero.")
                    else:
                        dest_row = df_destinos_custo[df_destinos_custo['nome_destino'] == destino_sel].iloc[0]
                        dummy_op_id = int(df_op.iloc[0]['id']) # DB requirement fallback
                        
                        st.session_state.rascunho_lancamentos.append({
                            "id_unico": uuid.uuid4().hex, "emp_id": int(emp_id), "op_id": dummy_op_id,
                            "op_nome": f"[CUSTO AVULSO] {destino_sel}", "v_base": float(v_consumido),
                            "v_pis": 0.0, "v_cofins": 0.0, "v_pis_ret": 0.0, "v_cof_ret": 0.0,
                            "hist": hist_c, "retro": 0, "origem": None, "nota": nota_c, "fornecedor": "",
                            "is_custo_avulso": 1, "custo_liq": float(custo_liquido_projecao),
                            "c_deb": dest_row['conta_debito'], "c_cred": dest_row['conta_credito'],
                            "c_cod": dest_row['hist_codigo'], "c_txt": dest_row['hist_texto']
                        })
                        st.session_state.form_key += 1; st.rerun()

    with col_ras:
        st.markdown("#### Rascunho")
        
        def remover_do_rascunho(idx):
            st.session_state.rascunho_lancamentos.pop(idx)

        with st.container(height=420, border=True):
            if not st.session_state.rascunho_lancamentos:
                st.markdown("<p style='text-align:center;color:#94a3b8;margin-top:50px;'>Vazio.</p>", unsafe_allow_html=True)
            else:
                for i, it in enumerate(st.session_state.rascunho_lancamentos):
                    c_txt, c_val, c_del = st.columns([5, 3, 1])
                    
                    if it['is_custo_avulso'] == 1:
                        c_txt.markdown(f"<small style='line-height: 1.2; color: #16a34a;'><b>{it['op_nome']}</b><br>Custo Líquido p/ ERP: {formatar_moeda(it['custo_liq']).replace('$', '&#36;')}<br><span style='color:#64748b;'>Doc: {it['nota'] or 'N/A'}</span></small>", unsafe_allow_html=True)
                        c_val.markdown(f"<span style='font-size: 14px; font-weight: 600; color: #16a34a;'>{formatar_moeda(it['v_base']).replace('$', '&#36;')}</span>", unsafe_allow_html=True)
                    else:
                        retro_badge = f" <span style='color:red;font-size:10px;'>(EXTEMP)</span>" if it['retro'] == 1 else ""
                        ret_badge = f" <span style='color:orange;font-size:10px;'>(RETENÇÃO)</span>" if float(it.get('v_pis_ret', 0)) > 0 or float(it.get('v_cof_ret', 0)) > 0 else ""
                        c_txt.markdown(f"<small style='line-height: 1.2;'><b>{it['op_nome']}</b>{retro_badge}{ret_badge}<br>PIS: {formatar_moeda(it['v_pis']).replace('$', '&#36;')} | COF: {formatar_moeda(it['v_cofins']).replace('$', '&#36;')}</small>", unsafe_allow_html=True)
                        c_val.markdown(f"<span style='font-size: 14px; font-weight: 600;'>{formatar_moeda(it['v_base']).replace('$', '&#36;')}</span>", unsafe_allow_html=True)
                    
                    c_del.button("×", key=f"del_{it['id_unico']}", on_click=remover_do_rascunho, args=(i,))
                    st.markdown("<hr style='margin: 5px 0;'>", unsafe_allow_html=True)
                    
        if st.button("Gravar na Base de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos)==0):
            comp_db = validar_competencia(competencia)
            if not comp_db: st.error("Competência inválida.")
            else:
                try:
                    with get_db_cursor(commit=True) as cursor:
                        for it in st.session_state.rascunho_lancamentos:
                            query = """INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, valor_pis_retido, valor_cofins_retido, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem, num_nota, fornecedor, valor_custo_liquido, custo_conta_deb, custo_conta_cred, custo_hist_cod, custo_hist_texto, is_custo_avulso) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,'ATIVO',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
                            c_origem_db = validar_competencia(it['origem']) if it['origem'] else None
                            cursor.execute(query, (int(it['emp_id']), int(it['op_id']), comp_db, float(it['v_base']), float(it['v_pis']), float(it['v_cofins']), float(it.get('v_pis_ret', 0)), float(it.get('v_cof_ret', 0)), it['hist'], st.session_state.username, int(it['retro']), c_origem_db, it['nota'], it['fornecedor'], float(it.get('custo_liq', 0.0)), it.get('c_deb'), it.get('c_cred'), it.get('c_cod'), it.get('c_txt'), int(it.get('is_custo_avulso', 0))))
                    st.session_state.rascunho_lancamentos = []; st.success("Gravado com sucesso!"); st.rerun()
                except Exception as e: st.error(f"Erro no banco: {e}")

    st.markdown("---")
    st.markdown("#### Lançamentos Gravados nesta Competência (Auditoria DB)")
    comp_db = validar_competencia(competencia)
    if comp_db:
        try:
            with get_db_connection() as conn:
                query_gravados = "SELECT l.id, IF(l.is_custo_avulso=1, '[CUSTO AVULSO]', o.nome) as operacao, l.valor_base, l.valor_pis, l.valor_cofins, l.valor_custo_liquido, l.historico, l.usuario_registro FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = %s AND l.competencia = %s AND l.status_auditoria = 'ATIVO'"
                df_gravados = pd.read_sql(query_gravados, conn, params=(emp_id, comp_db))
            
            if df_gravados.empty:
                st.info("Nenhum lançamento ativo salvo na base de dados para esta competência.")
            else:
                st.dataframe(df_gravados, use_container_width=True, hide_index=True)
                with st.expander("Estornar / Inativar Lançamento"):
                    if st.form_submit_button("Confirmar Estorno"):
                            if not motivo or len(motivo.strip()) < 5:
                                st.error("Informe um motivo válido (mínimo 5 caracteres).")
                            else:
                                try:
                                    # Usamos o contexto de commit para garantir a gravação
                                    with get_db_cursor(commit=True) as cursor_estorno:
                                        historico_add = f" | [ESTORNADO]: {motivo}"
                                        # O segredo está em passar os parâmetros como uma tupla (valor1, valor2)
                                        sql = "UPDATE lancamentos SET status_auditoria = 'INATIVO', historico = CONCAT(IFNULL(historico,''), %s) WHERE id = %s"
                                        cursor_estorno.execute(sql, (historico_add, int(id_alvo)))
                                    
                                    st.success(f"Lançamento ID {id_alvo} inativado com sucesso!")
                                    # Pequena pausa para o usuário ver a mensagem antes de recarregar
                                    import time
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Erro ao processar estorno no banco: {e}")

# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO ---
def modulo_relatorios():
    st.markdown("### Exportação para ERP e PDF Analítico")
    df_emp = carregar_empresas_visiveis()
    if df_emp.empty:
        st.warning("Nenhuma unidade liberada para este utilizador.")
        return

    c1, c2 = st.columns([2, 1])
    emp_sel = c1.selectbox("Unidade (CNPJ)", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    emp_row = df_emp[df_emp['id'] == emp_id].iloc[0]
    competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    consolidar = st.checkbox("Consolidar apuração com Filiais (mesma Raiz CNPJ)")
    
    if st.button("Gerar Ficheiros e Analisar Saldos"):
        comp_db = validar_competencia(competencia)
        if not comp_db: st.error("Competência inválida."); return
        
        try:
            with get_db_connection() as conn:
                if consolidar:
                    raiz_cnpj = emp_row['cnpj'][:10]
                    df_ids = pd.read_sql("SELECT id FROM empresas WHERE cnpj LIKE %s", conn, params=(f"{raiz_cnpj}%",))
                    lista_ids = tuple(df_ids['id'].tolist())
                    filtro_empresa = f"l.empresa_id = {lista_ids[0]}" if len(lista_ids) == 1 else f"l.empresa_id IN {lista_ids}"
                    nome_relatorio_pdf = f"{emp_row['nome']} (CONSOLIDADO)"
                else:
                    filtro_empresa = f"l.empresa_id = {emp_id}"
                    nome_relatorio_pdf = f"{emp_row['nome']}"

                query = f"""
                    SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, e.apelido_unidade, e.tipo as emp_tipo, 
                           COALESCE(ocu.conta_deb_pis, o.conta_deb_pis) as conta_deb_pis, 
                           COALESCE(ocu.conta_cred_pis, o.conta_cred_pis) as conta_cred_pis, 
                           o.pis_h_codigo, o.pis_h_texto, 
                           COALESCE(ocu.conta_deb_cof, o.conta_deb_cof) as conta_deb_cof, 
                           COALESCE(ocu.conta_cred_cof, o.conta_cred_cof) as conta_cred_cof, 
                           o.cofins_h_codigo, o.cofins_h_texto 
                    FROM lancamentos l 
                    JOIN operacoes o ON l.operacao_id = o.id 
                    JOIN empresas e ON l.empresa_id = e.id 
                    LEFT JOIN operacoes_contas_unidade ocu ON ocu.operacao_id = o.id AND ocu.empresa_id = l.empresa_id
                    WHERE {filtro_empresa} AND l.competencia = %s AND l.status_auditoria = 'ATIVO'
                """
                df_export = pd.read_sql(query, conn, params=(comp_db,))

                query_hist = f"SELECT o.tipo as op_tipo, SUM(l.valor_pis) as t_pis, SUM(l.valor_cofins) as t_cof, SUM(l.valor_pis_retido) as t_pis_ret, SUM(l.valor_cofins_retido) as t_cof_ret FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE {filtro_empresa} AND l.competencia < %s AND l.status_auditoria = 'ATIVO' AND l.is_custo_avulso = 0 GROUP BY o.tipo"
                df_hist = pd.read_sql(query_hist, conn, params=(comp_db,))
                
                # --- PROTEÇÃO CONTRA BASE VAZIA E EXTRAÇÃO DE ANEXOS FUTUROS ---
                df_fut = pd.read_sql(f"SELECT * FROM lancamentos l WHERE {filtro_empresa} AND l.competencia_origem = %s AND l.competencia != %s AND l.status_auditoria = 'ATIVO' AND l.is_custo_avulso = 0", conn, params=(comp_db, comp_db))
            
            if df_export.empty and df_fut.empty:
                st.warning(f"Atenção: Não existem lançamentos e nem anotações de auditoria para a competência {competencia} na base de dados.")
                return

            saldo_ant_pis = 0.0; saldo_ant_cof = 0.0
            if not df_hist.empty:
                hist_deb = df_hist[df_hist['op_tipo'] == 'RECEITA']
                hist_cred = df_hist[df_hist['op_tipo'] == 'DESPESA']
                res_hist_pis = (hist_deb['t_pis'].sum() if not hist_deb.empty else 0) - (hist_cred['t_pis'].sum() if not hist_cred.empty else 0) - (hist_deb['t_pis_ret'].sum() if not hist_deb.empty else 0)
                res_hist_cof = (hist_deb['t_cof'].sum() if not hist_deb.empty else 0) - (hist_cred['t_cof'].sum() if not hist_cred.empty else 0) - (hist_deb['t_cof_ret'].sum() if not hist_deb.empty else 0)
                if res_hist_pis < 0: saldo_ant_pis = abs(res_hist_pis)
                if res_hist_cof < 0: saldo_ant_cof = abs(res_hist_cof)

            # --- EXPORTAÇÃO EXCEL E FECHO CONSOLIDADO ---
            linhas_excel = []
            if not df_export.empty:
                for _, r in df_export.iterrows():
                    
                    # --- LÓGICA DE DATA E HISTÓRICO ---
                    is_retro = r.get('origem_retroativa') == 1
                    comp_origem = r.get('competencia_origem')
                    
                    if is_retro and pd.notnull(comp_origem) and str(comp_origem).strip():
                        if '-' in str(comp_origem):
                            ano_alvo, mes_alvo = str(comp_origem).split('-')[:2]
                        else:
                            mes_alvo, ano_alvo = str(comp_origem).split('/')
                        
                        comp_exibicao = f"{int(mes_alvo):02d}/{ano_alvo}"
                        ultimo_dia = calendar.monthrange(int(ano_alvo), int(mes_alvo))[1]
                        d_str = f"{ultimo_dia:02d}/{int(mes_alvo):02d}/{ano_alvo}"
                    else:
                        comp_exibicao = competencia
                        try:
                            mes_c, ano_c = competencia.split('/')
                            ultimo_dia = calendar.monthrange(int(ano_c), int(mes_c))[1]
                            d_str = f"{ultimo_dia:02d}/{mes_c}/{ano_c}"
                        except:
                            d_str = r.get('data_lancamento').strftime('%d/%m/%Y') if pd.notnull(r.get('data_lancamento')) else ''
                    
                    doc = r.get('num_nota') or r.get('id')
                    
                    # --- LANÇAMENTOS INDIVIDUAIS ---
                    if r.get('is_custo_avulso') == 0:
                        if pd.notnull(r.get('conta_deb_pis')) and pd.notnull(r.get('conta_cred_pis')):
                            linhas_excel.append(criar_linha_erp(r.get('conta_deb_pis'), r.get('conta_cred_pis'), d_str, r.get('valor_pis', 0), r.get('pis_h_codigo'), formatar_historico_erp(r.get('pis_h_texto'), comp_exibicao), doc))
                        if pd.notnull(r.get('conta_deb_cof')) and pd.notnull(r.get('conta_cred_cof')):
                            linhas_excel.append(criar_linha_erp(r.get('conta_deb_cof'), r.get('conta_cred_cof'), d_str, r.get('valor_cofins', 0), r.get('cofins_h_codigo'), formatar_historico_erp(r.get('cofins_h_texto'), comp_exibicao), doc))
                    
                    if r.get('is_custo_avulso') == 1 and float(r.get('valor_custo_liquido', 0)) > 0:
                        h_complementar = f" - {r.get('historico')}" if r.get('historico') else ""
                        texto_final_custo = formatar_historico_erp(r.get('custo_hist_texto'), comp_exibicao) + h_complementar
                        linhas_excel.append(criar_linha_erp(r.get('custo_conta_deb'), r.get('custo_conta_cred'), d_str, r.get('valor_custo_liquido', 0), r.get('custo_hist_cod'), texto_final_custo, doc))

                # --- LÓGICA DE TRANSFERÊNCIA / FECHO MENSAL CONSOLIDADO ---
                c_transf_pis = emp_row.get('conta_transf_pis')
                c_transf_cof = emp_row.get('conta_transf_cofins')
                
                df_notas = df_export[df_export['is_custo_avulso'] == 0].copy()

                if not df_notas.empty:
                    # Protege a unidade nula
                    if 'apelido_unidade' in df_notas.columns:
                        df_notas['apelido_unidade'] = df_notas['apelido_unidade'].fillna('MATRIZ')
                    else:
                        df_notas['apelido_unidade'] = 'MATRIZ'
                    
                    # Data final da competência para o Fecho
                    try:
                        m_c, a_c = competencia.split('/')
                        u_dia = calendar.monthrange(int(a_c), int(m_c))[1]
                        data_fecho = f"{u_dia:02d}/{m_c}/{a_c}"
                    except:
                        data_fecho = ""

                    # FECHO PIS (Agrupado por débito/ativo)
                    if c_transf_pis and 'conta_deb_pis' in df_notas.columns:
                        df_pis_valido = df_notas[df_notas['conta_deb_pis'].notnull()]
                        if not df_pis_valido.empty:
                            resumo_pis = df_pis_valido.groupby(['conta_deb_pis', 'apelido_unidade'])['valor_pis'].sum().reset_index()
                            for _, row in resumo_pis.iterrows():
                                if row.get('valor_pis', 0) > 0:
                                    apelido = str(row.get('apelido_unidade', '')).upper()
                                    hist = f"Vr. transferido para apuracao do PIS n/ mes {competencia} - {apelido}"
                                    linhas_excel.append(criar_linha_erp(c_transf_pis, row['conta_deb_pis'], data_fecho, row['valor_pis'], "", hist, "FECHO"))

                    # FECHO COFINS (Agrupado por débito/ativo)
                    if c_transf_cof and 'conta_deb_cof' in df_notas.columns:
                        df_cof_valido = df_notas[df_notas['conta_deb_cof'].notnull()]
                        if not df_cof_valido.empty:
                            resumo_cof = df_cof_valido.groupby(['conta_deb_cof', 'apelido_unidade'])['valor_cofins'].sum().reset_index()
                            for _, row in resumo_cof.iterrows():
                                if row.get('valor_cofins', 0) > 0:
                                    apelido = str(row.get('apelido_unidade', '')).upper()
                                    hist = f"Vr. transferido para apuracao do COFINS n/ mes {competencia} - {apelido}"
                                    linhas_excel.append(criar_linha_erp(c_transf_cof, row['conta_deb_cof'], data_fecho, row['valor_cofins'], "", hist, "FECHO"))

            df_xlsx = pd.DataFrame(linhas_excel)
            buffer = io.BytesIO()
            colunas_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
            if df_xlsx.empty: df_xlsx = pd.DataFrame(columns=colunas_erp)
            else: df_xlsx = df_xlsx[colunas_erp]
            
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Lancamentos_Contabeis')
            
            # --- GERAÇÃO DO PDF (SOMENTE FISCAL E ANEXOS) ---
            pdf = RelatorioCrescerePDF()
            pdf.add_page(); pdf.add_cabecalho(nome_relatorio_pdf, emp_row['cnpj'], "*** DEMONSTRATIVO DE APURACAO - PIS E COFINS ***", competencia)
            
            if not df_export.empty:
                df_pdf = df_export[df_export['is_custo_avulso'] == 0]
                deb_pis = deb_cof = cred_pis = cred_cof = ret_pis = ret_cof = ext_pis = ext_cof = 0
                
                pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
                if not df_pdf.empty:
                    for _, r in df_pdf[(df_pdf['op_tipo'] == 'RECEITA') & (df_pdf['origem_retroativa'] == 0)].iterrows():
                        desc_op = r['op_nome']
                        apelido_clean = limpar_texto(r.get('apelido_unidade', ''))
                        if consolidar and r['emp_tipo'] == 'Filial': desc_op += f" ({apelido_clean or 'Filial'})"
                        pdf.cell(90, 6, desc_op[:50], 1); pdf.cell(35, 6, formatar_moeda(r.get('valor_base',0)), 1); pdf.cell(30, 6, formatar_moeda(r.get('valor_pis',0)), 1); pdf.cell(35, 6, formatar_moeda(r.get('valor_cofins',0)), 1, ln=True)
                        deb_pis += r.get('valor_pis', 0); deb_cof += r.get('valor_cofins', 0); ret_pis += r.get('valor_pis_retido', 0); ret_cof += r.get('valor_cofins_retido', 0)
                
                pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "2. INSUMOS, CREDITOS E EXTEMPORANEOS", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
                if not df_pdf.empty:
                    for _, r in df_pdf[df_pdf['op_tipo'] == 'DESPESA'].iterrows():
                        desc_op = r['op_nome']
                        apelido_clean = limpar_texto(r.get('apelido_unidade', ''))
                        if consolidar and r['emp_tipo'] == 'Filial': desc_op += f" ({apelido_clean or 'Filial'})"
                        pdf.cell(90, 6, desc_op[:50], 1); pdf.cell(35, 6, formatar_moeda(r.get('valor_base', 0)), 1); pdf.cell(30, 6, formatar_moeda(r.get('valor_pis', 0)), 1); pdf.cell(35, 6, formatar_moeda(r.get('valor_cofins', 0)), 1, ln=True)
                        if r.get('origem_retroativa') == 1: ext_pis += r.get('valor_pis', 0); ext_cof += r.get('valor_cofins', 0)
                        else: cred_pis += r.get('valor_pis', 0); cred_cof += r.get('valor_cofins', 0)
                
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
            else:
                pdf.set_font("Arial", '', 10)
                pdf.cell(0, 10, "Nao houve movimentacao de faturamento ou creditos ordinarios nesta competencia.", ln=True, align='C')

            # --- ANEXO DE AUDITORIA ---
            pdf.add_page(); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "ANEXO I - DETALHAMENTO E NOTAS DE AUDITORIA FISCAL", ln=True)
            
            if not df_export.empty:
                df_ext = df_pdf[df_pdf['origem_retroativa'] == 1]
                if not df_ext.empty:
                    pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - APROVEITAMENTO DE CREDITO EXTEMPORANEO:", ln=True); pdf.set_font("Arial", '', 8)
                    pdf.multi_cell(0, 4, "Esta apuracao inclui a apropriacao de credito tributario originado em competencia anterior, lancado tempestivamente neste periodo."); pdf.ln(2)
                    for _, r in df_ext.iterrows(): pdf.multi_cell(0, 4, f"- Origem: {r.get('competencia_origem', '')} | Doc: {r.get('num_nota', '')} - {r.get('fornecedor', '')} | PIS: {formatar_moeda(r.get('valor_pis',0))} | COF: {formatar_moeda(r.get('valor_cofins',0))}\n  Justificativa: {r.get('historico', '')}")
            
            if not df_fut.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - CREDITO APROPRIADO EXTEMPORANEAMENTE (NO FUTURO):", ln=True); pdf.set_font("Arial", '', 8)
                for _, r in df_fut.iterrows(): pdf.multi_cell(0, 4, f"Registra-se que o documento fiscal {r.get('num_nota', '')}, emitido por {r.get('fornecedor', '')} nesta competencia ({comp_db}), nao compos a base de calculo original deste demonstrativo. O respectivo credito foi apropriado extemporaneamente na competencia {r.get('competencia', '')}.\nMotivo: {r.get('historico', '')}"); pdf.ln(2)

            pdf_bytes = pdf.output(dest='S').encode('latin1', 'replace')
            st.success("Ficheiros processados com sucesso!")
            c_btn1, c_btn2, _ = st.columns([1, 1, 2])
            c_btn1.download_button("Baixar XLSX (Exportação ERP)", data=buffer.getvalue(), file_name=f"LCTOS_{comp_db}.xlsx")
            c_btn2.download_button("Baixar PDF (Demonstrativo Fiscal)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
        except Exception as e: st.error(f"Erro na geração: {e}")

# --- 7.5 MÓDULO IMOBILIZADO E DEPRECIAÇÃO ---
def modulo_imobilizado():
    st.markdown("### Gestão de Ativo Imobilizado")
    df_emp = carregar_empresas_visiveis()
    if df_emp.empty:
        st.warning("Nenhuma unidade liberada para este utilizador.")
        return
    
    c_emp, c_vazio = st.columns([2, 1])
    emp_sel = c_emp.selectbox("Unidade", df_emp.apply(formatar_nome_empresa, axis=1), key="imo_emp")
    emp_id = int(df_emp.loc[df_emp.apply(formatar_nome_empresa, axis=1) == emp_sel].iloc[0]['id'])
    row_emp_ativa = df_emp[df_emp['id'] == emp_id].iloc[0]

    st.divider()
    
    abas = ["Cadastro e Processamento", "Inventário Dinâmico"]
    if st.session_state.nivel_acesso in ["SUPER_ADMIN", "ADMIN"]: abas.append("Manutenção de Ativos (Admin)")
    
    tabs = st.tabs(abas)

    with get_db_connection() as conn:
        df_g = pd.read_sql("SELECT * FROM grupos_imobilizado WHERE tenant_id = %s", conn, params=(emp_id,))

    # --- FUNÇÃO DE FRAGMENTO (MANUTENÇÃO) ---
    if len(tabs) > 2:
        @st.fragment
        def fragmento_manutencao(emp_id_param):
            st.markdown("#### Manutenção de Ativos (Edição/Transferência/Exclusão)")
            with get_db_connection() as conn_f:
                df_todos_manut = pd.read_sql("SELECT b.*, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = %s", conn_f, params=(int(emp_id_param),))
                df_grupos_locais = pd.read_sql("SELECT * FROM grupos_imobilizado WHERE tenant_id = %s", conn_f, params=(int(emp_id_param),))
                df_plano_existe = pd.read_sql("SELECT DISTINCT bem_id FROM plano_depreciacao_itens", conn_f)
            
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
                        
                        cenario_manut = st.selectbox("Cenário de Depreciação", ["1. Bem Novo (Cálculo Automático)", "2. Cliente Novo (Sem Histórico Mensal)", "3. Continuidade (Memória Cota Fixa)"], index=idx_cenario_atual, key=f"cenario_m_{bem_id}")

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
                        
                        with st.expander("Ver Prévia Dinâmica do Plano de Voo (Resumido)", expanded=True):
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
                                if s_rest_m > 0.009: st.markdown(f"<small style='color:gray;'>*... e assim sucessivamente até zerar.*</small>", unsafe_allow_html=True)

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
                                try:
                                    with get_db_cursor(commit=True) as cursor_upd:
                                        val_dtsi = m_dtsi if ("1" not in cenario_manut) else None
                                        val_tx_cust = m_taxa_cust if m_taxa_cust > 0 else None
                                        
                                        cursor_upd.execute("""UPDATE bens_imobilizado SET grupo_id=%s, descricao_item=%s, marca_modelo=%s, num_serie_placa=%s, plaqueta=%s, localizacao=%s, numero_nota_fiscal=%s, nome_fornecedor=%s, valor_compra=%s, data_compra=%s, regra_credito=%s, data_saldo_inicial=%s, valor_residual_inicial=%s, taxa_customizada=%s, tenant_id=%s, status=%s WHERE id=%s""", (int(m_grupo_id), m_desc, m_marca, m_serie, m_plaq, m_loc, m_nf, m_forn, float(m_vaq), m_dtc, m_regra, val_dtsi, float(m_vri_calculado), val_tx_cust, int(novo_emp_id), m_status, bem_id))
                                        
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

                                    st.success("Bem atualizado com sucesso!"); st.rerun()
                                except Exception as e: st.error(f"Erro ao atualizar: {e}")

                    with c_bt_delete:
                        st.markdown('<div class="btn-excluir">', unsafe_allow_html=True)
                        if st.button("Excluir Ativo", use_container_width=True, disabled=not confirm_excluir):
                            if texto_confirma.strip().upper() == "CONFIRMO":
                                try:
                                    with get_db_cursor(commit=True) as cursor_del:
                                        cursor_del.execute("DELETE FROM plano_depreciacao_itens WHERE bem_id = %s", (bem_id,))
                                        cursor_del.execute("DELETE FROM bens_imobilizado WHERE id = %s", (bem_id,))
                                    st.success("Ativo e plano de depreciação excluídos com sucesso!"); st.rerun()
                                except Exception as e: st.error(f"Erro ao excluir: {e}")
                            else: st.error("🔒 Digite CONFIRMO para validar a exclusão.")
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
                            taxa_usada = float(taxa_custom) if taxa_custom > 0 else float(g_row['taxa_anual_percentual'])
                            cota_sugerida = round((float(v_aq) * (taxa_usada / 100.0)) / 12.0, 2)
                            st.info(f"Cota Mensal Padrão projetada: **{formatar_moeda(cota_sugerida)}**")
                            
                            if cota_sugerida > 0 and v_residual_atual > 0:
                                st.markdown("##### Grade de Conferência")
                                primeira_cota_calc = cota_sugerida
                                mes_inicio_plan = dt_saldo.month + 1 if dt_saldo.month < 12 else 1
                                ano_inicio_plan = dt_saldo.year if dt_saldo.month < 12 else dt_saldo.year + 1
                                primeira_cota_manual = st.number_input("Ajuste da 1ª Parcela (Opcional - R$)", min_value=0.0, max_value=float(v_residual_atual), value=float(primeira_cota_calc), step=10.0, key="cota_cad_manual")
                                
                                with st.expander("Ver Prévia Dinâmica do Plano de Voo", expanded=True):
                                    preview_data = []
                                    s_rest = v_residual_atual
                                    d_plan = date(ano_inicio_plan, mes_inicio_plan, 1)
                                    
                                    c_at_1 = min(s_rest, float(primeira_cota_manual))
                                    if c_at_1 > 0:
                                        preview_data.append({"Mês": d_plan.strftime('%m/%Y'), "Cota Projetada": formatar_moeda(c_at_1), "Saldo Restante": formatar_moeda(s_rest - c_at_1)})
                                        s_rest -= c_at_1
                                        m_plan = d_plan.month + 1 if d_plan.month < 12 else 1
                                        a_plan = d_plan.year if d_plan.month < 12 else d_plan.year + 1
                                        d_plan = date(a_plan, m_plan, 1)
                                    
                                    while s_rest > 0.009 and len(preview_data) < 12:
                                        c_at = min(s_rest, float(cota_sugerida))
                                        preview_data.append({"Mês": d_plan.strftime('%m/%Y'), "Cota Projetada": formatar_moeda(c_at), "Saldo Restante": formatar_moeda(s_rest - c_at)})
                                        s_rest -= c_at
                                        m_plan = d_plan.month + 1 if d_plan.month < 12 else 1
                                        a_plan = d_plan.year if d_plan.month < 12 else d_plan.year + 1
                                        d_plan = date(a_plan, m_plan, 1)
                                    
                                    if preview_data:
                                        st.dataframe(pd.DataFrame(preview_data), hide_index=True, use_container_width=True)
                                        if s_rest > 0.009: st.markdown(f"<small style='color:gray;'>*... e assim sucessivamente por mais meses até zerar.*</small>", unsafe_allow_html=True)

                                confirmacao_cad = st.checkbox("Confirmo que a memória de cálculo acima está correta e pronta para ser gravada.", key="conf_cad")
                            else: confirmacao_cad = False
                    else:
                        dt_saldo = None; v_dep_acumulada = 0.0; primeira_cota_manual = 0.0; cota_sugerida = 0.0; v_residual_atual = 0.0

                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    btn_disabled = ("3" in cenario and not confirmacao_cad)
                    if btn_disabled: st.warning("⚠️ Confirme a memória de cálculo para habilitar a gravação.")
                    
                    if st.button("Registar no Inventário", type="primary", use_container_width=True, disabled=btn_disabled):
                        if not desc or v_aq <= 0: st.error("Descrição e Valor de Aquisição são obrigatórios e devem ser maiores que zero.")
                        elif dt_c > hoje_br.date(): st.error("A Data de Compra não pode ser no futuro.")
                        elif ("1" not in cenario) and v_residual_atual <= 0 and "3" in cenario: st.error("O Valor Residual calculado zerou. Não utilize Continuidade para itens 100% depreciados.")
                        elif "3" in cenario and cota_sugerida <= 0: st.error("A cota de projeção não pode ser zero. Verifique a Alíquota ou Taxa.")
                        else:
                            try:
                                with get_db_cursor(commit=True) as cursor_c:
                                    dt_s_db = dt_saldo if ("1" not in cenario) else None
                                    v_s_db = float(v_residual_atual) if ("1" not in cenario) else 0.0
                                    tx_cust_db = float(taxa_custom) if taxa_custom > 0 else None
                                    
                                    cursor_c.execute("""INSERT INTO bens_imobilizado (tenant_id, grupo_id, descricao_item, marca_modelo, num_serie_placa, plaqueta, localizacao, numero_nota_fiscal, nome_fornecedor, data_compra, valor_compra, regra_credito, data_saldo_inicial, valor_residual_inicial, taxa_customizada) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (int(emp_id), int(g_row['id']), desc, marca, num_serie, plaqueta, localizacao, nf, forn, dt_c, float(v_aq), regra_cred, dt_s_db, v_s_db, tx_cust_db))
                                    bem_id = cursor_c.lastrowid

                                if "3" in cenario and cota_sugerida > 0 and v_s_db > 0:
                                    saldo_restante = v_s_db
                                    mes_plan = dt_saldo.month + 1 if dt_saldo.month < 12 else 1
                                    ano_plan = dt_saldo.year if dt_saldo.month < 12 else dt_saldo.year + 1
                                    data_plan = date(ano_plan, mes_plan, 1)

                                    is_first_month = True
                                    while saldo_restante > 0.009:
                                        cota_atual = min(saldo_restante, float(primeira_cota_manual) if is_first_month else float(cota_sugerida))
                                        cursor_c.execute("INSERT INTO plano_depreciacao_itens (bem_id, mes_referencia, valor_cota, tipo_registro, status_contabil) VALUES (%s, %s, %s, 'PROJETADO', 'PENDENTE')", (bem_id, data_plan.strftime('%Y-%m-%d'), cota_atual))
                                        saldo_restante -= cota_atual
                                        is_first_month = False
                                        if data_plan.month == 12: data_plan = date(data_plan.year + 1, 1, 1)
                                        else: data_plan = date(data_plan.year, data_plan.month + 1, 1)

                                st.success("Bem registado com sucesso!"); st.rerun()
                            except Exception as e: st.error(f"Erro ao salvar: {e}")

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
                    with get_db_connection() as conn_p:
                        df_bens = pd.read_sql("SELECT b.*, g.taxa_anual_percentual, g.conta_contabil_despesa, g.conta_contabil_dep_acumulada, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = %s AND b.status = 'ativo'", conn_p, params=(int(emp_id),))
                        df_planos = pd.read_sql("SELECT p.* FROM plano_depreciacao_itens p JOIN bens_imobilizado b ON p.bem_id = b.id WHERE b.tenant_id = %s", conn_p, params=(int(emp_id),))
                        if not df_planos.empty: df_planos['mes_referencia'] = pd.to_datetime(df_planos['mes_referencia']).dt.date

                    if not df_bens.empty:
                        linhas = []
                        for m_proc in sorted(meses_selecionados):
                            last_day = calendar.monthrange(a_proc, m_proc)[1]
                            dia_final_calculo = hoje_br.day if (a_proc == hoje_br.year and m_proc == hoje_br.month) else last_day
                            data_lancamento_str = f"{dia_final_calculo:02d}/{m_proc:02d}/{a_proc}"
                            data_ref_plano = date(a_proc, m_proc, 1)
                            
                            registros_calc = []
                            for _, b in df_bens.iterrows():
                                dt_base = b['data_saldo_inicial'] if pd.notnull(b.get('data_saldo_inicial')) else b['data_compra']
                                if a_proc < dt_base.year or (a_proc == dt_base.year and m_proc < dt_base.month): continue
                                
                                base_calc = float(b['valor_compra'])
                                taxa_anual = 0.0
                                if pd.notnull(b.get('taxa_customizada')) and float(b['taxa_customizada']) > 0: taxa_anual = float(b['taxa_customizada']) / 100.0
                                elif pd.notnull(b.get('taxa_anual_percentual')): taxa_anual = float(b['taxa_anual_percentual']) / 100.0

                                dep_acumulada_ant = 0.0
                                saldo_ini = float(b.get('valor_residual_inicial', 0.0))
                                plano_do_bem = df_planos[df_planos['bem_id'] == b['id']] if not df_planos.empty else pd.DataFrame()
                                
                                dt_ref_calc_ant = date(a_proc, m_proc, 1) - timedelta(days=1)
                                if not plano_do_bem.empty:
                                    dep_acumulada_ant = plano_do_bem[plano_do_bem['mes_referencia'] <= dt_ref_calc_ant]['valor_cota'].sum()
                                else:
                                    dias_totais_ant = max(0, (dt_ref_calc_ant - dt_base).days)
                                    dep_acumulada_ant = min(base_calc, (base_calc * taxa_anual / 365.0) * dias_totais_ant)
                                
                                if pd.notnull(b.get('data_saldo_inicial')): residual_ant = max(0.0, saldo_ini - dep_acumulada_ant)
                                else: residual_ant = max(0.0, base_calc - dep_acumulada_ant)
                                
                                if residual_ant <= 0.009: continue

                                cota = 0.0
                                usou_plano = False
                                
                                if not plano_do_bem.empty:
                                    plano_item = plano_do_bem[plano_do_bem['mes_referencia'] == data_ref_plano]
                                    if not plano_item.empty:
                                        cota = float(plano_item.iloc[0]['valor_cota'])
                                        usou_plano = True
                                
                                if not usou_plano:
                                    dia_inicial = dt_base.day if (a_proc == dt_base.year and m_proc == dt_base.month) else 1
                                    if metodo_calc == "Mês Comercial (30 Dias)":
                                        dias_comerciais = 30 - dia_inicial + 1 if dia_inicial > 1 else 30
                                        cota = (base_calc * taxa_anual / 360.0) * dias_comerciais
                                    else:
                                        dias_uso = max(0, dia_final_calculo - dia_inicial + 1)
                                        cota = (base_calc * taxa_anual / 365.0) * dias_uso
                                    
                                cota = min(cota, residual_ant)
                                
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
                                    if metodo_calc == "Mês Comercial (30 Dias)": hist_txt_export = f"Vr. ref. depreciação no mês {m_proc:02d}/{a_proc}"
                                    else: hist_txt_export = f"Vr. ref. depreciação no mês {m_proc:02d}/{a_proc} - {r['desc']}"
                                    linhas.append(criar_linha_erp(r['c_d_use'], r['c_c_use'], r['data_lanc'], r['cota'], "", hist_txt_export, r['nf']))
                        
                        df_xlsx = pd.DataFrame(linhas)
                        buffer = io.BytesIO()
                        colunas_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
                        if df_xlsx.empty: df_xlsx = pd.DataFrame(columns=colunas_erp)
                        else: df_xlsx = df_xlsx[colunas_erp]
                        
                        with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Depreciacao')
                        st.download_button("Baixar Planilha ERP (XLSX)", data=buffer.getvalue(), file_name=f"DEPREC_{a_proc}.xlsx")

    with tabs[1]:
        st.markdown("#### Consultar Inventário Dinâmico")
        mostrar_inativos = st.checkbox("Exibir bens inativos (baixados nos últimos 5 anos)")
        limite_anos = hoje_br.year - 5
        filtro_status = "1=1" if mostrar_inativos else "b.status = 'ativo'"
        if mostrar_inativos: filtro_status += f" AND (b.data_baixa IS NULL OR YEAR(b.data_baixa) >= {limite_anos})"

        with get_db_connection() as conn_inv:
            df_todos = pd.read_sql(f"SELECT b.*, g.taxa_anual_percentual, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = %s AND {filtro_status}", conn_inv, params=(int(emp_id),))
            df_planos_inv = pd.read_sql("SELECT p.* FROM plano_depreciacao_itens p JOIN bens_imobilizado b ON p.bem_id = b.id WHERE b.tenant_id = %s", conn_inv, params=(int(emp_id),))
            if not df_planos_inv.empty: df_planos_inv['mes_referencia'] = pd.to_datetime(df_planos_inv['mes_referencia']).dt.date

        if not df_todos.empty:
            dados_visao = []
            for _, rb in df_todos.iterrows():
                dt_base = rb['data_saldo_inicial'] if pd.notnull(rb.get('data_saldo_inicial')) else rb['data_compra']
                if rb['status'] == 'ativo': dt_ref = hoje_br.date()
                else:
                    dt_ref = rb['data_baixa'] if pd.notnull(rb.get('data_baixa')) else dt_base
                    if isinstance(dt_ref, datetime) or isinstance(dt_ref, pd.Timestamp): dt_ref = dt_ref.date()

                base_calc = float(rb['valor_compra'])
                if pd.notnull(rb.get('taxa_customizada')) and float(rb['taxa_customizada']) > 0:
                    taxa_anual = float(rb['taxa_customizada']) / 100.0; taxa_display = f"{rb['taxa_customizada']}% (Custom)"
                elif pd.notnull(rb.get('taxa_anual_percentual')):
                    taxa_anual = float(rb['taxa_anual_percentual']) / 100.0; taxa_display = f"{rb['taxa_anual_percentual']}%"
                else:
                    taxa_anual = 0.0; taxa_display = "S/ Grupo"

                saldo_ini = float(rb.get('valor_residual_inicial', 0.0)); dep_acumulada = 0.0
                plano_do_bem = df_planos_inv[df_planos_inv['bem_id'] == rb['id']] if not df_planos_inv.empty else pd.DataFrame()
                
                if not plano_do_bem.empty: dep_acumulada = plano_do_bem[plano_do_bem['mes_referencia'] <= dt_ref]['valor_cota'].sum()
                else:
                    dias_totais = max(0, (dt_ref - dt_base).days)
                    dep_acumulada = min(base_calc, (base_calc * taxa_anual / 365.0) * dias_totais)
                
                if pd.notnull(rb.get('data_saldo_inicial')): valor_residual = max(0.0, saldo_ini - dep_acumulada)
                else: valor_residual = max(0.0, base_calc - dep_acumulada)
                
                desc_limpa = limpar_texto(rb.get('descricao_item')); marca_limpa = limpar_texto(rb.get('marca_modelo'))
                dados_visao.append({"Descrição": f"{desc_limpa} {marca_limpa}".strip(), "Data Ref.": dt_base.strftime('%d/%m/%Y'), "Valor Base": formatar_moeda(rb['valor_compra']), "Taxa (%)": taxa_display, "Valor Residual": formatar_moeda(valor_residual), "Situação": rb['status'].upper()})
            
            if dados_visao: st.dataframe(pd.DataFrame(dados_visao), use_container_width=True, hide_index=True)

        st.markdown("---")
        with st.expander("🖨️ Central de Relatórios de Inventário (Auditoria e Projeção)", expanded=False):
            st.info("Gere relatórios de saldos exatos a partir da data de aquisição do ativo mais antigo ou verifique o valor residual em datas futuras para projeção de desinvestimento.")
            
            with get_db_connection() as conn_min_dt:
                cursor_min = conn_min_dt.cursor(dictionary=True)
                cursor_min.execute("SELECT MIN(data_compra) as min_c, MIN(data_saldo_inicial) as min_s FROM bens_imobilizado WHERE tenant_id = %s", (int(emp_id),))
                res_min = cursor_min.fetchone()
                
                dts = []
                if res_min:
                    if res_min['min_c']: dts.append(res_min['min_c'])
                    if res_min['min_s']: dts.append(res_min['min_s'])
                
                if dts:
                    data_minima = min(dts)
                    if isinstance(data_minima, pd.Timestamp) or isinstance(data_minima, datetime):
                        data_minima = data_minima.date()
                else:
                    data_minima = date(2024, 12, 31)

            c_filtro, c_item, c_data, c_btn = st.columns([1.5, 2, 1, 1])
            opcoes_grupos = ["Todos os Grupos"] + df_g['nome_grupo'].tolist() if not df_g.empty else ["Todos os Grupos"]
            grupo_filtro = c_filtro.selectbox("Filtrar por Grupo", opcoes_grupos, key="filtro_grupo_pdf")
            
            with get_db_connection() as conn_inv_pdf:
                if grupo_filtro != "Todos os Grupos":
                    grp_id = int(df_g[df_g['nome_grupo'] == grupo_filtro].iloc[0]['id'])
                    df_bens_filtro = pd.read_sql("SELECT id, descricao_item, numero_nota_fiscal, valor_compra, status FROM bens_imobilizado WHERE tenant_id = %s AND grupo_id = %s", conn_inv_pdf, params=(int(emp_id), grp_id))
                else:
                    df_bens_filtro = pd.read_sql("SELECT id, descricao_item, numero_nota_fiscal, valor_compra, status FROM bens_imobilizado WHERE tenant_id = %s", conn_inv_pdf, params=(int(emp_id),))
            
            lista_itens = ["Todos os Itens do Grupo/Empresa"]
            if not df_bens_filtro.empty:
                for _, r_bem in df_bens_filtro.iterrows():
                    desc_bem = limpar_texto(r_bem['descricao_item'])[:30]
                    nf_bem = f" | NF: {r_bem['numero_nota_fiscal']}" if pd.notnull(r_bem.get('numero_nota_fiscal')) and str(r_bem.get('numero_nota_fiscal')).strip() else ""
                    val_bem = f" | {formatar_moeda(r_bem['valor_compra'])}"
                    status_bem = f" ({str(r_bem['status']).upper()})"
                    lista_itens.append(f"[{r_bem['id']}] {desc_bem}{nf_bem}{val_bem}{status_bem}")
            
            item_filtro = c_item.selectbox("Ativo Específico", lista_itens, key="filtro_item_pdf")
            data_posicao = c_data.date_input("Data Base (Posição ou Projeção)", value=hoje_br.date(), min_value=data_minima, key="dt_pos_pdf")
            
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            if c_btn.button("Gerar PDF do Inventário", type="primary", use_container_width=True):
                with get_db_connection() as conn_pdf:
                    df_pdf = pd.read_sql("SELECT b.*, g.taxa_anual_percentual, g.nome_grupo FROM bens_imobilizado b LEFT JOIN grupos_imobilizado g ON b.grupo_id = g.id WHERE b.tenant_id = %s", conn_pdf, params=(int(emp_id),))
                    df_planos_pdf = pd.read_sql("SELECT p.* FROM plano_depreciacao_itens p JOIN bens_imobilizado b ON p.bem_id = b.id WHERE b.tenant_id = %s", conn_pdf, params=(int(emp_id),))
                    if not df_planos_pdf.empty: df_planos_pdf['mes_referencia'] = pd.to_datetime(df_planos_pdf['mes_referencia']).dt.date
                
                if df_pdf.empty:
                    st.warning("Nenhum bem encontrado para esta unidade.")
                else:
                    if data_posicao < hoje_br.date(): etiqueta_titulo = "[POSICAO HISTORICA - AUDITORIA]"
                    elif data_posicao > hoje_br.date(): etiqueta_titulo = "[PROJECAO DE VALOR CONTABIL - SIMULACAO]"
                    else: etiqueta_titulo = "[POSICAO ATUAL - INVENTARIO]"
                    
                    titulo_final = f"INVENTARIO DE ATIVO IMOBILIZADO\n{etiqueta_titulo}"

                    pdf_inv = RelatorioCrescerePDF()
                    pdf_inv.add_page()
                    filtro_pdf_str = grupo_filtro
                    if item_filtro != "Todos os Itens do Grupo/Empresa": filtro_pdf_str = f"Ativo Específico ID: {item_filtro.split(']')[0].replace('[','')}"
                    pdf_inv.add_cabecalho(row_emp_ativa['nome'], row_emp_ativa['cnpj'], titulo_final, f"Posicao base em: {data_posicao.strftime('%d/%m/%Y')} | Filtro: {filtro_pdf_str}")
                    
                    pdf_inv.set_font("Arial", 'B', 8)
                    pdf_inv.cell(10, 6, "ID", 1); pdf_inv.cell(65, 6, "Descricao", 1); pdf_inv.cell(20, 6, "Aquisicao", 1); pdf_inv.cell(25, 6, "Vlr. Base", 1); pdf_inv.cell(30, 6, "Dep. Acumul.", 1); pdf_inv.cell(40, 6, "Saldo Residual", 1, ln=True)
                    pdf_inv.set_font("Arial", '', 8)
                    
                    t_base = 0.0; t_dep = 0.0; t_res = 0.0
                    
                    for _, r in df_pdf.iterrows():
                        if grupo_filtro != "Todos os Grupos" and r.get('nome_grupo') != grupo_filtro: continue
                        if item_filtro != "Todos os Itens do Grupo/Empresa":
                            item_selecionado_id = int(item_filtro.split("]")[0].replace("[", ""))
                            if r['id'] != item_selecionado_id: continue
                        
                        dt_base = r['data_saldo_inicial'] if pd.notnull(r.get('data_saldo_inicial')) else r['data_compra']
                        if isinstance(dt_base, datetime) or isinstance(dt_base, pd.Timestamp): dt_base = dt_base.date()
                        
                        dt_compra_orig = r['data_compra']
                        if isinstance(dt_compra_orig, datetime) or isinstance(dt_compra_orig, pd.Timestamp): dt_compra_orig = dt_compra_orig.date()

                        if dt_compra_orig > data_posicao: continue
                        
                        if r['status'] != 'ativo' and pd.notnull(r.get('data_baixa')):
                            dt_baixa = r['data_baixa']
                            if isinstance(dt_baixa, datetime) or isinstance(dt_baixa, pd.Timestamp): dt_baixa = dt_baixa.date()
                            if dt_baixa <= data_posicao: continue

                        base_calc = float(r['valor_compra'])
                        if pd.notnull(r.get('taxa_customizada')) and float(r['taxa_customizada']) > 0: taxa_anual = float(r['taxa_customizada']) / 100.0
                        elif pd.notnull(r.get('taxa_anual_percentual')): taxa_anual = float(r['taxa_anual_percentual']) / 100.0
                        else: taxa_anual = 0.0
                        
                        saldo_ini = float(r.get('valor_residual_inicial', 0.0))
                        dep_acumulada = 0.0
                        plano_do_bem = df_planos_pdf[df_planos_pdf['bem_id'] == r['id']] if not df_planos_pdf.empty else pd.DataFrame()
                        
                        if not plano_do_bem.empty:
                            dep_acumulada = plano_do_bem[plano_do_bem['mes_referencia'] <= data_posicao]['valor_cota'].sum()
                        else:
                            if dt_base > data_posicao: dep_acumulada = 0.0
                            else:
                                dias_totais = max(0, (data_posicao - dt_base).days)
                                dep_acumulada = min(base_calc, (base_calc * taxa_anual / 365.0) * dias_totais)
                        
                        if pd.notnull(r.get('data_saldo_inicial')):
                            if data_posicao < dt_base:
                                valor_residual = base_calc; dep_acumulada = 0.0
                            else: valor_residual = max(0.0, saldo_ini - dep_acumulada)
                        else: 
                            valor_residual = max(0.0, base_calc - dep_acumulada)
                            
                        if dep_acumulada > base_calc: dep_acumulada = base_calc

                        desc_limpa = limpar_texto(r.get('descricao_item'))[:35]
                        
                        pdf_inv.cell(10, 6, str(r['id']), 1); pdf_inv.cell(65, 6, desc_limpa, 1); pdf_inv.cell(20, 6, dt_compra_orig.strftime('%d/%m/%Y'), 1); pdf_inv.cell(25, 6, formatar_moeda(base_calc), 1); pdf_inv.cell(30, 6, formatar_moeda(dep_acumulada), 1); pdf_inv.cell(40, 6, formatar_moeda(valor_residual), 1, ln=True)
                        
                        t_base += base_calc; t_dep += dep_acumulada; t_res += valor_residual
                        
                    pdf_inv.set_font("Arial", 'B', 9)
                    pdf_inv.cell(95, 8, "TOTAIS DA POSICAO", 1)
                    pdf_inv.cell(25, 8, formatar_moeda(t_base), 1)
                    pdf_inv.cell(30, 8, formatar_moeda(t_dep), 1)
                    pdf_inv.cell(40, 8, formatar_moeda(t_res), 1, ln=True)
                    
                    pdf_bytes_inv = pdf_inv.output(dest='S').encode('latin1', 'replace') # Correção 'replace'
                    st.session_state['pdf_inv_b64'] = pdf_bytes_inv
                    st.session_state['pdf_inv_nome'] = f"Inventario_{row_emp_ativa['apelido_unidade'] or row_emp_ativa['id']}_{data_posicao.strftime('%m%Y')}.pdf"

            if 'pdf_inv_b64' in st.session_state:
                st.success("Relatório processado e pronto para download!")
                st.download_button("⬇️ Baixar Arquivo PDF", data=st.session_state['pdf_inv_b64'], file_name=st.session_state['pdf_inv_nome'], mime="application/pdf", use_container_width=True)

    if len(tabs) > 2:
        with tabs[2]:
            fragmento_manutencao(emp_id)

# --- 8. MÓDULO PARÂMETROS CONTÁBEIS ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": st.error("Acesso restrito."); return
    st.markdown("### Parâmetros Contábeis e Exportação ERP")
    df_op = carregar_operacoes()
    op_nomes = df_op['nome'].tolist()
    
    tab_edit, tab_novo, tab_vinculo, tab_custo, tab_fecho, tab_limpeza, tab_imob = st.tabs([
        "Editar Global", "Nova Operação", "Vínculo Contábil (Matriz/Filial)", 
        "Destinos Custo", "Fecho Mensal", "Auditoria", "Grupos Imobilizado"
    ])
    
    with tab_edit:
        st.info("Edite os parâmetros GLOBAIS. Para contas específicas de filiais, use a aba 'Vínculo Contábil'.")
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

            if st.form_submit_button("Atualizar Operação Global"):
                try:
                    with get_db_cursor(commit=True) as cursor:
                        cursor.execute("""UPDATE operacoes SET conta_deb_pis=%s, conta_cred_pis=%s, pis_h_codigo=%s, pis_h_texto=%s, conta_deb_cof=%s, conta_cred_cof=%s, cofins_h_codigo=%s, cofins_h_texto=%s WHERE id=%s""", (p_deb, p_cred, p_cod, p_txt, c_deb, c_cred, c_cod, c_txt, int(oid)))
                    carregar_operacoes.clear(); st.success("Atualizado!"); st.rerun()
                except Exception as e: st.error(f"Erro: {e}")

    with tab_novo:
        with st.form("form_nova_op", clear_on_submit=True):
            c_nome, c_tipo = st.columns([3, 1])
            novo_nome = c_nome.text_input("Nome da Nova Operação")
            novo_tipo = c_tipo.selectbox("Natureza", ["RECEITA", "DESPESA"])
            st.markdown("##### Configuração PIS")
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2]); n_p_deb = c1.text_input("Débito PIS", key="n_pd"); n_p_cred = c2.text_input("Crédito PIS", key="n_pc"); n_p_cod = c3.text_input("Cód ERP PIS", key="n_pcd"); n_p_txt = c4.text_input("Texto Padrão PIS", key="n_ptx")
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2]); n_c_deb = c5.text_input("Débito COFINS", key="n_cd"); n_c_cred = c6.text_input("Crédito COFINS", key="n_cc"); n_c_cod = c7.text_input("Cód ERP COFINS", key="n_ccd"); n_c_txt = c8.text_input("Texto Padrão COF", key="n_ctx")
            st.divider()
            
            if st.form_submit_button("Registar Nova Operação"):
                if not novo_nome: st.error("O nome é obrigatório.")
                else:
                    nome_limpo = novo_nome.strip().lower()
                    if any(o.strip().lower() == nome_limpo for o in op_nomes): st.error(f"Erro: Já existe uma operação chamada '{novo_nome}'.")
                    else:
                        try:
                            with get_db_cursor(commit=True) as cursor:
                                query_insert = """INSERT INTO operacoes (nome, tipo, conta_deb_pis, conta_cred_pis, pis_h_codigo, pis_h_texto, conta_deb_cof, conta_cred_cof, cofins_h_codigo, cofins_h_texto, ret_pis_conta_deb, ret_pis_conta_cred, ret_pis_h_codigo, ret_pis_h_texto, ret_cofins_conta_deb, ret_cofins_conta_cred, ret_cofins_h_codigo, ret_cofins_h_texto) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"""
                                valores = (novo_nome, novo_tipo, n_p_deb, n_p_cred, n_p_cod, n_p_txt, n_c_deb, n_c_cred, n_c_cod, n_c_txt)
                                cursor.execute(query_insert, valores)
                            carregar_operacoes.clear(); st.success("Nova operação registada com sucesso!"); st.rerun()
                        except Exception as e: st.error(f"Erro ao salvar: {e}")

    with tab_vinculo:
        st.markdown("##### Vínculo Contábil Específico por Unidade")
        st.info("Utilize esta aba para sobrescrever as contas padrão de uma Operação para uma Filial específica.")
        df_emp_v = carregar_empresas_visiveis()
        if not df_emp_v.empty:
            c_emp_v, c_op_v = st.columns(2)
            emp_sel_v = c_emp_v.selectbox("Empresa / Filial", df_emp_v.apply(formatar_nome_empresa, axis=1), key="sel_emp_vinc")
            emp_id_v = int(df_emp_v.loc[df_emp_v.apply(formatar_nome_empresa, axis=1) == emp_sel_v].iloc[0]['id'])
            
            op_sel_v = c_op_v.selectbox("Operação", df_op['nome'].tolist(), key="sel_op_vinc")
            op_id_v = int(df_op[df_op['nome'] == op_sel_v].iloc[0]['id'])
            
            with get_db_connection() as conn_v:
                df_vinculo = pd.read_sql("SELECT * FROM operacoes_contas_unidade WHERE empresa_id = %s AND operacao_id = %s", conn_v, params=(emp_id_v, op_id_v))
            
            row_v = df_vinculo.iloc[0] if not df_vinculo.empty else pd.Series()
            
            with st.form("form_vinculo"):
                st.markdown(f"**Contas exclusivas de '{op_sel_v}' para a unidade '{emp_sel_v}'**")
                c_v1, c_v2 = st.columns(2)
                v_dp = c_v1.text_input("Débito PIS (Específico)", value=limpar_texto(row_v.get('conta_deb_pis')))
                v_cp = c_v2.text_input("Crédito PIS (Específico)", value=limpar_texto(row_v.get('conta_cred_pis')))
                c_v3, c_v4 = st.columns(2)
                v_dc = c_v3.text_input("Débito COFINS (Específico)", value=limpar_texto(row_v.get('conta_deb_cof')))
                v_cc = c_v4.text_input("Crédito COFINS (Específico)", value=limpar_texto(row_v.get('conta_cred_cof')))
                
                c_b1, c_b2 = st.columns([1, 1])
                if c_b1.form_submit_button("Gravar Vínculo Específico", type="primary"):
                    try:
                        with get_db_cursor(commit=True) as cur_v:
                            cur_v.execute("""
                                INSERT INTO operacoes_contas_unidade (empresa_id, operacao_id, conta_deb_pis, conta_cred_pis, conta_deb_cof, conta_cred_cof)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE conta_deb_pis=VALUES(conta_deb_pis), conta_cred_pis=VALUES(conta_cred_pis), conta_deb_cof=VALUES(conta_deb_cof), conta_cred_cof=VALUES(conta_cred_cof)
                            """, (emp_id_v, op_id_v, v_dp, v_cp, v_dc, v_cc))
                        st.success("Vínculo salvo! A integração usará estas contas para esta unidade."); st.rerun()
                    except Exception as e: st.error(f"Erro: {e}")
                    
                if not df_vinculo.empty and c_b2.form_submit_button("Remover Vínculo (Usar Padrão)"):
                    try:
                        with get_db_cursor(commit=True) as cur_vdel:
                            cur_vdel.execute("DELETE FROM operacoes_contas_unidade WHERE empresa_id = %s AND operacao_id = %s", (emp_id_v, op_id_v))
                        st.success("Vínculo removido. A integração voltará a usar as contas globais."); st.rerun()
                    except Exception as e: st.error(f"Erro: {e}")

    with tab_custo:
        st.markdown("##### Destinos de Custo Líquido por Empresa")
        df_emp_c = carregar_empresas_visiveis()
        if not df_emp_c.empty:
            emp_sel_c = st.selectbox("Selecione a Empresa", df_emp_c.apply(formatar_nome_empresa, axis=1), key="sel_emp_custos")
            emp_id_c = int(df_emp_c.loc[df_emp_c.apply(formatar_nome_empresa, axis=1) == emp_sel_c].iloc[0]['id'])
            
            with get_db_connection() as conn_c:
                df_destinos = pd.read_sql("SELECT * FROM destinos_custo WHERE empresa_id = %s", conn_c, params=(emp_id_c,))
            
            if df_destinos.empty:
                st.warning("Nenhum destino de custo configurado para esta empresa.")
            else:
                for _, r_dest in df_destinos.iterrows():
                    col_d1, col_d2 = st.columns([5, 1])
                    col_d1.markdown(f"**{r_dest['nome_destino']}** | Débito: {r_dest['conta_debito']} | Crédito: {r_dest['conta_credito']} | Histórico: {r_dest['hist_texto']}")
                    if col_d2.button("Excluir", key=f"del_dest_{r_dest['id']}"):
                        with get_db_cursor(commit=True) as cur_del:
                            cur_del.execute("DELETE FROM destinos_custo WHERE id = %s", (r_dest['id'],))
                        st.rerun()
                st.divider()

            st.markdown("###### Adicionar Novo Destino de Custo")
            with st.form("form_novo_destino_custo", clear_on_submit=True):
                n_destino = st.text_input("Nome/Identificação (Ex: CMV - Mercadorias)")
                c_d1, c_d2, c_d3, c_d4 = st.columns([1, 1, 1, 2])
                n_cd = c_d1.text_input("Conta Débito Custo")
                n_cc = c_d2.text_input("Conta Crédito Custo")
                n_hc = c_d3.text_input("Cód ERP")
                n_ht = c_d4.text_input("Texto Padrão Custo")
                
                if st.form_submit_button("Salvar Destino de Custo"):
                    if not n_destino or not n_cd or not n_cc: st.error("Obrigatório: Nome e Contas.")
                    else:
                        try:
                            with get_db_cursor(commit=True) as cur_ins:
                                cur_ins.execute("INSERT INTO destinos_custo (empresa_id, nome_destino, conta_debito, conta_credito, hist_codigo, hist_texto) VALUES (%s,%s,%s,%s,%s,%s)", (emp_id_c, n_destino, n_cd, n_cc, n_hc, n_ht))
                            st.success("Salvo!"); st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")

    with tab_fecho:
        st.markdown("##### Contas de Transferência / Fecho Mensal")
        df_emp_f = carregar_empresas_visiveis()
        if not df_emp_f.empty:
            with st.form("form_fecho"):
                emp_sel_f = st.selectbox("Selecione a Empresa", df_emp_f.apply(formatar_nome_empresa, axis=1), key="sel_emp_fecho")
                emp_id_f = int(df_emp_f.loc[df_emp_f.apply(formatar_nome_empresa, axis=1) == emp_sel_f].iloc[0]['id'])
                row_emp_f = df_emp_f[df_emp_f['id'] == emp_id_f].iloc[0]
                
                c1, c2 = st.columns(2)
                t_pis = c1.text_input("Conta Transferência PIS", value=limpar_texto(row_emp_f.get('conta_transf_pis')))
                t_cofins = c2.text_input("Conta Transferência COFINS", value=limpar_texto(row_emp_f.get('conta_transf_cofins')))
                
                if st.form_submit_button("Salvar Contas de Fecho"):
                    try:
                        with get_db_cursor(commit=True) as cursor:
                            cursor.execute("UPDATE empresas SET conta_transf_pis=%s, conta_transf_cofins=%s WHERE id=%s", (t_pis, t_cofins, int(emp_id_f)))
                        carregar_empresas_ativas.clear(); carregar_empresas_visiveis.clear()
                        st.success("Atualizado com sucesso!"); st.rerun()
                    except Exception as e: st.error(f"Erro ao atualizar as contas de fecho: {e}")

    with tab_limpeza:
        st.markdown("#### Verificação de Integridade de Operações")
        if st.button("Executar Auditoria de Operações"):
            with get_db_cursor(dictionary=True) as cursor:
                cursor.execute("SELECT o.id, o.nome, o.tipo, (SELECT COUNT(*) FROM lancamentos l WHERE l.operacao_id = o.id) as total_usado FROM operacoes o ORDER BY o.nome")
                ops = cursor.fetchall()
            st.write("---")
            vistos = {}; duplicados = []
            for o in ops:
                n = o['nome'].strip().lower()
                if n in vistos: duplicados.append((o, vistos[n]))
                else: vistos[n] = o
            if not duplicados: st.success("Nenhuma duplicidade encontrada.")
            else:
                for d, original in duplicados:
                    c1, c2 = st.columns([4, 1])
                    c1.warning(f"DUPLICADO: '{d['nome']}' (ID: {d['id']}) - Usado {d['total_usado']} vezes.")
                    if d['total_usado'] == 0:
                        if c2.button("Excluir", key=f"excl_{d['id']}"):
                            with get_db_cursor(commit=True) as cursor:
                                cursor.execute("DELETE FROM operacoes WHERE id=%s", (int(d['id']),))
                            carregar_operacoes.clear(); st.rerun()

    with tab_imob:
        st.markdown("##### Grupos de Depreciação")
        df_e = carregar_empresas_visiveis()
        if df_e.empty: st.warning("Nenhuma unidade encontrada."); return
        
        e_sel = st.selectbox("Selecione a Empresa para Gerir Grupos", df_e.apply(formatar_nome_empresa, axis=1), key="sel_emp_grp")
        e_id = int(df_e.loc[df_e.apply(formatar_nome_empresa, axis=1) == e_sel].iloc[0]['id'])
        
        with get_db_connection() as conn:
            df_g = pd.read_sql("SELECT * FROM grupos_imobilizado WHERE tenant_id = %s", conn, params=(int(e_id),))
        
        with st.expander("🔄 Clonar Grupos de Outra Unidade", expanded=False):
            st.info("Utilize esta opção para copiar rapidamente os grupos (e suas contas) de uma empresa já configurada para a empresa atual.")
            emp_clonar_sel = st.selectbox("Copiar parâmetros da Empresa:", df_e[df_e['id'] != e_id].apply(formatar_nome_empresa, axis=1))
            if st.button("Iniciar Clonagem", type="primary"):
                if emp_clonar_sel:
                    id_origem = int(df_e.loc[df_e.apply(formatar_nome_empresa, axis=1) == emp_clonar_sel].iloc[0]['id'])
                    with get_db_connection() as conn_origem:
                        df_origem = pd.read_sql("SELECT * FROM grupos_imobilizado WHERE tenant_id = %s", conn_origem, params=(id_origem,))
                    
                    if df_origem.empty: st.warning("A empresa de origem não possui grupos cadastrados.")
                    else:
                        with get_db_cursor(commit=True) as cursor:
                            for _, r in df_origem.iterrows():
                                if not df_g.empty and r['nome_grupo'] in df_g['nome_grupo'].tolist(): continue
                                cursor.execute("INSERT INTO grupos_imobilizado (tenant_id, nome_grupo, taxa_anual_percentual, conta_contabil_despesa, conta_contabil_dep_acumulada) VALUES (%s,%s,%s,%s,%s)", (int(e_id), r['nome_grupo'], float(r['taxa_anual_percentual']), r['conta_contabil_despesa'], r['conta_contabil_dep_acumulada']))
                        st.success("Grupos clonados com sucesso!"); st.rerun()

        st.divider()
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
                        with get_db_cursor(commit=True) as cursor:
                            cursor.execute("UPDATE grupos_imobilizado SET nome_grupo=%s, taxa_anual_percentual=%s, conta_contabil_despesa=%s, conta_contabil_dep_acumulada=%s WHERE id=%s", (n_g, float(tx), cd, cc, int(g_row['id'])))
                        st.success("Atualizado!"); st.rerun()
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
                        with get_db_cursor(commit=True) as cursor:
                            cursor.execute("INSERT INTO grupos_imobilizado (tenant_id, nome_grupo, taxa_anual_percentual, conta_contabil_despesa, conta_contabil_dep_acumulada) VALUES (%s,%s,%s,%s,%s)", (int(e_id), n_g_n, float(tx_n), cd_n, cc_n))
                        st.success("Criado!"); st.rerun()

# --- 9. GESTÃO DE UTILIZADORES (MULTIEMPRESA) ---
def modulo_usuarios():
    if st.session_state.nivel_acesso not in ["SUPER_ADMIN", "ADMIN"]: 
        st.error("Acesso restrito.")
        return
    
    st.markdown("### Gestão de Utilizadores")
    with get_db_connection() as conn:
        if st.session_state.nivel_acesso == "SUPER_ADMIN":
            df_users = pd.read_sql("SELECT id, nome, username, nivel_acesso, status_usuario, data_criacao, contabilidade_id FROM usuarios ORDER BY nome ASC", conn)
        else:
            df_users = pd.read_sql("SELECT id, nome, username, nivel_acesso, status_usuario, data_criacao, contabilidade_id FROM usuarios WHERE contabilidade_id = %s ORDER BY nome ASC", conn, params=(int(st.session_state.contabilidade_id),))
        
        df_empresas = carregar_empresas_ativas()
    
    tab_lista, tab_novo, tab_perm = st.tabs(["Utilizadores Registados", "Adicionar Utilizador", "Permissões por Empresa"])
    
    with tab_lista:
        st.dataframe(df_users, use_container_width=True, hide_index=True)
        st.markdown("##### Gerir Acesso Base")
        with st.form("form_gestao_usuario"):
            c1, c2 = st.columns([2, 1])
            usr_sel = c1.selectbox("Selecione o Utilizador", df_users['username'].tolist() if not df_users.empty else [])
            nova_acao = c2.selectbox("Ação", ["Inativar Acesso", "Reativar Acesso", "Redefinir Palavra-passe"])
            nova_senha = st.text_input("Nova Palavra-passe (se aplicável)", type="password")
            
            if st.form_submit_button("Executar Ação"):
                try:
                    with get_db_cursor(commit=True) as cursor:
                        if nova_acao == "Inativar Acesso": cursor.execute("UPDATE usuarios SET status_usuario = 'INATIVO' WHERE username = %s", (usr_sel,))
                        elif nova_acao == "Reativar Acesso": cursor.execute("UPDATE usuarios SET status_usuario = 'ATIVO' WHERE username = %s", (usr_sel,))
                        elif nova_acao == "Redefinir Palavra-passe":
                            if len(nova_senha) < 6: st.error("A senha deve ter pelo menos 6 caracteres.")
                            else: cursor.execute("UPDATE usuarios SET senha_hash = %s WHERE username = %s", (gerar_hash_senha(nova_senha), usr_sel))
                    import time; time.sleep(1.2); st.rerun()
                except Exception as e: st.error(f"Erro no banco: {e}")
                    
    with tab_novo:
        st.markdown("##### Registar Novo Utilizador")
        with st.form("form_novo_usuario", clear_on_submit=True):
            c_u1, c_u2 = st.columns(2)
            n_nome = c_u1.text_input("Nome Completo")
            n_user = c_u2.text_input("Username (Login)")
            
            c_u3, c_u4 = st.columns(2)
            n_senha = c_u3.text_input("Palavra-passe", type="password")
            
            opcoes_nivel = ["CLIENT_OPERATOR", "ADMIN"]
            if st.session_state.nivel_acesso == "SUPER_ADMIN":
                opcoes_nivel.append("SUPER_ADMIN")
            n_nivel = c_u4.selectbox("Nível de Acesso", opcoes_nivel)
            
            if st.form_submit_button("Registar Utilizador", type="primary"):
                if not n_nome or not n_user or not n_senha:
                    st.error("Nome, Username e Palavra-passe são obrigatórios.")
                elif len(n_senha) < 6:
                    st.error("A palavra-passe deve ter pelo menos 6 caracteres.")
                else:
                    try:
                        with get_db_cursor(commit=True) as cur_usr:
                            cur_usr.execute("SELECT id FROM usuarios WHERE username = %s", (n_user,))
                            if cur_usr.fetchone():
                                st.error("Este username já está em uso.")
                            else:
                                contab_id_novo = st.session_state.contabilidade_id if st.session_state.contabilidade_id else None
                                cur_usr.execute(
                                    "INSERT INTO usuarios (nome, username, senha_hash, nivel_acesso, status_usuario, contabilidade_id) VALUES (%s, %s, %s, %s, 'ATIVO', %s)",
                                    (n_nome, n_user, gerar_hash_senha(n_senha), n_nivel, contab_id_novo)
                                )
                        st.success("Utilizador registado com sucesso!")
                        import time; time.sleep(1); st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao registar: {e}")

    with tab_perm:
        st.markdown("##### Gerir Permissões Multiempresa")
        if not df_users.empty and not df_empresas.empty:
            usr_sel_perm = st.selectbox("Utilizador Alvo", df_users.apply(lambda r: f"{r['nome']} ({r['username']})", axis=1))
            usr_row = df_users.iloc[df_users.apply(lambda r: f"{r['nome']} ({r['username']})", axis=1) == usr_sel_perm].iloc[0]
            
            u_id = int(usr_row['id'])
            u_contab = int(usr_row['contabilidade_id']) if pd.notnull(usr_row.get('contabilidade_id')) else (int(st.session_state.contabilidade_id) if st.session_state.contabilidade_id else None)
            
            with get_db_connection() as conn_perm:
                df_perms_atuais = pd.read_sql("SELECT empresa_id FROM usuario_empresas WHERE usuario_id = %s AND status = 'ATIVO'", conn_perm, params=(u_id,))
            
            ids_atuais = [int(i) for i in df_perms_atuais['empresa_id'].tolist()] if not df_perms_atuais.empty else []
            nomes_empresas = df_empresas.apply(formatar_nome_empresa, axis=1).tolist()
            empresas_pre_selecionadas = df_empresas[df_empresas['id'].isin(ids_atuais)].apply(formatar_nome_empresa, axis=1).tolist()
            
            with st.form("form_perm"):
                empresas_selecionadas = st.multiselect("Empresas Permitidas", options=nomes_empresas, default=empresas_pre_selecionadas)
                
                if st.form_submit_button("Salvar Permissões", type="primary"):
                    ids_selecionados = [int(float(i)) for i in df_empresas[df_empresas.apply(formatar_nome_empresa, axis=1).isin(empresas_selecionadas)]['id'].tolist()]
                    
                    try:
                        with get_db_cursor(commit=True) as cursor_perm:
                            for eid in ids_selecionados:
                                query_upsert = """
                                    INSERT INTO usuario_empresas (contabilidade_id, usuario_id, empresa_id, status, concedido_por) 
                                    VALUES (%s, %s, %s, 'ATIVO', %s) 
                                    ON DUPLICATE KEY UPDATE status='ATIVO', concedido_por=VALUES(concedido_por)
                                """
                                cursor_perm.execute(query_upsert, (int(u_contab) if u_contab else None, int(u_id), int(eid), 'ATIVO', int(st.session_state.usuario_id)))
                            
                            ids_revogados = [int(eid) for eid in ids_atuais if eid not in ids_selecionados]
                            if ids_revogados:
                                format_strings = ','.join(['%s'] * len(ids_revogados))
                                query_revoke = f"UPDATE usuario_empresas SET status='INATIVO', concedido_por=%s WHERE usuario_id=%s AND empresa_id IN ({format_strings})"
                                params_revoke = [int(st.session_state.usuario_id), int(u_id)] + ids_revogados
                                cursor_perm.execute(query_revoke, params_revoke)
                        
                        carregar_empresas_visiveis.clear()
                        st.success("Permissões salvas com sucesso!")
                        import time; time.sleep(1); st.rerun()
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

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
    
    if st.session_state.nivel_acesso in ["SUPER_ADMIN", "ADMIN"]:
        modulos_disp = ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação", "Parâmetros Contábeis", "Gestão de Utilizadores"]
    else:
        modulos_disp = ["Apuração Mensal", "Relatórios e Integração", "Imobilizado & Depreciação"]
        
    menu = st.radio("Módulos", modulos_disp)
    
    st.write("---")
    
    # --- NOVO BOTÃO ESTRATÉGICO ---
    st.markdown("##### Sistemas Integrados")
    st.link_button("📊 Auditoria de Vendas", "https://conciliador-contabil-hsppms6xpbjstvmmfktgkc.streamlit.app/", use_container_width=True, help="Acessar o sistema de conciliação e auditoria de vendas")
    st.markdown("<br>", unsafe_allow_html=True)
    
    if st.button("Encerrar Sessão", use_container_width=True): st.session_state.autenticado = False; st.rerun()

# --- 11. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "Imobilizado & Depreciação": modulo_imobilizado()
elif menu == "Parâmetros Contábeis": modulo_parametros()
elif menu == "Gestão de Utilizadores": modulo_usuarios()
