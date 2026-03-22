import streamlit as st
import mysql.connector
import pandas as pd

# 1. Configuração da Página
st.set_page_config(page_title="Crescere - Apuração", layout="wide")

# 2. Conexão com o UOL (usando Secrets)
def get_db_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

# 3. Inicialização do Banco (Cria tabelas se não existirem)
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS empresas 
                      (id INT AUTO_INCREMENT PRIMARY KEY, nome VARCHAR(255), tipo VARCHAR(50))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS lancamentos 
                      (id INT AUTO_INCREMENT PRIMARY KEY, empresa_id INT, mes VARCHAR(50), 
                       ano INT, faturamento DECIMAL(15,2), pis DECIMAL(15,2), 
                       cofins DECIMAL(15,2), total DECIMAL(15,2))''')
    conn.commit()
    conn.close()

init_db()

# --- MENU LATERAL ---
st.sidebar.title("🛡️ Crescere Navegação")
menu = st.sidebar.radio("Selecione uma opção:", ["Início", "Cadastrar Empresa", "Lançar Apuração", "Relatórios"])

# --- PÁGINA: INÍCIO ---
if menu == "Início":
    st.title("🛡️ Sistema de Apuração PIS/COFINS")
    st.success("✅ Conectado ao banco de dados do UOL.")
    st.write("Use o menu lateral para gerenciar suas empresas e apurações.")

# --- PÁGINA: CADASTRO ---
elif menu == "Cadastrar Empresa":
    st.header("🏢 Cadastro de Empresas (Matriz/Filial)")
    with st.form("form_empresa"):
        nome = st.text_input("Nome da Unidade")
        tipo = st.selectbox("Tipo", ["Matriz", "Filial"])
        if st.form_submit_button("Salvar"):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO empresas (nome, tipo) VALUES (%s, %s)", (nome, tipo))
            conn.commit()
            conn.close()
            st.success(f"Empresa '{nome}' cadastrada com sucesso!")

# --- PÁGINA: LANÇAMENTOS (Cálculo 0,65% e 3%) ---
elif menu == "Lançar Apuração":
    st.header("💰 Nova Apuração Mensal")
    
    # Busca empresas para o selectbox
    conn = get_db_connection()
    df_empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
    conn.close()

    if df_empresas.empty:
        st.warning("⚠️ Cadastre uma empresa primeiro!")
    else:
        with st.form("form_lancamento"):
            empresa_selecionada = st.selectbox("Selecione a Empresa", df_empresas['nome'])
            empresa_id = int(df_empresas[df_empresas['nome'] == empresa_selecionada]['id'].values[0])
            
            col1, col2 = st.columns(2)
            mes = col1.selectbox("Mês", ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"])
            ano = col2.number_input("Ano", min_value=2024, max_value=2030, value=2026)
            
            faturamento = st.number_input("Faturamento Bruto (R$)", min_value=0.0, format="%.2f")
            
            # Cálculos automáticos
            pis = faturamento * 0.0065
            cofins = faturamento * 0.03
            total = pis + cofins
            
            st.write(f"**PIS (0,65%):** R$ {pis:,.2f}")
            st.write(f"**COFINS (3,00%):** R$ {cofins:,.2f}")
            st.write(f"**Total de Impostos:** R$ {total:,.2f}")

            if st.form_submit_button("Gravar Apuração"):
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""INSERT INTO lancamentos (empresa_id, mes, ano, faturamento, pis, cofins, total) 
                                  VALUES (%s, %s, %s, %s, %s, %s, %s)""", 
                               (empresa_id, mes, ano, faturamento, pis, cofins, total))
                conn.commit()
                conn.close()
                st.success("Apuração gravada no histórico do UOL!")

# --- PÁGINA: RELATÓRIOS ---
elif menu == "Relatórios":
    st.header("📊 Histórico de Apurações")
    conn = get_db_connection()
    df_relatorio = pd.read_sql("""SELECT e.nome as Empresa, l.mes, l.ano, l.faturamento, l.pis, l.cofins, l.total 
                                  FROM lancamentos l 
                                  JOIN empresas e ON l.empresa_id = e.id""", conn)
    conn.close()
    
    if df_relatorio.empty:
        st.info("Nenhum dado lançado ainda.")
    else:
        st.dataframe(df_relatorio)
