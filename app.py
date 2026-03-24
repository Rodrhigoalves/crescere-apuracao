import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date
import calendar

# --- 1. CONFIGURAÇÕES E ESTADOS ---
st.set_page_config(page_title="Crescere - Inteligência Contábil", layout="wide", page_icon="🛡️")

# Estilo Customizado (Recuperando sua interface Clean original)
st.markdown("""
<style>  
    .main { background-color: #f5f7f9; }  
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #004b87; color: white; }  
    .stTextInput>div>div>input { border-radius: 5px; }  
</style>  
""", unsafe_allow_html=True)

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
                
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS empresas (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nome VARCHAR(255), fantasia VARCHAR(255), cnpj VARCHAR(20),
                    regime VARCHAR(50), tipo VARCHAR(50), cnae VARCHAR(20), endereco TEXT
                )""")
                
                if f['id']: 
                    sql = "UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s WHERE id=%s"
                    cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco, f['id']))
                else: 
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

# --- 4. FUNÇÕES E MÓDULO DE APURAÇÃO ---
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
    st.markdown("<h2 style='text-align: center; color: #1E3A8A;'>Painel de Apuração - PIS e COFINS</h2>", unsafe_allow_html=True)
    st.divider()

    conn = get_db_connection()
    
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes", conn)
    except:
        st.error("⚠️ As tabelas base não foram encontradas.")
        with st.expander("⚙️ Ferramentas de Sistema (Setup Inicial)"):
            if st.button("🚨 Inicializar Tabelas de Apuração"):
                resetar_tabelas_apuracao()
                st.rerun()
        conn.close()
        return

    if df_empresas.empty:
        st.warning("Nenhuma empresa cadastrada no sistema. Vá ao menu 'Gestão de Empresas'.")
        conn.close()
        return

    col_filtro1, col_filtro2, col_filtro3 = st.columns([2, 1, 1])
    
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = col_filtro1.selectbox("Empresa Ativa", opcoes_empresas, label_visibility="collapsed")
    empresa_id = df_empresas.loc[opcoes_empresas == empresa_selecionada, 'id'].values[0]
    
    competencia = col_filtro2.text_input("Competência", value=date.today().strftime("%m/%Y"), help="Formato MM/AAAA")
    
    try:
        mes_str, ano_str = competencia.split('/')
        competencia_db = f"{ano_str}-{mes_str.zfill(2)}"
    except:
        competencia_db = ""

    df_lancamentos = pd.DataFrame()
    if competencia_db:
        query = f"""
            SELECT l.data_lancamento, o.nome as operacao, l.valor_base, l.valor_pis, l.valor_cofins 
            FROM lancamentos l
            JOIN operacoes o ON l.operacao_id = o.id
            WHERE l.empresa_id = {empresa_id} AND l.competencia = '{competencia_db}'
            ORDER BY l.data_lancamento DESC, l.id DESC
        """
        try:
            df_lancamentos = pd.read_sql(query, conn)
        except:
            pass 

    st.write("") 
    m1, m2, m3 = st.columns(3)
    
    total_base = df_lancamentos['valor_base'].sum() if not df_lancamentos.empty else 0.0
    total_pis = df_lancamentos['valor_pis'].sum() if not df_lancamentos.empty else 0.0
    total_cofins = df_lancamentos['valor_cofins'].sum() if not df_lancamentos.empty else 0.0

    m1.metric("Base de Cálculo Total", f"R$ {total_base:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    m2.metric("PIS Apurado", f"R$ {total_pis:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    m3.metric("COFINS Apurado", f"R$ {total_cofins:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    st.divider()

    col_form, col_extrato = st.columns([1, 1.5], gap="large")

    with col_form:
        st.markdown("#### 📥 Novo Lançamento")
        with st.form("form_novo_lancamento", clear_on_submit=True):
            operacao_nome = st.selectbox("Tipo de Operação", df_operacoes['nome'].tolist())
            valor_base = st.number_input("Valor da Base (R$)", min_value=0.01, step=100.00, format="%.2f")
            historico = st.text_input("Histórico / Observação", placeholder="Ex: NF 1234 a 1250...")
            
            submit = st.form_submit_button("Registrar Valor", use_container_width=True)
            
            if submit:
                if not competencia_db:
                    st.error("Formato de competência inválido. Use MM/AAAA.")
                else:
                    op_data = df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]
                    op_id = int(op_data['id'])
                    
                    valor_pis = valor_base * float(op_data['aliquota_pis'])
                    valor_cofins = valor_base * float(op_data['aliquota_cofins'])
                    
                    mes, ano = map(int, competencia.split('/'))
                    ultimo_dia = calendar.monthrange(ano, mes)[1]
                    data_lancamento = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
                    
                    cursor = conn.cursor()
                    sql = """INSERT INTO lancamentos 
                             (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico) 
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
                    cursor.execute(sql, (int(empresa_id), op_id, competencia_db, data_lancamento, valor_base, valor_pis, valor_cofins, historico))
                    conn.commit()
                    st.rerun() 

    with col_extrato:
        st.markdown(f"#### 📄 Extrato da Competência ({competencia})")
        if not df_lancamentos.empty:
            df_view = df_lancamentos.copy()
            df_view['data_lancamento'] = pd.to_datetime(df_view['data_lancamento']).dt.strftime('%d/%m/%Y')
            df_view.rename(columns={
                'data_lancamento': 'Data',
                'operacao': 'Operação',
                'valor_base': 'Base (R$)',
                'valor_pis': 'PIS (R$)',
                'valor_cofins': 'COFINS (R$)'
            }, inplace=True)
            
            st.dataframe(df_view, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum lançamento registrado para esta empresa nesta competência.")

    conn.close()

# --- 5. MENU LATERAL E EXECUÇÃO PRINCIPAL ---
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3135/3135679.png", width=80)
st.sidebar.title("🛡️ Crescere")
st.sidebar.caption("Inteligência Contábil")
menu = st.sidebar.radio("Navegação", ["Página Inicial", "Gestão de Empresas", "Apuração Mensal"])

if menu == "Página Inicial":
    st.title("Bem-vindo ao Crescere")
    st.write("Utilize o menu lateral para navegar entre os módulos.")
    st.info("👈 Selecione 'Gestão de Empresas' para cadastrar ou 'Apuração Mensal' para lançar valores.")
elif menu == "Gestão de Empresas":
    modulo_empresas()
elif menu == "Apuração Mensal":
    modulo_apuracao()
