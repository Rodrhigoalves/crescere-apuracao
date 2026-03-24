import streamlit as st
import mysql.connector
from datetime import date
import calendar

# --- FUNÇÃO DE CONEXÃO ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

# --- 1. RESET E CRIAÇÃO DO BANCO (RODAR APENAS 1 VEZ) ---
def resetar_tabelas_apuracao():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1.1 Apagar tabelas antigas de teste
    cursor.execute("DROP TABLE IF EXISTS lancamentos")
    cursor.execute("DROP TABLE IF EXISTS fechamentos")
    cursor.execute("DROP TABLE IF EXISTS operacoes")
    
    # 1.2 Criar Tabela de Operações
    cursor.execute("""
        CREATE TABLE operacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            tipo ENUM('RECEITA', 'DESPESA') NOT NULL,
            gera_credito BOOLEAN DEFAULT FALSE,
            aliquota_pis DECIMAL(5,4) DEFAULT 0.0165,
            aliquota_cofins DECIMAL(5,4) DEFAULT 0.0760
        )
    """)
    
    # 1.3 Criar Tabela de Lançamentos
    cursor.execute("""
        CREATE TABLE lancamentos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa_id INT NOT NULL,
            operacao_id INT NOT NULL,
            competencia VARCHAR(7) NOT NULL,
            data_lancamento DATE NOT NULL,
            valor_base DECIMAL(15,2) NOT NULL,
            valor_pis DECIMAL(15,2) NOT NULL,
            valor_cofins DECIMAL(15,2) NOT NULL,
            historico TEXT,
            FOREIGN KEY (operacao_id) REFERENCES operacoes(id)
        )
    """)
    
    # 1.4 Criar Tabela de Fechamentos
    cursor.execute("""
        CREATE TABLE fechamentos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa_id INT NOT NULL,
            competencia VARCHAR(7) NOT NULL,
            status ENUM('ABERTO', 'FECHADO') DEFAULT 'FECHADO',
            data_fechamento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 1.5 Inserir as Operações Padrão do Sistema
    operacoes_padrao = [
        ("Venda de Mercadorias / Produtos", "RECEITA", False, 0.0165, 0.0760),
        ("Venda de Serviços", "RECEITA", False, 0.0165, 0.0760),
        ("Receita Financeira", "RECEITA", False, 0.0065, 0.0400), # Alíquota diferenciada
        ("Compra Mercador/Insumos", "DESPESA", True, 0.0165, 0.0760),
        ("Combustível (Diesel)", "DESPESA", True, 0.0165, 0.0760),
        ("Energia Elétrica", "DESPESA", True, 0.0165, 0.0760),
        ("Depreciação", "DESPESA", True, 0.0165, 0.0760)
    ]
    
    sql_insert = "INSERT INTO operacoes (nome, tipo, gera_credito, aliquota_pis, aliquota_cofins) VALUES (%s, %s, %s, %s, %s)"
    cursor.executemany(sql_insert, operacoes_padrao)
    
    conn.commit()
    conn.close()
    st.success("Tabelas de apuração recriadas com sucesso e limpas!")

# --- 2. INTERFACE DE APURAÇÃO ---
def modulo_apuracao():
    st.header("📊 Apuração Mensal - PIS e COFINS")
    
    # Botão de setup (Esconda isso depois que rodar a primeira vez)
    with st.expander("⚙️ Ferramentas de Manutenção (Apenas Dev)"):
        if st.button("🚨 Resetar Banco de Apuração (Apagar Tudo)"):
            resetar_tabelas_apuracao()

    conn = get_db_connection()
    
    # Pegar empresas para o Selectbox
    df_empresas = pd.read_sql("SELECT id, nome, cnpj FROM empresas", conn)
    if df_empresas.empty:
        st.warning("Cadastre uma empresa primeiro.")
        return
        
    # Pegar operações para o Selectbox
    df_operacoes = pd.read_sql("SELECT * FROM operacoes", conn)
    
    c1, c2 = st.columns([2, 1])
    empresa_selecionada = c1.selectbox("Selecione a Empresa", df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1))
    empresa_id = df_empresas.iloc[df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1) == empresa_selecionada]['id'].values[0]
    
    competencia = c2.text_input("Competência (MM/AAAA)", value=date.today().strftime("%m/%Y"))

    st.divider()
    
    with st.form("form_lancamento", clear_on_submit=True):
        st.subheader("Novo Lançamento")
        col_op, col_val = st.columns([3, 1])
        
        operacao_nome = col_op.selectbox("Operação", df_operacoes['nome'].tolist())
        valor_base = col_val.number_input("Valor (R$)", min_value=0.01, step=100.00, format="%.2f")
        
        historico = st.text_input("Histórico (Opcional)", placeholder="Ex: Referente às notas de venda...")
        
        if st.form_submit_button("➕ Incluir Lançamento"):
            # Lógica de Cálculo Automático
            op_data = df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]
            op_id = int(op_data['id'])
            aliq_pis = float(op_data['aliquota_pis'])
            aliq_cofins = float(op_data['aliquota_cofins'])
            
            valor_pis = valor_base * aliq_pis
            valor_cofins = valor_base * aliq_cofins
            
            # Pegar o último dia do mês para a data_lancamento
            mes, ano = map(int, competencia.split('/'))
            ultimo_dia = calendar.monthrange(ano, mes)[1]
            data_lancamento = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
            
            competencia_db = f"{ano}-{mes:02d}"
            
            # Salvar no Banco
            cursor = conn.cursor()
            sql = """INSERT INTO lancamentos 
                     (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (empresa_id, op_id, competencia_db, data_lancamento, valor_base, valor_pis, valor_cofins, historico))
            conn.commit()
            
            st.success(f"Lançamento registrado! PIS: R$ {valor_pis:.2f} | COFINS: R$ {valor_cofins:.2f}")
            st.rerun()

    conn.close()

 Chamada do módulo no fluxo do menu
  elif menu == "Apuração Mensal":
     modulo_apuracao()
