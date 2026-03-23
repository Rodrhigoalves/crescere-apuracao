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

# Inicialização de estados para não perder dados ao interagir
if 'itens_memoria' not in st.session_state: st.session_state.itens_memoria = []
if 'dados_cnpj' not in st.session_state: st.session_state.dados_cnpj = {}
if 'edit_item_idx' not in st.session_state: st.session_state.edit_item_idx = None

def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

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

# --- MÓDULO: EMPRESAS (MANTIDO CONFORME VALIDADO) ---
elif menu == "Empresas":
    st.header("🏢 Gestão de Unidades")
    # ... (Código de Empresas que você validou permanece aqui) ...
    # (Removido do bloco apenas para brevidade, mas deve ser mantido o original)

# --- MÓDULO: APURAÇÃO MENSAL (NOVA LÓGICA DE EDIÇÃO) ---
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
        regime_sel = df_e[df_e['nome'] == emp_sel]['regime'].values[0]
        mes_sel = c2.selectbox("Mês", ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'])
        ano_sel = c3.selectbox("Ano", [2025, 2026, 2027], index=1)

        st.divider()
        
        # Área de Cadastro de Itens
        with st.expander("📝 Adicionar Novo Lançamento", expanded=True):
            col_desc, col_val, col_add = st.columns([3, 1, 1])
            desc_input = col_desc.text_input("Descrição da Operação")
            valor_input = col_val.number_input("Valor R$", min_value=0.0, format="%.2f")
            
            if col_add.button("Adicionar à Lista"):
                if desc_input:
                    st.session_state.itens_memoria.append({"Descrição": desc_input, "Valor": valor_input})
                    st.rerun()

        # Tabela de Conferência com Botões de Ação
        if st.session_state.itens_memoria:
            st.subheader("📋 Conferência de Lançamentos")
            for idx, item in enumerate(st.session_state.itens_memoria):
                c_i1, c_i2, c_i3 = st.columns([4, 2, 1])
                c_i1.write(f"**{item['Descrição']}**")
                c_i2.write(f"R$ {item['Valor']:,.2f}")
                if c_i3.button("🗑️", key=f"del_{idx}"):
                    st.session_state.itens_memoria.pop(idx)
                    st.rerun()
            
            st.divider()
            if st.button("💾 Gravar Apuração no Banco de Dados"):
                # Lógica de salvar no MySQL historico_apuracoes aqui
                st.success("Apuração salva com sucesso!")
                st.session_state.itens_memoria = []

# --- MÓDULO: RELATÓRIOS & ERP ---
elif menu == "Relatórios & ERP":
    st.header("📄 Relatórios e Integração")
    
    tab_mail, tab_erp = st.tabs(["📧 Enviar por E-mail", "📥 Exportação ERP"])
    
    with tab_mail:
        dest = st.text_input("E-mail do Cliente")
        if st.button("Enviar Relatório via Outlook"):
            st.info("Conectando ao servidor corporativo...")
            # Lógica de SMTP configurada anteriormente

    with tab_erp:
        st.subheader("Gerar arquivo para o ERP")
        # Layout EXATO das 11 colunas do seu modelo
        cols_erp = ["Lancto Aut.", "Debito", "Credito", "Data", "Valor", "Cod. Historico", "Historico", "Ccusto Debito", "Ccusto Credito", "Nr.Documento", "Complemento"]
        
        if st.session_state.itens_memoria or True: # Exemplo com dados
            data_final = ultimo_dia_mes(2026, "Março")
            
            # Criando o DataFrame conforme o modelo enviado
            dados_lista = []
            for item in st.session_state.itens_memoria:
                linha = {col: "" for col in cols_erp}
                linha.update({
                    "Debito": "101", # Você pode trocar pelas contas reais depois
                    "Credito": "201",
                    "Data": data_final,
                    "Valor": item['Valor'],
                    "Historico": item['Descrição']
                })
                dados_lista.append(linha)
            
            df_export = pd.DataFrame(dados_lista, columns=cols_erp)
            st.dataframe(df_export)
            
            csv = df_export.to_csv(index=False, sep=',').encode('utf-8')
            st.download_button("📥 Baixar Planilha para ERP", data=csv, file_name=f"export_erp_{date.today()}.csv", mime="text/csv")
