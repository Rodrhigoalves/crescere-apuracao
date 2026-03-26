import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import io
import bcrypt
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES VISUAIS ---
st.set_page_config(page_title="Crescere V6 - Gestão Fiscal", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500; height: 45px; transition: all 0.2s; }
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"] { background-color: #ffffff; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; }
    h1, h2, h3 { color: #0f172a; font-family: 'Segoe UI', sans-serif; }
</style>
""", unsafe_allow_html=True)

# --- 2. CONEXÃO E AUXILIARES ---
def get_db_connection():
    try:
        return mysql.connector.connect(**st.secrets["mysql"])
    except mysql.connector.Error as err:
        st.error(f"Erro de conexão: {err}")
        st.stop()

@st.cache_data(ttl=60)
def carregar_operacoes():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM operacoes ORDER BY tipo DESC, nome ASC", conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def carregar_empresas_ativas():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM empresas WHERE status_assinatura = 'ATIVO'", conn)
    conn.close()
    return df

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def verificar_senha(senha_plana, hash_banco):
    return bcrypt.checkpw(senha_plana.encode('utf-8'), hash_banco.encode('utf-8'))

# --- 3. MOTOR DE CÁLCULO ---
def calcular_impostos(regime, operacao_nome, valor_base):
    if regime == "Lucro Real":
        if "Receita Financeira" in operacao_nome: return (valor_base * 0.0065, valor_base * 0.04)
        return (valor_base * 0.0165, valor_base * 0.076)
    elif regime == "Lucro Presumido":
        return (valor_base * 0.0065, valor_base * 0.03)
    return (0.0, 0.0)

# --- 4. AUTENTICAÇÃO E ESTADO ---
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'dados_form' not in st.session_state: st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": "", "apelido_unidade": ""}
if 'rascunho_lancamentos' not in st.session_state: st.session_state.rascunho_lancamentos = []
if 'form_key' not in st.session_state: st.session_state.form_key = 0

hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")

if not st.session_state.autenticado:
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h2 style='text-align: center; color: #004b87;'>CRESCERE</h2>", unsafe_allow_html=True)
        with st.form("form_login"):
            user_input = st.text_input("Usuário")
            pw_input = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT * FROM usuarios WHERE username = %s AND status_usuario = 'ATIVO'", (user_input,))
                user_data = cursor.fetchone()
                conn.close()
                if user_data and verificar_senha(pw_input, user_data['senha_hash']):
                    st.session_state.update({"autenticado": True, "usuario_logado": user_data['nome'], "username": user_data['username'], "empresa_id": user_data.get('empresa_id'), "nivel_acesso": "SUPER_ADMIN" if user_data['username'].lower() == "rodrhigo" else user_data['nivel_acesso']})
                    st.rerun()
                else: st.error("Acesso Negado.")
    st.stop()

# --- 5. MÓDULO EMPRESAS (LIMPO) ---
def modulo_empresas():
    st.markdown("### 🏢 Cadastro de Unidades")
    tab_cad, tab_lista = st.tabs(["Novo Registro", "Lista"])
    with tab_cad:
        f = st.session_state.dados_form
        with st.form("form_empresa"):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            cnpj = c2.text_input("CNPJ", value=f['cnpj'])
            c3, c4, c5 = st.columns([2, 1, 1])
            apelido = c3.text_input("Apelido Unidade", value=f.get('apelido_unidade', ''))
            regime = c4.selectbox("Regime", ["Lucro Real", "Lucro Presumido"])
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"])
            if st.form_submit_button("Salvar Empresa"):
                conn = get_db_connection(); cursor = conn.cursor()
                if f['id']:
                    cursor.execute("UPDATE empresas SET nome=%s, cnpj=%s, regime=%s, tipo=%s, apelido_unidade=%s WHERE id=%s", (nome, cnpj, regime, tipo, apelido, f['id']))
                else:
                    cursor.execute("INSERT INTO empresas (nome, cnpj, regime, tipo, apelido_unidade, status_assinatura) VALUES (%s,%s,%s,%s,%s,'ATIVO')", (nome, cnpj, regime, tipo, apelido))
                conn.commit(); conn.close(); carregar_empresas_ativas.clear(); st.success("Ok!"); st.rerun()

    with tab_lista:
        df = carregar_empresas_ativas()
        st.dataframe(df[['nome', 'cnpj', 'regime', 'apelido_unidade']], use_container_width=True)

# --- 6. MÓDULO APURAÇÃO ---
def modulo_apuracao():
    st.markdown("### 📝 Lançamentos Mensais")
    df_emp = carregar_empresas_ativas()
    if st.session_state.nivel_acesso != "SUPER_ADMIN": df_emp = df_emp[df_emp['id'] == st.session_state.empresa_id]
    
    c1, c2 = st.columns([2, 1])
    emp_sel = c1.selectbox("Empresa", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    regime = df_emp.loc[df_emp['id'] == emp_id].iloc[0]['regime']
    competencia = c2.text_input("Competência (MM/AAAA)", value=competencia_padrao)

    st.divider()
    col_in, col_ras = st.columns(2)
    with col_in:
        df_op = carregar_operacoes()
        op_sel = st.selectbox("Operação", df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1))
        op_row = df_op[df_op.apply(lambda x: f"[{x['tipo']}] {x['nome']}", axis=1) == op_sel].iloc[0]
        v_base = st.number_input("Valor Base", min_value=0.0)
        hist = st.text_input("Histórico Adicional")
        
        c_r, c_o = st.columns(2)
        retro = c_r.checkbox("Extemporâneo")
        origem = c_o.text_input("Mês Origem", disabled=not retro)
        
        if st.button("Adicionar"):
            vp, vc = calcular_impostos(regime, op_row['nome'], v_base)
            st.session_state.rascunho_lancamentos.append({
                "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_row['nome'], "v_base": v_base, 
                "v_pis": vp, "v_cofins": vc, "hist": hist, "retro": retro, "origem": origem
            })
            st.rerun()

    with col_ras:
        st.write("Rascunho:", st.session_state.rascunho_lancamentos)
        if st.button("Gravar Tudo", type="primary") and st.session_state.rascunho_lancamentos:
            conn = get_db_connection(); cursor = conn.cursor()
            m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            for it in st.session_state.rascunho_lancamentos:
                cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, valor_base, valor_pis, valor_cofins, historico, usuario_registro, origem_retroativa, competencia_origem) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", 
                               (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['hist'], st.session_state.username, it['retro'], it['origem']))
            conn.commit(); conn.close(); st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()

# --- 7. MÓDULO RELATÓRIOS (V6 - HISTÓRICOS INDEPENDENTES) ---
def modulo_relatorios():
    st.markdown("### 📂 Exportação Alterdata & PDF")
    df_emp = carregar_empresas_ativas()
    emp_sel = st.selectbox("Unidade", df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1))
    emp_id = int(df_emp.loc[df_emp.apply(lambda r: f"{r['nome']} - {r['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    competencia = st.text_input("Comp.", value=competencia_padrao)

    if st.button("Gerar Arquivos"):
        conn = get_db_connection()
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        # Query V6: Puxa todos os campos independentes
        query = f"""SELECT l.*, o.nome as op_nome, o.tipo as op_tipo, 
                   o.conta_deb_pis, o.conta_cred_pis, o.pis_h_codigo, o.pis_h_texto,
                   o.conta_deb_cof, o.conta_cred_cof, o.cofins_h_codigo, o.cofins_h_texto,
                   o.conta_deb_custo, o.conta_cred_custo, o.custo_h_codigo, o.custo_h_texto
                   FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id 
                   WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}'"""
        df_export = pd.read_sql(query, conn)
        
        if not df_export.empty:
            linhas = []
            for _, r in df_export.iterrows():
                dt = r['data_lancamento'].strftime('%d/%m/%Y') if r['data_lancamento'] else ""
                def fmt_h(t, n): return t.replace("{operacao}", n).replace("{competencia}", competencia) if t else f"VLR REF {n}"
                
                # LINHA PIS
                if r['conta_deb_pis']:
                    linhas.append({"Debito": str(r['conta_deb_pis']).replace('.',''), "Credito": str(r['conta_cred_pis']).replace('.',''), "Data": dt, "Valor": r['valor_pis'], "Cod. Hist": r['pis_h_codigo'], "Historico": fmt_h(r['pis_h_texto'], r['op_nome'])})
                # LINHA COFINS
                if r['conta_deb_cof']:
                    linhas.append({"Debito": str(r['conta_deb_cof']).replace('.',''), "Credito": str(r['conta_cred_cof']).replace('.',''), "Data": dt, "Valor": r['valor_cofins'], "Cod. Hist": r['cofins_h_codigo'], "Historico": fmt_h(r['cofins_h_texto'], r['op_nome'])})
                # LINHA CUSTO
                if r['op_tipo'] != 'RETENÇÃO' and r['conta_deb_custo']:
                    linhas.append({"Debito": str(r['conta_deb_custo']).replace('.',''), "Credito": str(r['conta_cred_custo']).replace('.',''), "Data": dt, "Valor": r['valor_base']-r['valor_pis']-r['valor_cofins'], "Cod. Hist": r['custo_h_codigo'], "Historico": fmt_h(r['custo_h_texto'], r['op_nome'])})
            
            # Exportação Excel
            df_xlsx = pd.DataFrame(linhas)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as w: df_xlsx.to_excel(w, index=False)
            st.download_button("Baixar XLSX Alterdata", buf.getvalue(), f"ALTERDATA_{competencia.replace('/','_')}.xlsx")
        conn.close()

# --- 8. MÓDULO PARÂMETROS (V6 - TABELAS SEPARADAS) ---
def modulo_parametros():
    st.markdown("### ⚙️ Parâmetros Contábeis")
    t1, t2 = st.tabs(["Operações e Históricos", "Transferências (Fecho)"])
    
    with t1:
        df_op = carregar_operacoes()
        sel = st.selectbox("Selecione a Operação", df_op['nome'].tolist())
        r = df_op[df_op['nome'] == sel].iloc[0]
        with st.form("f_op_v6"):
            c1, c2, c3 = st.columns(3)
            # PIS
            p_cod = c1.text_input("PIS: Cód. Histórico", value=r.get('pis_h_codigo', ''))
            p_txt = c1.text_area("PIS: Texto Padrão", value=r.get('pis_h_texto', ''))
            # COFINS
            c_cod = c2.text_input("COF: Cód. Histórico", value=r.get('cofins_h_codigo', ''))
            c_txt = c2.text_area("COF: Texto Padrão", value=r.get('cofins_h_texto', ''))
            # CUSTO
            cu_cod = c3.text_input("CUSTO: Cód. Histórico", value=r.get('custo_h_codigo', ''))
            cu_txt = c3.text_area("CUSTO: Texto Padrão", value=r.get('custo_h_texto', ''))
            
            if st.form_submit_button("Salvar Parâmetros"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("""UPDATE operacoes SET pis_h_codigo=%s, pis_h_texto=%s, cofins_h_codigo=%s, cofins_h_texto=%s, custo_h_codigo=%s, custo_h_texto=%s WHERE id=%s""", 
                               (p_cod, p_txt, c_cod, c_txt, cu_cod, cu_txt, int(r['id'])))
                conn.commit(); conn.close(); carregar_operacoes.clear(); st.success("Atualizado!"); st.rerun()

    with t2:
        st.info("Aqui você configura as contas de transferência específicas por empresa.")
        df_emp = carregar_empresas_ativas()
        sel_e = st.selectbox("Unidade para Fecho", df_emp['nome'].tolist())
        e_id = int(df_emp[df_emp['nome'] == sel_e].iloc[0]['id'])
        
        # Interface simplificada para a tabela parametros_fecho
        with st.form("f_fecho"):
            c_orig = st.text_input("Conta Origem (Ex: 2492)")
            c_dest = st.text_input("Conta Destino (Ex: 1050)")
            if st.form_submit_button("Vincular Conta de Fecho"):
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO parametros_fecho (empresa_id, conta_origem, conta_destino) VALUES (%s,%s,%s)", (e_id, c_orig, c_dest))
                conn.commit(); conn.close(); st.success("Vinculado!"); st.rerun()

# --- 10. MENU E RENDER ---
with st.sidebar:
    st.markdown("## 🛡️ CRESCERE")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])
    if st.button("Sair"): st.session_state.autenticado = False; st.rerun()

if menu == "Gestão de Empresas": modulo_empresas()
elif menu == "Apuração Mensal": modulo_apuracao()
elif menu == "Relatórios e Integração": modulo_relatorios()
elif menu == "⚙️ Parâmetros Contábeis": modulo_parametros()
