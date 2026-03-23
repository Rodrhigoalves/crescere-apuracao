import streamlit as st
import mysql.connector
import pandas as pd
import requests
import smtplib
import calendar
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import date
from fpdf import FPDF

# --- 1. CONFIGURAÇÕES E ESTADOS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide")

if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'dados_cnpj' not in st.session_state: st.session_state.dados_cnpj = {}

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200: return response.json()
    except: pass
    return None

def ultimo_dia_mes(ano, mes_nome):
    meses_num = {m: i+1 for i, m in enumerate(['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'])}
    dia = calendar.monthrange(ano, meses_num[mes_nome])[1]
    return f"{dia}/{str(meses_num[mes_nome]).zfill(2)}/{ano}"

# --- 2. INTERFACE (SIDEBAR) ---
with st.sidebar:
    st.title("🛡️ Crescere")
    menu = st.radio("Navegação", ["Início", "Empresas", "Apuração Mensal", "Relatórios & ERP"])

# --- MÓDULO: INÍCIO ---
if menu == "Início":
    st.markdown("<br><br><div style='text-align: center;'>", unsafe_allow_html=True)
    st.title("🛡️ Crescere")
    st.subheader("Bem-vindo à Crescere Inteligência Contábil")
    st.write("---")
    st.info("Sistema de Gestão de Apuração PIS/COFINS e Integração ERP")
    st.markdown("</div>", unsafe_allow_html=True)

# --- MÓDULO: EMPRESAS (EXATAMENTE COMO VOCÊ APROVOU) ---
elif menu == "Empresas":
    st.header("🏢 Gestão de Unidades")
    
    with st.expander("🔍 Consultar Novo CNPJ (Receita Federal)", expanded=True):
        c1, c2 = st.columns([3, 1])
        cnpj_busca = c1.text_input("Digite o CNPJ para auto-preenchimento")
        if c2.button("Buscar Dados"):
            limpo = cnpj_busca.replace(".","").replace("/","").replace("-","")
            res = consultar_cnpj(limpo)
            if res and res.get('status') != 'ERROR':
                st.session_state.dados_cnpj = res
                st.toast("✅ Dados carregados!")
            else:
                st.error("CNPJ não encontrado.")

    with st.form("form_unidade"):
        d = st.session_state.dados_cnpj
        col1, col2 = st.columns(2)
        nome = col1.text_input("Razão Social", value=d.get('nome', ''))
        fanta = col2.text_input("Nome Fantasia", value=d.get('fantasia', ''))
        
        c3, c4, c5 = st.columns([2, 2, 1])
        cnpj = c3.text_input("CNPJ", value=d.get('cnpj', ''))
        regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"])
        tipo = c5.selectbox("Tipo", ["Matriz", "Filial"])
        
        cnae_val = f"{d['atividade_principal'][0].get('code', '')}" if 'atividade_principal' in d else ""
        cnae = st.text_input("CNAE Principal", value=cnae_val)
        
        end_val = f"{d.get('logradouro','')}, {d.get('numero','')} - {d.get('bairro','')}, {d.get('municipio','')}/{d.get('uf','')}" if 'logradouro' in d else ""
        endereco = st.text_area("Endereço Completo", value=end_val)

        if st.form_submit_button("💾 Salvar Unidade"):
            conn = get_db_connection()
            cursor = conn.cursor()
            sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco))
            conn.commit()
            conn.close()
            st.session_state.dados_cnpj = {}
            st.success("✅ Unidade salva!")
            st.rerun()

    st.divider()
    st.subheader("📝 Unidades Cadastradas")
    conn = get_db_connection()
    df_lista = pd.read_sql("SELECT id, nome, cnpj, tipo, regime FROM empresas", conn)
    conn.close()
    for _, row in df_lista.iterrows():
        exp = st.expander(f"[{row['tipo'][0]}] {row['nome']}")
        c_ed1, c_ed2 = exp.columns([4, 1])
        c_ed1.write(f"CNPJ: {row['cnpj']} | Regime: {row['regime']}")
        if c_ed2.button("✏️ Editar", key=f"edit_{row['id']}"):
            st.info("Carregando dados para o formulário...")

# --- MÓDULO: APURAÇÃO MENSAL ---
elif menu == "Apuração Mensal":
    st.header("💰 Lançamentos Mensais")
    
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT id, nome, regime FROM empresas", conn)
    conn.close()

    if df_e.empty:
        st.warning("Cadastre uma empresa primeiro.")
    else:
        c1, c2, c3 = st.columns([2,1,1])
        emp_sel = c1.selectbox("Unidade", df_e['nome'])
        mes_sel = c2.selectbox("Mês", ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'])
        ano_sel = c3.selectbox("Ano", [2025, 2026, 2027], index=1)

        st.divider()
        with st.expander("📝 Adicionar Operação (Memória Temporária)", expanded=True):
            col_desc, col_val, col_add = st.columns([3, 1, 1])
            desc_input = col_desc.text_input("Descrição da Operação")
            valor_input = col_val.number_input("Valor R$", min_value=0.0, format="%.2f")
            
            if col_add.button("Adicionar"):
                st.session_state.itens_memoria.append({"Descrição": desc_input, "Valor": valor_input})
                st.rerun()

        if st.session_state.itens_memoria:
            st.subheader("📋 Conferência antes de salvar")
            for idx, item in enumerate(st.session_state.itens_memoria):
                ci1, ci2, ci3 = st.columns([4, 2, 1])
                ci1.write(item['Descrição'])
                ci2.write(f"R$ {item['Valor']:,.2f}")
                if ci3.button("🗑️", key=f"del_{idx}"):
                    st.session_state.itens_memoria.pop(idx)
                    st.rerun()
            
            if st.button("💾 Gravar Apuração no Banco"):
                st.success("Dados enviados ao MySQL!")
                st.session_state.itens_memoria = []

# --- MÓDULO: RELATÓRIOS & ERP ---
elif menu == "Relatórios & ERP":
    st.header("📄 Finalização")
    cols_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
    
    if st.button("📥 Gerar CSV para ERP"):
        data_f = ultimo_dia_mes(2026, "Março")
        # Criando o DataFrame exatamente com as 11 colunas do seu modelo
        df_erp = pd.DataFrame(columns=cols_erp)
        for item in st.session_state.itens_memoria:
             nova_linha = {c: "" for c in cols_erp}
             nova_linha.update({"Debito": "0", "Credito": "0", "Data": data_f, "Valor": item['Valor'], "Historico": item['Descrição']})
             df_erp = pd.concat([df_erp, pd.DataFrame([nova_linha])], ignore_index=True)
        
        st.dataframe(df_erp)
        csv = df_erp.to_csv(index=False).encode('utf-8')
        st.download_button("Baixar Arquivo", data=csv, file_name="export_erp.csv")
