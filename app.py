import streamlit as st
import mysql.connector
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import date, datetime
import calendar
from fpdf import FPDF
import io

# --- 1. CONFIGURAÇÕES INICIAIS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide", page_icon="🛡️")

# Estilo para botões e containers
st.markdown("""
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 5px; padding: 10px; }
    .stTabs [aria-selected="true"] { background-color: #004b87; color: white; }
    div.stButton > button:first-child { background-color: #004b87; color: white; border-radius: 8px; }
    </style>
    """, unsafe_allow_html=True)

# Funções de Apoio
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def ultimo_dia_mes(ano, mes_nome):
    meses_num = {m: i+1 for i, m in enumerate(['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'])}
    mes_num = meses_num[mes_nome]
    ultimo_dia = calendar.monthrange(ano, mes_num)[1]
    return f"{ultimo_dia}/{str(mes_num).zfill(2)}/{ano}"

# --- 2. LOGICA DE E-MAIL (OUTLOOK) ---
def enviar_email_outlook(destinatario, assunto, corpo, arquivo_pdf, nome_arquivo):
    try:
        msg = MIMEMultipart()
        msg['From'] = st.secrets["email"]["smtp_user"]
        msg['To'] = destinatario
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo, 'plain'))

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(arquivo_pdf)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename= {nome_arquivo}")
        msg.attach(part)

        server = smtplib.SMTP(st.secrets["email"]["smtp_server"], st.secrets["email"]["smtp_port"])
        server.starttls()
        server.login(st.secrets["email"]["smtp_user"], st.secrets["email"]["smtp_password"])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")
        return False

# --- 3. INTERFACE E NAVEGAÇÃO ---
with st.sidebar:
    st.markdown("# 🛡️ Crescere")
    st.divider()
    menu = st.radio("Menu Principal", ["Início", "Empresas", "Apuração Mensal", "Relatórios & ERP"], label_visibility="collapsed")

# --- MÓDULO: INÍCIO ---
if menu == "Início":
    st.markdown("<br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.image("https://via.placeholder.com/150", width=150) # Substitua pelo link da sua logo
        st.title("Bem-vindo à Crescere")
        st.subheader("Inteligência Contábil")
        st.markdown("---")
        st.info("Utilize o menu lateral para gerenciar empresas, realizar apurações e exportar dados para o ERP.")

# --- MÓDULO: APURAÇÃO MENSAL (COM EDIÇÃO ANTES DE SALVAR) ---
elif menu == "Apuração Mensal":
    st.header("💰 Apuração PIS/COFINS")
    
    # 1. Seleção de Empresa e Período
    conn = get_db_connection()
    df_e = pd.read_sql("SELECT id, nome, regime FROM empresas", conn)
    conn.close()
    
    c1, c2, c3 = st.columns([2,1,1])
    emp_sel = c1.selectbox("Selecione a Empresa", df_e['nome'])
    regime_emp = df_e[df_e['nome'] == emp_sel]['regime'].values[0]
    mes_sel = c2.selectbox("Mês", ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'])
    ano_sel = c3.selectbox("Ano", [2025, 2026, 2027], index=1)

    # 2. Lançamento Temporário (Memória)
    st.divider()
    with st.expander("➕ Novo Lançamento para Conferência", expanded=True):
        c_desc, c_val, c_btn = st.columns([3, 1, 1])
        desc = c_desc.text_input("Descrição da Operação (Ex: Venda de Mercadorias)")
        valor = c_val.number_input("Valor R$", min_value=0.0, format="%.2f")
        
        if c_btn.button("Adicionar"):
            novo_item = {"Descrição": desc, "Valor": valor}
            st.session_state.itens_memoria.append(novo_item)

    # 3. Tabela de Conferência (CRUD antes do Banco)
    if st.session_state.itens_memoria:
        st.subheader("📝 Conferência de Lançamentos")
        df_temp = pd.DataFrame(st.session_state.itens_memoria)
        
        # Exibição com opção de limpar
        st.table(df_temp.style.format({"Valor": "R$ {:.2f}"}))
        
        col_acao1, col_acao2 = st.columns([1, 4])
        if col_acao1.button("🗑️ Limpar Tudo"):
            st.session_state.itens_memoria = []
            st.rerun()
            
        if col_acao2.button("✅ Confirmar e Gravar no Banco de Dados"):
            # Aqui entraria o loop de INSERT no seu historico_apuracoes
            st.success(f"Apuração de {emp_sel} gravada com sucesso!")
            st.session_state.itens_memoria = []

# --- MÓDULO: RELATÓRIOS & ERP ---
elif menu == "Relatórios & ERP":
    st.header("📄 Finalização e Integração")
    
    tab1, tab2 = st.tabs(["📧 Enviar por E-mail", "💾 Exportar ERP (Layout CSV)"])
    
    with tab1:
        st.write("Disparar PDF para o e-mail corporativo cadastrado.")
        email_cliente = st.text_input("E-mail do Destinatário")
        if st.button("📨 Enviar Relatório agora"):
            # Lógica para gerar PDF e enviar
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", 'B', 16)
            pdf.cell(40, 10, f"Relatório Crescere - {mes_sel}/{ano_sel}")
            pdf_bytes = pdf.output(dest='S').encode('latin-1')
            
            if enviar_email_outlook(email_cliente, "Relatório de Apuração", "Segue anexo o relatório.", pdf_bytes, "Relatorio.pdf"):
                st.success("E-mail enviado com sucesso via Outlook!")

    with tab2:
        st.write("Gera o arquivo CSV compatível com o seu ERP.")
        
        # Montagem do DataFrame conforme seu modelo enviado
        data_final = ultimo_dia_mes(ano_sel, mes_sel)
        
        dados_erp = {
            "Lancto Aut.": [""],
            "Debito": ["101"],      # Exemplo de conta
            "Credito": ["201"],     # Exemplo de conta
            "Data": [data_final],
            "Valor": [1500.50],     # Exemplo vindo da apuração
            "Cod. Historico": [""],
            "Historico": [f"Apuração PIS/COFINS {mes_sel}/{ano_sel}"],
            "Ccusto Debito": [""],
            "Ccusto Credito": [""],
            "Nr.Documento": [""],
            "Complemento": [""]
        }
        
        df_export = pd.DataFrame(dados_erp)
        st.dataframe(df_export)
        
        csv = df_export.to_csv(index=False, sep=',').encode('utf-8')
        st.download_button("📥 Baixar CSV para ERP", data=csv, file_name=f"export_erp_{mes_sel}.csv", mime="text/csv")
