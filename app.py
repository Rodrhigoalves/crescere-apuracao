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
    .stButton>button, .stDownloadButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; width: 100%; transition: all 0.2s; }
    .stButton>button:hover, .stDownloadButton>button:hover { background-color: #003366; color: white; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
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

# --- FUNÇÃO PADRÃO PARA EXPORTAÇÃO ALTERDATA ---
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
            pool_size=5,
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

# --- 3. MOTOR DE CÁLCULO ---
def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido": return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

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
                    st.session_state.form_key += 1; st.rerun()

        with tab_custo:
            st.info("Utilize esta aba para apropriar o custo cheio do estoque ou serviço consumido no mês. O sistema extrairá os impostos recuperáveis para gerar a linha contábil Alterdata, sem afetar o PDF de apuração.")
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
                        c_txt.markdown(f"<small style='line-height: 1.2; color: #16a34a;'><b>{it['op_nome']}</b><br>Custo Líquido p/ Alterdata: {formatar_moeda(it['custo_liq']).replace('$', '&#36;')}<br><span style='color:#64748b;'>Doc: {it['nota'] or 'N/A'}</span></small>", unsafe_allow_html=True)
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
                    with st.form("form_edicao_lancamento"):
                        c_id, c_motivo = st.columns([1, 3])
                        id_alvo = c_id.selectbox("ID do Lançamento", df_gravados['id'].tolist())
                        motivo = c_motivo.text_input("Motivo do Estorno (Obrigatório)")
                        if st.form_submit_button("Confirmar Estorno"):
                            if not motivo or len(motivo.strip()) < 5: st.error("Informe um motivo válido.")
                            else:
                                with get_db_cursor(commit=True) as cursor:
                                    historico_add = f" | [ESTORNADO]: {motivo}"
                                    cursor.execute("UPDATE lancamentos SET status_auditoria = 'INATIVO', historico = CONCAT(IFNULL(historico,''), %s) WHERE id = %s", (historico_add, int(id_alvo)))
                                st.success("Lançamento inativado."); st.rerun()
        except Exception as e:
            st.error(f"Erro ao consultar: {e}")

# --- 7. MÓDULO RELATÓRIOS E INTEGRAÇÃO ---
def modulo_relatorios():
    st.markdown("### Exportação para Alterdata e PDF Analítico")
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

                # Query ajustada para buscar contas da filial em operacoes_contas_unidade, com fallback para as globais da tabela operacoes
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
            
            saldo_ant_pis = 0.0; saldo_ant_cof = 0.0
            if not df_hist.empty:
                hist_deb = df_hist[df_hist['op_tipo'] == 'RECEITA']
                hist_cred = df_hist[df_hist['op_tipo'] == 'DESPESA']
                res_hist_pis = (hist_deb['t_pis'].sum() if not hist_deb.empty else 0) - (hist_cred['t_pis'].sum() if not hist_cred.empty else 0) - (hist_deb['t_pis_ret'].sum() if not hist_deb.empty else 0)
                res_hist_cof = (hist_deb['t_cof'].sum() if not hist_deb.empty else 0) - (hist_cred['t_cof'].sum() if not hist_cred.empty else 0) - (hist_deb['t_cof_ret'].sum() if not hist_deb.empty else 0)
                if res_hist_pis < 0: saldo_ant_pis = abs(res_hist_pis)
                if res_hist_cof < 0: saldo_ant_cof = abs(res_hist_cof)

            # --- EXPORTAÇÃO EXCEL (TODOS) ---
            linhas_excel = []
            if not df_export.empty:
                for _, r in df_export.iterrows():
                    d_str = r['data_lancamento'].strftime('%d/%m/%Y') if pd.notnull(r['data_lancamento']) else ''
                    doc = r['num_nota'] or r['id']
                    
                    if r.get('is_custo_avulso') == 0:
                        if pd.notnull(r['conta_deb_pis']) and pd.notnull(r['conta_cred_pis']):
                            linhas_excel.append(criar_linha_erp(r['conta_deb_pis'], r['conta_cred_pis'], d_str, r['valor_pis'], r.get('pis_h_codigo'), formatar_historico_erp(r.get('pis_h_texto'), competencia), doc))
                        if pd.notnull(r['conta_deb_cof']) and pd.notnull(r['conta_cred_cof']):
                            linhas_excel.append(criar_linha_erp(r['conta_deb_cof'], r['conta_cred_cof'], d_str, r['valor_cofins'], r.get('cofins_h_codigo'), formatar_historico_erp(r.get('cofins_h_texto'), competencia), doc))
                    
                    if r.get('is_custo_avulso') == 1 and float(r.get('valor_custo_liquido', 0)) > 0:
                        h_complementar = f" - {r['historico']}" if r.get('historico') else ""
                        texto_final_custo = formatar_historico_erp(r.get('custo_hist_texto'), competencia) + h_complementar
                        linhas_excel.append(criar_linha_erp(r['custo_conta_deb'], r['custo_conta_cred'], d_str, r['valor_custo_liquido'], r.get('custo_hist_cod'), texto_final_custo, doc))
            
            df_xlsx = pd.DataFrame(linhas_excel)
            buffer = io.BytesIO()
            colunas_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
            if df_xlsx.empty: df_xlsx = pd.DataFrame(columns=colunas_erp)
            else: df_xlsx = df_xlsx[colunas_erp]
            
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_xlsx.to_excel(writer, index=False, sheet_name='Lancamentos_Contabeis')
            
            # --- GERAÇÃO DO PDF (SOMENTE FISCAL) ---
            df_pdf = df_export[df_export['is_custo_avulso'] == 0]
            
            pdf = RelatorioCrescerePDF()
            pdf.add_page(); pdf.add_cabecalho(nome_relatorio_pdf, emp_row['cnpj'], "*** DEMONSTRATIVO DE APURACAO - PIS E COFINS ***", competencia)
            deb_pis = deb_cof = cred_pis = cred_cof = ret_pis = ret_cof = ext_pis = ext_cof = 0
            
            pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "1. BASE DE CALCULO DAS RECEITAS (DEBITOS)", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
            if not df_pdf.empty:
                for _, r in df_pdf[(df_pdf['op_tipo'] == 'RECEITA') & (df_pdf['origem_retroativa'] == 0)].iterrows():
                    desc_op = r['op_nome']
                    apelido_clean = limpar_texto(r.get('apelido_unidade', ''))
                    if consolidar and r['emp_tipo'] == 'Filial': desc_op += f" ({apelido_clean or 'Filial'})"
                    pdf.cell(90, 6, desc_op[:50], 1); pdf.cell(35, 6, formatar_moeda(r['valor_base']), 1); pdf.cell(30, 6, formatar_moeda(r['valor_pis']), 1); pdf.cell(35, 6, formatar_moeda(r['valor_cofins']), 1, ln=True)
                    deb_pis += r['valor_pis']; deb_cof += r['valor_cofins']; ret_pis += r['valor_pis_retido']; ret_cof += r['valor_cofins_retido']
            
            pdf.ln(5); pdf.set_font("Arial", 'B', 10); pdf.cell(190, 8, "2. INSUMOS, CREDITOS E EXTEMPORANEOS", ln=True); pdf.set_font("Arial", 'B', 9); pdf.cell(90, 6, "Operacao", 1); pdf.cell(35, 6, "Base", 1); pdf.cell(30, 6, "PIS", 1); pdf.cell(35, 6, "COFINS", 1, ln=True); pdf.set_font("Arial", '', 9)
            if not df_pdf.empty:
                for _, r in df_pdf[df_pdf['op_tipo'] == 'DESPESA'].iterrows():
                    desc_op = r['op_nome']
                    apelido_clean = limpar_texto(r.get('apelido_unidade', ''))
                    if consolidar and r['emp_tipo'] == 'Filial': desc_op += f" ({apelido_clean or 'Filial'})"
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
            df_ext = df_pdf[df_pdf['origem_retroativa'] == 1]
            if not df_ext.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - APROVEITAMENTO DE CREDITO EXTEMPORANEO:", ln=True); pdf.set_font("Arial", '', 8)
                pdf.multi_cell(0, 4, "Esta apuracao inclui a apropriacao de credito tributario originado em competencia anterior, lancado tempestivamente neste periodo."); pdf.ln(2)
                for _, r in df_ext.iterrows(): pdf.multi_cell(0, 4, f"- Origem: {r['competencia_origem']} | Doc: {r['num_nota']} - {r['fornecedor']} | PIS: {formatar_moeda(r['valor_pis'])} | COF: {formatar_moeda(r['valor_cofins'])}\n  Justificativa: {r['historico']}")
            
            with get_db_connection() as conn:
                df_fut = pd.read_sql(f"SELECT * FROM lancamentos l WHERE {filtro_empresa} AND l.competencia_origem = %s AND l.competencia != %s AND l.status_auditoria = 'ATIVO' AND l.is_custo_avulso = 0", conn, params=(comp_db, comp_db))
            
            if not df_fut.empty:
                pdf.ln(5); pdf.set_font("Arial", 'B', 9); pdf.cell(0, 6, "NOTA DE AUDITORIA - CREDITO APROPRIADO EXTEMPORANEAMENTE (NO FUTURO):", ln=True); pdf.set_font("Arial", '', 8)
                for _, r in df_fut.iterrows(): pdf.multi_cell(0, 4, f"Registra-se que o documento fiscal {r['num_nota']}, emitido por {r['fornecedor']} nesta competencia ({comp_db}), nao compos a base de calculo original deste demonstrativo. O respectivo credito foi apropriado extemporaneamente na competencia {r['competencia']}.\nMotivo: {r['historico']}"); pdf.ln(2)

            pdf_bytes = pdf.output(dest='S').encode('latin1')
            st.success("Ficheiros processados com sucesso!")
            c_btn1, c_btn2, _ = st.columns([1, 1, 2])
            c_btn1.download_button("Baixar XLSX (Exportação Alterdata)", data=buffer.getvalue(), file_name=f"LCTOS_{comp_db}.xlsx")
            c_btn2.download_button("Baixar PDF (Demonstrativo Fiscal)", data=pdf_bytes, file_name=f"RESUMO_{comp_db}.pdf")
        except Exception as e: st.error(f"Erro na geração: {e}")

# --- 7.5 MÓDULO IMOBILIZADO E DEPRECIAÇÃO (CÓDIGO OMITIDO PARA ECONOMIZAR ESPAÇO NO CHAT, MAS MANTENHA O SEU ATUAL) ---
def modulo_imobilizado():
    st.info("Módulo Imobilizado inalterado nesta atualização.")
    pass

# --- 8. MÓDULO PARÂMETROS CONTÁBEIS ---
def modulo_parametros():
    if st.session_state.nivel_acesso == "CLIENT_OPERATOR": st.error("Acesso restrito."); return
    st.markdown("### Parâmetros Contábeis e Exportação Alterdata")
    df_op = carregar_operacoes()
    op_nomes = df_op['nome'].tolist()
    
    tab_edit, tab_novo, tab_vinculo, tab_custo, tab_fecho, tab_limpeza, tab_imob = st.tabs(["Editar Global", "Nova Operação", "Vínculo Contábil (Matriz/Filial)", "Destinos Custo (CMV)", "Fecho Mensal", "Auditoria", "Grupos Imobilizado"])
    
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
            p_cod = c3.text_input("Cód Alterdata PIS", value=limpar_texto(row_op.get('pis_h_codigo')), key=f"pcd_{oid}")
            p_txt = c4.text_input("Texto Padrão PIS", value=limpar_texto(row_op.get('pis_h_texto')), key=f"ptx_{oid}")
            
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2])
            c_deb = c5.text_input("Débito COFINS", value=limpar_texto(row_op.get('conta_deb_cof')), key=f"cd_{oid}")
            c_cred = c6.text_input("Crédito COFINS", value=limpar_texto(row_op.get('conta_cred_cof')), key=f"cc_{oid}")
            c_cod = c7.text_input("Cód Alterdata COFINS", value=limpar_texto(row_op.get('cofins_h_codigo')), key=f"ccd_{oid}")
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
            c1, c2, c3, c4 = st.columns([1, 1, 1, 2]); n_p_deb = c1.text_input("Débito PIS", key="n_pd"); n_p_cred = c2.text_input("Crédito PIS", key="n_pc"); n_p_cod = c3.text_input("Cód Alterdata PIS", key="n_pcd"); n_p_txt = c4.text_input("Texto Padrão PIS", key="n_ptx")
            st.markdown("##### Configuração COFINS")
            c5, c6, c7, c8 = st.columns([1, 1, 1, 2]); n_c_deb = c5.text_input("Débito COFINS", key="n_cd"); n_c_cred = c6.text_input("Crédito COFINS", key="n_cc"); n_c_cod = c7.text_input("Cód Alterdata COFINS", key="n_ccd"); n_c_txt = c8.text_input("Texto Padrão COF", key="n_ctx")
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
                n_hc = c_d3.text_input("Cód Alterdata")
                n_ht = c_d4.text_input("Texto Padrão Custo")
                
                if st.form_submit_button("Salvar Destino de Custo"):
                    if not n_destino or not n_cd or not n_cc: st.error("Obrigatório: Nome e Contas.")
                    else:
                        try:
                            with get_db_cursor(commit=True) as cur_ins:
                                cur_ins.execute("INSERT INTO destinos_custo (empresa_id, nome_destino, conta_debito, conta_credito, hist_codigo, hist_texto) VALUES (%s,%s,%s,%s,%s,%s)", (emp_id_c, n_destino, n_cd, n_cc, n_hc, n_ht))
                            st.success("Salvo!"); st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")

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

    with tab_fecho:
        st.markdown("##### Contas de Transferência / Fecho Mensal")
        st.info("Módulo Fecho Mensal - Manter Inalterado para esta versão.")
    with tab_imob:
        st.info("Módulo Grupos Imobilizado - Manter Inalterado para esta versão.")

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
        st.info("Módulo de Inserção - Inalterado")

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
                    # CORREÇÃO DO BUG DO NUMPY: Convertendo explicitamente cada item para o tipo nativo int do Python
                    ids_selecionados = [int(float(i)) for i in df_empresas[df_empresas.apply(formatar_nome_empresa, axis=1).isin(empresas_selecionadas)]['id'].tolist()]
                    
                    try:
                        with get_db_cursor(commit=True) as cursor_perm:
                            for eid in ids_selecionados:
                                query_upsert = """
                                    INSERT INTO usuario_empresas (contabilidade_id, usuario_id, empresa_id, status, concedido_por) 
                                    VALUES (%s, %s, %s, 'ATIVO', %s) 
                                    ON DUPLICATE KEY UPDATE status='ATIVO', concedido_por=VALUES(concedido_por)
                                """
                                cursor_perm.execute(query_upsert, (int(u_contab) if u_contab else None, int(u_id), int(eid), int(st.session_state.usuario_id)))
                            
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
    if st.button("Encerrar Sessão", use_container_width=True): st.session_state.autenticado = False; st.rerun()

# --- 11. RENDERIZAÇÃO DE ROTAS ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "Imobilizado & Depreciação": modulo_imobilizado()
elif menu == "Parâmetros Contábeis": modulo_parametros()
elif menu == "Gestão de Utilizadores": modulo_usuarios()
