import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import calendar
from fpdf import FPDF
import io
import os
import bcrypt

# --- 1. CONFIGURAÇÕES VISUAIS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500;}
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"], .css-1d391kg { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;}
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .stTextInput input { background-color: #f8fafc; border: 1px solid #cbd5e1; }
    .stTextInput input:focus { border: 2px solid #004b87 !important; background-color: #e6f0fa !important; }
</style>
""", unsafe_allow_html=True)

# --- 2. FUNÇÕES DE SEGURANÇA E BANCO ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def realizar_login(usuario, senha):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT u.*, e.status_assinatura 
            FROM usuarios u
            LEFT JOIN empresas e ON u.empresa_id = e.id
            WHERE u.username = %s AND u.status_usuario = 'ATIVO'
        """
        cursor.execute(query, (usuario,))
        user_data = cursor.fetchone()
        
        if user_data and verificar_senha(senha, user_data['senha_hash']):
            if user_data['nivel_acesso'] != 'SUPER_ADMIN' and user_data['status_assinatura'] == 'SUSPENSO':
                return None, "Acesso Suspenso. Entre em contato com o financeiro."
            return user_data, None
        return None, "Usuário ou senha incorretos."
    finally:
        conn.close()

# --- 3. LÓGICA DE ACESSO (TELA DE BLOQUEIO) ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown("<br><br>", unsafe_allow_html=True)
    c1, login_col, c3 = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h1 style='text-align: center; color: #004b87;'>🛡️ CRESCERE</h1>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Usuário")
            pw_input = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                dados_user, erro = realizar_login(user_input, pw_input)
                if dados_user:
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = dados_user['nome']
                    st.session_state.nivel_acesso = dados_user['nivel_acesso']
                    st.session_state.empresa_id = dados_user['empresa_id']
                    st.rerun()
                else:
                    st.error(erro)
    st.stop()

# --- 4. CONFIGURAÇÕES DE ESTADO PÓS-LOGIN ---
hoje = date.today()
primeiro_dia_mes_atual = hoje.replace(day=1)
ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
competencia_padrao = ultimo_dia_mes_anterior.strftime("%m/%Y")

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

# --- 5. FUNÇÕES BASE E FORMATAÇÃO ---
def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None

# --- 6. MÓDULOS DO SISTEMA ---
def modulo_empresas():
    st.markdown("## Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["Novo Cadastro", "Unidades Cadastradas"])
    
    with tab_cad:
        c_busca, c_btn = st.columns([3,1])
        with c_busca:
            cnpj_input = st.text_input("🔍 Busca Automática (CNPJ):", placeholder="Apenas números")
        
        with c_btn:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True) 
            if st.button("Consultar CNPJ", use_container_width=True):
                res = consultar_cnpj(cnpj_input.replace(".","").replace("/","").replace("-",""))
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
        c1, c2 = st.columns(2)
        nome = c1.text_input("Razão Social", value=f['nome'])
        fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
        
        c3, c4, c5 = st.columns([2, 1.5, 1.5])
        cnpj = c3.text_input("CNPJ", value=f['cnpj'])
        regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
        tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
        
        cnae = st.text_input("CNAE Principal", value=f['cnae'])
        endereco = st.text_area("Endereço Completo", value=f['endereco'])
        
        if st.button("Salvar Empresa", use_container_width=True):
            conn = get_db_connection()
            cursor = conn.cursor()
            if f['id']: 
                sql = "UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s WHERE id=%s"
                cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco, f['id']))
            else: 
                sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco))
            conn.commit()
            conn.close()
            st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
            st.success("Empresa salva!")
            st.rerun()

    with tab_lista:
        conn = get_db_connection()
        try:
            df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo FROM empresas", conn)
            for _, row in df.iterrows():
                col_info, col_btn = st.columns([5, 1])
                col_info.markdown(f"**{row['nome']}** | {row['tipo']}<br>CNPJ: {row['cnpj']} | Regime: {row['regime']}", unsafe_allow_html=True)
                if col_btn.button("Editar", key=f"btn_{row['id']}"):
                    df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                    st.session_state.dados_form = df_edit.iloc[0].to_dict()
                    st.rerun()
                st.divider()
        except: pass
        conn.close()

def modulo_apuracao():
    st.markdown("## Apuração Mensal")
    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
        df_operacoes['nome_exibicao'] = df_operacoes.apply(lambda x: f"[DÉBITO] {x['nome']}" if x['tipo'] == 'RECEITA' else f"[CRÉDITO] {x['nome']}", axis=1)
    except:
        st.warning("Banco não pronto. Vá em Parâmetros e faça o Reset."); conn.close(); return

    if df_empresas.empty:
        st.info("Cadastre uma empresa."); conn.close(); return

    c_empresa, c_comp, c_user = st.columns([2, 1, 1])
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = c_empresa.selectbox("Empresa Ativa", opcoes_empresas)
    empresa_id = int(df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['id'])
    regime_empresa = df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Operador", value=st.session_state.usuario_logado, disabled=True)

    st.write("---")
    col_entrada, col_rascunho = st.columns([1, 1.2], gap="large")

    with col_entrada:
        st.markdown("#### Inserção")
        operacao_sel = st.selectbox("Operação", df_operacoes['nome_exibicao'].tolist())
        op_row = df_operacoes[df_operacoes['nome_exibicao'] == operacao_sel].iloc[0]
        valor_base = st.number_input("Valor da Base (R$)", min_value=0.00, step=100.00)
        historico = st.text_input("Observação")
        
        if st.button("Adicionar ao Rascunho", use_container_width=True):
            if valor_base > 0:
                # Lógica de cálculo conforme regime
                if regime_empresa == "Lucro Real":
                    vp, vc = (valor_base * 0.0065, valor_base * 0.04) if op_row['nome'] == "Receita Financeira" else (valor_base * 0.0165, valor_base * 0.076)
                else:
                    vp, vc = (valor_base * 0.0065, valor_base * 0.03)
                
                st.session_state.rascunho_lancamentos.append({
                    "empresa_id": empresa_id, "operacao_id": int(op_row['id']), "operacao_exibicao": operacao_sel,
                    "valor_base": valor_base, "valor_pis": vp, "valor_cofins": vc, "historico": historico
                })
                st.rerun()

    with col_rascunho:
        st.markdown("#### Rascunho (Pré-Banco)")
        with st.container(height=380, border=True):
            for i, item in enumerate(st.session_state.rascunho_lancamentos):
                c_desc, c_val, c_del = st.columns([5, 3, 1])
                c_desc.markdown(f"**{item['operacao_exibicao']}**")
                c_val.markdown(formatar_moeda(item['valor_base']))
                if c_del.button("✖", key=f"del_{i}"):
                    st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                st.divider()
        
        if st.session_state.rascunho_lancamentos and st.button("💾 Gravar no Banco", type="primary", use_container_width=True):
            m, a = competencia.split('/')
            comp_db = f"{a}-{m.zfill(2)}"
            data_l = f"{a}-{m.zfill(2)}-{calendar.monthrange(int(a), int(m))[1]:02d}"
            cursor = conn.cursor()
            for item in st.session_state.rascunho_lancamentos:
                cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               (item['empresa_id'], item['operacao_id'], comp_db, data_l, item['valor_base'], item['valor_pis'], item['valor_cofins'], item['historico'], st.session_state.usuario_logado))
            conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()
    conn.close()

def modulo_parametros():
    st.markdown("## ⚙️ Parâmetros Contábeis")
    
    # GOVERNANÇA: Só Super Admin vê a Manutenção (Reset)
    tabs_list = ["➕ Nova Operação"]
    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        tabs_list.append("🚨 Manutenção")
    
    tabs = st.tabs(tabs_list)
    
    with tabs[0]:
        with st.form("f_op"):
            nome_op = st.text_input("Nome")
            c1, c2 = st.columns(2)
            d, c = c1.text_input("Débito"), c2.text_input("Crédito")
            tipo = st.radio("Natureza", ["Receita", "Despesa"])
            if st.form_submit_button("Salvar"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO operacoes (nome, tipo, conta_debito, conta_credito) VALUES (%s,%s,%s,%s)", (nome_op, tipo.upper(), d, c))
                conn.commit(); conn.close(); st.success("OK")

    if st.session_state.nivel_acesso == "SUPER_ADMIN":
        with tabs[1]:
            st.error("Área Crítica!")
            if st.text_input("Segurança: CONFIRMAR EXCLUSAO TOTAL") == "CONFIRMAR EXCLUSAO TOTAL":
                if st.button("Resetar Sistema"):
                    # Aqui iria sua função resetar_tabelas_apuracao()
                    st.warning("Função de reset acionada.")

def modulo_relatorios():
    st.markdown("## 📄 Relatórios & Integração")
    st.info("Módulo de exportação XLSX para ERP.")

# --- 7. NAVEGAÇÃO LATERAL ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    
    st.markdown('''<a href="https://conciliador-contabil-hsppms6xpbjstvmmfktgkc.streamlit.app/" target="_blank" style="display: block; padding: 10px; background-color: #004b87; color: white; text-align: center; border-radius: 6px; text-decoration: none; font-weight: bold;">🚀 Conciliador Contábil</a>''', unsafe_allow_html=True)
    
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])
    
    st.write("---")
    if st.button("🚪 Sair / Logoff", use_container_width=True):
        st.session_state.autenticado = False
        st.rerun()

# --- 8. RENDERIZAÇÃO ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
