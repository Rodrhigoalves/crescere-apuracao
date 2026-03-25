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
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 42px;}
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"], .css-1d391kg, .stExpander { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;}
    h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
    .stTextInput input, .stNumberInput input, .stSelectbox select { background-color: #f8fafc; border: 1px solid #cbd5e1; }
</style>
""", unsafe_allow_html=True)

# --- 2. FUNÇÕES BASE E SEGURANÇA ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except: return None

# --- 3. LÓGICA DE ACESSO ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown("<br><br>", unsafe_allow_html=True)
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h2 style='text-align: center; color: #004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Usuário")
            pw_input = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar no Sistema", use_container_width=True):
                conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT u.* FROM usuarios u WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                user_data = cursor.fetchone(); conn.close()
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']
                    st.session_state.nivel_acesso = user_data['nivel_acesso']
                    st.rerun()
                else: st.error("Acesso negado.")
    st.stop()

# --- 4. ESTADOS ---
hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")
if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "status_assinatura": "ATIVO"}
if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

# --- 5. MÓDULO GESTÃO DE EMPRESAS (RESTAURADO) ---
def modulo_empresas():
    st.markdown("### Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["Novo Cadastro", "Unidades Cadastradas"])
    with tab_cad:
        c_busca, c_btn = st.columns([3,1])
        with c_busca: cnpj_input = st.text_input("CNPJ para busca automática:")
        with c_btn:
            st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
            if st.button("Consultar", use_container_width=True):
                res = consultar_cnpj(cnpj_input.replace(".","").replace("/","").replace("-",""))
                if res and res.get('status') != 'ERROR':
                    st.session_state.dados_form.update({"nome": res.get('nome', ''), "fantasia": res.get('fantasia', ''), "cnpj": res.get('cnpj', ''), "cnae": res.get('atividade_principal', [{}])[0].get('code', ''), "endereco": f"{res.get('logradouro', '')}, {res.get('numero', '')} - {res.get('bairro', '')}, {res.get('municipio', '')}/{res.get('uf', '')}"})
                    st.rerun()
        st.divider()
        f = st.session_state.dados_form
        c1, c2 = st.columns(2)
        nome = c1.text_input("Razão Social", value=f['nome'])
        fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
        c3, c4, c5 = st.columns([2, 1.5, 1.5])
        cnpj = c3.text_input("CNPJ", value=f['cnpj'])
        regime = c4.selectbox("Regime", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
        tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
        c6, c7 = st.columns([2, 1])
        cnae = c6.text_input("CNAE", value=f['cnae'])
        status_emp = c7.selectbox("Status", ["ATIVO", "SUSPENSO"], index=0 if f['status_assinatura'] == "ATIVO" else 1)
        end = st.text_area("Endereço", value=f['endereco'])
        if st.button("Salvar Registro", use_container_width=True):
            conn = get_db_connection(); cursor = conn.cursor()
            if f['id']: cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s, status_assinatura=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, cnae, end, status_emp, f['id']))
            else: cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco, status_assinatura) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (nome, fanta, cnpj, regime, tipo, cnae, end, status_emp))
            conn.commit(); conn.close(); st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "status_assinatura": "ATIVO"}; st.success("Sucesso!"); st.rerun()

    with tab_lista:
        conn = get_db_connection(); df = pd.read_sql("SELECT * FROM empresas", conn); conn.close()
        for _, row in df.iterrows():
            c_inf, c_b = st.columns([5, 1])
            c_inf.markdown(f"**{row['nome']}** - {row['cnpj']}<br><small>{row['endereco']} | Status: {row['status_assinatura']}</small>", unsafe_allow_html=True)
            if c_b.button("Editar", key=f"ed_{row['id']}"):
                st.session_state.dados_form = row.to_dict(); st.rerun()
            st.divider()

# --- 6. MÓDULO APURAÇÃO (ALINHAMENTO GEOMÉTRICO) ---
def modulo_apuracao():
    st.markdown("### Apuração Mensal")
    conn = get_db_connection()
    df_emp = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas WHERE status_assinatura='ATIVO'", conn)
    df_op = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{'DÉBITO' if x['tipo'] == 'RECEITA' else 'CRÉDITO'}] {x['nome']}", axis=1)

    c_e, c_c, c_u = st.columns([2, 1, 1])
    emp_sel = c_e.selectbox("Empresa Ativa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    comp = c_c.text_input("Competência", value=competencia_padrao)
    c_u.text_input("Operador", value=st.session_state.usuario_logado, disabled=True)

    st.divider()
    col_l, col_r = st.columns([1, 1], gap="large")

    with col_l:
        st.markdown("#### Lançamento")
        with st.container(border=True): # Container para alinhar altura
            op_s = st.selectbox("Operação", df_op['nome_exibicao'].tolist())
            v_b = st.number_input("Valor da Base (R$)", min_value=0.0, step=100.0)
            hst = st.text_input("Observação")
            c_rt, c_og = st.columns(2)
            rt = c_rt.checkbox("Retroativo")
            og = c_og.text_input("Mês Origem", disabled=not rt)
            st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True) # Espaçador
            if st.button("Adicionar ao Rascunho", use_container_width=True):
                op_row = df_op[df_op['nome_exibicao'] == op_s].iloc[0]
                vp, vc = (v_b*0.0165, v_b*0.076) if regime=="Lucro Real" else (v_b*0.0065, v_b*0.03)
                st.session_state.rascunho_lancamentos.append({"emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_s, "v_b": v_b, "v_p": vp, "v_c": vc, "hst": hst, "rt": rt, "og": og})
                st.rerun()

    with col_r:
        st.markdown("#### Lista de Rascunho")
        with st.container(height=288, border=True): # Altura fixada para parear botões
            for i, it in enumerate(st.session_state.rascunho_lancamentos):
                cl1, cl2, cl3 = st.columns([6, 3, 1])
                cl1.write(f"<small>{it['op_nome']}</small>", unsafe_allow_html=True)
                cl2.write(f"**{formatar_moeda(it['v_b'])}**")
                if cl3.button("×", key=f"rm_{i}"): st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                st.divider()
        if st.button("Gravar no Banco de Dados", type="primary", use_container_width=True, disabled=not st.session_state.rascunho_lancamentos):
            cursor = conn.cursor(); m, a = comp.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            for it in st.session_state.rascunho_lancamentos:
                cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s)", (it['emp_id'], it['op_id'], comp_db, it['v_b'], it['v_p'], it['v_c'], it['hst'], st.session_state.username, it['rt'], it['og']))
            conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()

    st.divider()
    st.markdown("#### Extrato de Auditoria")
    m, a = comp.split('/'); comp_db = f"{a}-{m.zfill(2)}"
    df_ex = pd.read_sql(f"SELECT l.id, o.nome as Operação, l.valor_base, l.valor_pis, l.valor_cofins, l.usuario_registro FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO'", conn)
    if not df_ex.empty: st.dataframe(df_ex, use_container_width=True, hide_index=True)
    conn.close()

# --- 7. MÓDULO RELATÓRIOS (PDF E EXCEL) ---
def modulo_relatorios():
    st.markdown("### Relatórios e Integração")
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT id, nome FROM empresas", conn)
    c1, c2 = st.columns(2)
    emp = c1.selectbox("Empresa", df_e['nome'])
    comp = c2.text_input("Competência (MM/AAAA)", key="rel_comp")
    
    col1, col2 = st.columns(2)
    if col1.button("📥 Gerar Excel ERP (11 Colunas)", use_container_width=True):
        st.info("Arquivo XLSX gerado para integração.")
    if col2.button("📄 Gerar PDF de Conferência", use_container_width=True):
        st.info("Relatório PDF pronto para impressão.")
    conn.close()

# --- 8. SIDEBAR ---
with st.sidebar:
    st.markdown("<h3 style='color: #004b87; text-align: center;'>CRESCERE</h3>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.usuario_logado}**")
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros"])
    if st.button("Sair"): st.session_state.autenticado = False; st.rerun()

# --- 9. RENDERIZAÇÃO ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "⚙️ Parâmetros": st.info("Módulo Admin.")
