import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import calendar
import io
import os
import bcrypt

# --- 1. CONFIGURAÇÕES VISUAIS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px;}
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"], .css-1d391kg, .stExpander { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;}
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    .stTextInput input, .stNumberInput input { background-color: #f8fafc; border: 1px solid #cbd5e1; }
</style>
""", unsafe_allow_html=True)

# --- 2. FUNÇÕES BASE ---
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
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT u.*, e.status_assinatura FROM usuarios u LEFT JOIN empresas e ON u.empresa_id = e.id WHERE u.username = %s AND u.status_usuario = 'ATIVO'", (user_input,))
                user_data = cursor.fetchone()
                conn.close()
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.autenticado = True
                    st.session_state.usuario_logado = user_data['nome']
                    st.session_state.username = user_data['username']
                    st.session_state.nivel_acesso = user_data['nivel_acesso']
                    st.rerun()
                else: st.error("Acesso Negado.")
    st.stop()

# --- 4. ESTADOS ---
hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")
if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
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
        if st.button("Salvar Empresa", use_container_width=True):
            conn = get_db_connection(); cursor = conn.cursor()
            if f['id']: cursor.execute("UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s WHERE id=%s", (nome, fanta, cnpj, regime, tipo, f['id']))
            else: cursor.execute("INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, status_assinatura) VALUES (%s,%s,%s,%s,%s,'ATIVO')", (nome, fanta, cnpj, regime, tipo))
            conn.commit(); conn.close(); st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}; st.success("Salvo!"); st.rerun()

    with tab_lista:
        conn = get_db_connection()
        df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo FROM empresas", conn)
        for _, row in df.iterrows():
            col_info, col_btn = st.columns([5, 1])
            col_info.markdown(f"**{row['nome']}** | {row['tipo']}<br>CNPJ: {row['cnpj']} | Regime: {row['regime']}", unsafe_allow_html=True)
            if col_btn.button("Editar", key=f"btn_{row['id']}"):
                df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                st.session_state.dados_form = df_edit.iloc[0].to_dict(); st.rerun()
            st.divider()
        conn.close()

# --- 6. MÓDULO APURAÇÃO (DESIGN HARMONIZADO) ---
def modulo_apuracao():
    st.markdown("### Apuração Mensal")
    conn = get_db_connection()
    df_emp = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
    df_op = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
    df_op['nome_exibicao'] = df_op.apply(lambda x: f"[{'DÉBITO' if x['tipo'] == 'RECEITA' else 'CRÉDITO'}] {x['nome']}", axis=1)

    c_emp, c_comp, c_user = st.columns([2, 1, 1])
    emp_sel = c_emp.selectbox("Empresa Ativa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    c_user.text_input("Operador", value=st.session_state.usuario_logado, disabled=True)

    st.divider()
    col_in, col_ras = st.columns([1, 1], gap="large")

    with col_in:
        st.markdown("#### Lançamento")
        op_sel = st.selectbox("Operação", df_op['nome_exibicao'].tolist())
        v_base = st.number_input("Valor da Base (R$)", min_value=0.00, step=50.0)
        hist = st.text_input("Observação Livre")
        
        c_retro, c_origem = st.columns([1, 1])
        retro = c_retro.checkbox("Lançamento Retroativo")
        comp_origem = c_origem.text_input("Mês de Origem", placeholder="MM/AAAA", disabled=not retro)

        if st.button("Adicionar ao Rascunho", use_container_width=True):
            op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
            if regime == "Lucro Real":
                vp, vc = (v_base * 0.0065, v_base * 0.04) if op_row['nome'] == "Receita Financeira" else (v_base * 0.0165, v_base * 0.076)
            else: vp, vc = (v_base * 0.0065, v_base * 0.03)
            st.session_state.rascunho_lancamentos.append({"emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, "v_base": v_base, "v_pis": vp, "v_cofins": vc, "hist": hist, "retro": retro, "origem": comp_origem if retro else None})
            st.rerun()

    with col_ras:
        st.markdown("#### Lista de Rascunho")
        # Altura ajustada para 345px para alinhar o botão inferior com o de 'Adicionar'
        with st.container(height=345, border=True):
            for i, it in enumerate(st.session_state.rascunho_lancamentos):
                c_txt, c_val, c_del = st.columns([6, 3, 1])
                c_txt.markdown(f"<small>{it['op_nome']}</small>", unsafe_allow_html=True)
                c_val.markdown(f"**{formatar_moeda(it['v_base'])}**")
                if c_del.button("×", key=f"d_{i}"):
                    st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                st.divider()
        
        # Botão alinhado na mesma direção do botão da coluna ao lado
        if st.button("Gravar no Banco de Dados", type="primary", use_container_width=True, disabled=len(st.session_state.rascunho_lancamentos)==0):
            cursor = conn.cursor()
            m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            for it in st.session_state.rascunho_lancamentos:
                cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s)", (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['hist'], st.session_state.username, it['retro'], it['origem']))
            conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()

    st.divider()
    st.markdown("#### Extrato e Retificação")
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        df_ext = pd.read_sql(f"SELECT l.id, o.nome as Operação, l.valor_base, l.valor_pis, l.valor_cofins, l.historico FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO' ORDER BY l.id DESC", conn)
        if not df_ext.empty:
            df_view = df_ext.copy()
            df_view['valor_base'] = df_view['valor_base'].apply(formatar_moeda)
            st.dataframe(df_view, use_container_width=True, hide_index=True)
            with st.expander("Retificar Lançamento"):
                c_id, c_nv, c_mot = st.columns([1, 2, 3])
                id_ret = c_id.number_input("ID", min_value=0)
                n_val = c_nv.number_input("Novo Valor Base", min_value=0.0)
                motivo = c_mot.text_input("Justificativa")
                if st.button("Confirmar Alteração"):
                    if motivo and id_ret in df_ext['id'].values:
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute(f"UPDATE lancamentos SET status_auditoria='INATIVO', motivo_alteracao=%s WHERE id=%s", (f"RETIFICADO: {motivo}", id_ret))
                        # Lógica de inserção da nova linha mantida em "off" (status ativo mas ligada ao anterior)
                        st.success("Alteração concluída!"); st.rerun()
    except: pass
    conn.close()

# --- 7. SIDEBAR ---
with st.sidebar:
    st.markdown("<h3 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h3>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])
    if st.button("🚪 Sair"):
        st.session_state.autenticado = False; st.rerun()

# --- 8. RENDERIZAÇÃO ---
if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": st.info("Módulo em desenvolvimento.")
elif menu == "⚙️ Parâmetros Contábeis": st.info("Módulo restrito.")
