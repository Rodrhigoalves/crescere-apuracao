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
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500;}
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

# --- 3. LÓGICA DE ACESSO ---
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown("<br><br>", unsafe_allow_html=True)
    _, login_col, _ = st.columns([1, 1.5, 1])
    with login_col:
        st.markdown("<h1 style='text-align: center; color: #004b87;'>🛡️ CRESCERE</h1>", unsafe_allow_html=True)
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
                else: st.error("Incorreto.")
    st.stop()

# --- 4. ESTADOS ---
hoje = date.today()
competencia_padrao = (hoje.replace(day=1) - timedelta(days=1)).strftime("%m/%Y")
if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

# --- 5. MÓDULO APURAÇÃO (DESIGN HARMONIZADO) ---
def modulo_apuracao():
    st.markdown("## Apuração Mensal")
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

        if st.button("➕ Adicionar ao Rascunho", use_container_width=True):
            op_row = df_op[df_op['nome_exibicao'] == op_sel].iloc[0]
            # Lógica Tributária
            if regime == "Lucro Real":
                vp, vc = (v_base * 0.0065, v_base * 0.04) if op_row['nome'] == "Receita Financeira" else (v_base * 0.0165, v_base * 0.076)
            else: vp, vc = (v_base * 0.0065, v_base * 0.03)
            
            st.session_state.rascunho_lancamentos.append({
                "emp_id": emp_id, "op_id": int(op_row['id']), "op_nome": op_sel, 
                "v_base": v_base, "v_pis": vp, "v_cofins": vc, "hist": hist,
                "retro": retro, "origem": comp_origem if retro else None
            })
            st.rerun()

    with col_ras:
        st.markdown("#### Lista de Rascunho")
        with st.container(height=260, border=True):
            for i, it in enumerate(st.session_state.rascunho_lancamentos):
                c_txt, c_val, c_del = st.columns([5, 3, 1])
                c_txt.markdown(f"<small>{it['op_nome']}</small>", unsafe_allow_html=True)
                c_val.markdown(f"**{formatar_moeda(it['v_base'])}**")
                if c_del.button("×", key=f"d_{i}", help="Remover"):
                    st.session_state.rascunho_lancamentos.pop(i); st.rerun()
                st.divider()
        
        if st.session_state.rascunho_lancamentos:
            if st.button("💾 Gravar Lançamentos no Banco", type="primary", use_container_width=True):
                cursor = conn.cursor()
                m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
                for it in st.session_state.rascunho_lancamentos:
                    cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria, origem_retroativa, competencia_origem) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s,%s)",
                                   (it['emp_id'], it['op_id'], comp_db, it['v_base'], it['v_pis'], it['v_cofins'], it['hist'], st.session_state.username, it['retro'], it['origem']))
                conn.commit(); st.session_state.rascunho_lancamentos = []; st.success("Gravado!"); st.rerun()

    st.divider()
    st.markdown("#### 🔍 Extrato e Retificação (Padrão SAP)")
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        df_ext = pd.read_sql(f"SELECT l.id, o.nome as Operação, l.valor_base, l.valor_pis, l.valor_cofins, l.historico FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}' AND l.status_auditoria = 'ATIVO' ORDER BY l.id DESC", conn)
        if not df_ext.empty:
            df_view = df_ext.copy()
            df_view['valor_base'] = df_view['valor_base'].apply(formatar_moeda)
            st.dataframe(df_view, use_container_width=True, hide_index=True)
            
            with st.expander("✏️ Retificar Lançamento Selecionado"):
                c_id, c_nv, c_mot = st.columns([1, 2, 3])
                id_ret = c_id.number_input("ID", min_value=0)
                n_val = c_nv.number_input("Novo Valor Base (R$)", min_value=0.0)
                motivo = c_mot.text_input("Justificativa da Alteração")
                if st.button("Confirmar Retificação (Imutável)"):
                    if motivo and id_ret in df_ext['id'].values:
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute(f"SELECT * FROM lancamentos WHERE id={id_ret}")
                        velho = cursor.fetchone()
                        # Inativa o antigo
                        cursor.execute(f"UPDATE lancamentos SET status_auditoria='INATIVO', motivo_alteracao=%s WHERE id=%s", (f"RETIFICADO: {motivo}", id_ret))
                        # Insere o novo
                        cursor.execute("INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, usuario_registro, status_auditoria, motivo_alteracao) VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,'ATIVO',%s)",
                                       (velho['empresa_id'], velho['operacao_id'], velho['competencia'], n_val, n_val*0.01, n_val*0.05, velho['historico'], st.session_state.username, f"Origem ID {id_ret}"))
                        conn.commit(); st.success("Retificado!"); st.rerun()
    except: pass
    conn.close()

# --- 6. SIDEBAR ---
with st.sidebar:
    st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center;'>👤 <b>{st.session_state.usuario_logado}</b><br><small>{st.session_state.nivel_acesso}</small></p>", unsafe_allow_html=True)
    st.write("---")
    menu = st.radio("Módulos", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração", "⚙️ Parâmetros Contábeis"])
    if st.button("🚪 Sair / Logoff", use_container_width=True):
        st.session_state.autenticado = False; st.rerun()

# --- 7. RENDERIZAÇÃO ---
if menu == "Apuração Mensal": modulo_apuracao()
# (Outros módulos chamados aqui...)
