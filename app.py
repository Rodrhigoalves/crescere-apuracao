import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date
import calendar

# --- 1. CONFIGURAÇÕES E ESTADOS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide", page_icon="🛡️")

# Estado para o formulário de edição de empresas
if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

# --- 2. FUNÇÕES DE BANCO E API ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None

# --- 3. MÓDULO DE EMPRESAS (CRUD) ---
def modulo_empresas():
    st.header("🏢 Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["📝 Formulário", "📋 Unidades Cadastradas"])
    
    with tab_cad:
        c_busca, c_btn = st.columns([3,1])
        cnpj_input = c_busca.text_input("Consultar CNPJ para preencher", placeholder="Apenas números")
        if c_btn.button("🔍 Consultar CNPJ"):
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
            else:
                st.error("Erro na consulta do CNPJ.")

        with st.form("form_empresa", clear_on_submit=False):
            f = st.session_state.dados_form
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
            
            c3, c4, c5 = st.columns([2, 2, 1])
            cnpj = c3.text_input("CNPJ", value=f['cnpj'])
            regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
            
            cnae = st.text_input("CNAE Principal", value=f['cnae'])
            endereco = st.text_area("Endereço Completo", value=f['endereco'])
            
            if st.form_submit_button("💾 SALVAR EMPRESA"):
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Garante que a tabela exista antes de inserir
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS empresas (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nome VARCHAR(255), fantasia VARCHAR(255), cnpj VARCHAR(20),
                    regime VARCHAR(50), tipo VARCHAR(50), cnae VARCHAR(20), endereco TEXT
                )""")
                
                if f['id']: # UPDATE
                    sql = "UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s WHERE id=%s"
                    cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco, f['id']))
                else: # INSERT
                    sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                    cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco))
                
                conn.commit()
                conn.close()
                st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
                st.success("✅ Dados processados com sucesso!")
                st.rerun()

    with tab_lista:
        conn = get_db_connection()
        try:
            df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo FROM empresas", conn)
            for _, row in df.iterrows():
                with st.container():
                    col_info, col_btn = st.columns([5, 1])
                    col_info.markdown(f"**{row['nome']}** \nCNPJ: {row['cnpj']} | Regime: {row['regime']} | Tipo: {row['tipo']}")
                    if col_btn.button("✏️ Editar", key=f"btn_{row['id']}"):
                        df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                        st.session_state.dados_form = df_edit.iloc[0].to_dict()
                        st.rerun()
                    st.divider()
        except:
            st.info("Nenhuma empresa cadastrada no momento.")
        conn.close()

# --- 4. MÓDULO DE APURAÇÃO ---
def resetar_tabelas_apuracao():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS lancamentos")
    cursor.execute("DROP TABLE IF EXISTS fechamentos")
    cursor.execute("DROP TABLE IF EXISTS operacoes")
    
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
            historico TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE fechamentos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa_id INT NOT NULL,
            competencia VARCHAR(7) NOT NULL,
            status ENUM('ABERTO', 'FECHADO') DEFAULT 'FECHADO',
            data_fechamento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    operacoes_padrao = [
        ("Venda de Mercadorias / Produtos", "RECEITA", False, 0.0165, 0.0760),
        ("Venda de Serviços", "RECEITA", False, 0.0165, 0.0760),
        ("Receita Financeira", "RECEITA", False, 0.0065, 0.0400),
        ("Compra Mercador/Insumos", "DESPESA", True, 0.0165, 0.0760),
        ("Combustível (Diesel)", "DESPESA", True, 0.0165, 0.0760),
        ("Energia Elétrica", "DESPESA", True, 0.0165, 0.0760),
        ("Depreciação", "DESPESA", True, 0.0165, 0.0760)
    ]
    
    sql_insert = "INSERT INTO operacoes (nome, tipo, gera_credito, aliquota_pis, aliquota_cofins) VALUES (%s, %s, %s, %s, %s)"
    cursor.executemany(sql_insert, operacoes_padrao)
    conn.commit()
    conn.close()
    st.success("Tabelas de apuração recriadas com sucesso!")

def modulo_apuracao():
    st.header("📊 Apuração Mensal - PIS e COFINS")
    with st.expander("⚙️ Manutenção (Apenas Dev)"):
        if st.button("🚨 Resetar e Criar Tabelas de Apuração"):
            resetar_tabelas_apuracao()

    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes", conn)
    except:
        st.warning("Tabelas base ainda não existem. Cadastre uma empresa ou clique no botão de Manutenção acima.")
        conn.close()
        return

    if df_empresas.empty:
        st.warning("Nenhuma empresa cadastrada. Vá até a Gestão de Empresas primeiro.")
        conn.close()
        return
        
    c1, c2 = st.columns([2, 1])
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = c1.selectbox("Selecione a Empresa", opcoes_empresas)
    empresa_id = df_empresas.loc[opcoes_empresas == empresa_selecionada, 'id'].values[0]
    
    competencia = c2.text_input("Competência (MM/AAAA)", value=date.today().strftime("%m/%Y"))

    st.divider()
    
    with st.form("form_lancamento", clear_on_submit=True):
        st.subheader("Novo Lançamento")
        col_op, col_val = st.columns([3, 1])
        
        operacao_nome = col_op.selectbox("Operação", df_operacoes['nome'].tolist())
        valor_base = col_val.number_input("Valor (R$)", min_value=0.01, step=100.00, format="%.2f")
        historico = st.text_input("Histórico (Opcional)", placeholder="Ex: Referente às notas de venda...")
        
        if st.form_submit_button("➕ Incluir Lançamento"):
            op_data = df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]
            op_id = int(op_data['id'])
            
            valor_pis = valor_base * float(op_data['aliquota_pis'])
            valor_cofins = valor_base * float(op_data['aliquota_cofins'])
            
            mes, ano = map(int, competencia.split('/'))
            ultimo_dia = calendar.monthrange(ano, mes)[1]
            data_lancamento = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
            competencia_db = f"{ano}-{mes:02d}"
            
            cursor = conn.cursor()
            sql = """INSERT INTO lancamentos 
                     (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(sql, (int(empresa_id), op_id, competencia_db, data_lancamento, valor_base, valor_pis, valor_cofins, historico))
            conn.commit()
            
            st.success(f"Lançamento registrado! PIS: R$ {valor_pis:.2f} | COFINS: R$ {valor_cofins:.2f}")
    conn.close()

# --- 5. MENU LATERAL ---
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3135/3135679.png", width=80)
st.sidebar.title("Navegação")
menu = st.sidebar.radio("Escolha o módulo:", ["Página Inicial", "Gestão de Empresas", "Apuração Mensal"])

if menu == "Página Inicial":
    st.title("Bem-vindo ao Crescere")
    st.write("Utilize o menu lateral para navegar entre os módulos.")
    st.info("👈 Selecione 'Gestão de Empresas' para cadastrar ou 'Apuração Mensal' para lançar valores.")
elif menu == "Gestão de Empresas":
    modulo_empresas()
elif menu == "Apuração Mensal":
    modulo_apuracao()
