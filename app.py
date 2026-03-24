import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date, timedelta
import calendar
from fpdf import FPDF
import io
import os

# --- 1. CONFIGURAÇÕES VISUAIS E ESTADOS ---
st.set_page_config(page_title="Crescere - Apuração Fiscal", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #004b87; color: white; border-radius: 4px; border: none; font-weight: 500;}
    .stButton>button:hover { background-color: #003366; color: white; }
    div[data-testid="stForm"], .css-1d391kg { background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;}
    h1, h2, h3, h4 { color: #0f172a; font-weight: 600; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
</style>
""", unsafe_allow_html=True)

# Lógica de Competência Padrão (Mês Anterior)
hoje = date.today()
primeiro_dia_mes_atual = hoje.replace(day=1)
ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
competencia_padrao = ultimo_dia_mes_anterior.strftime("%m/%Y")

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

if 'rascunho_lancamentos' not in st.session_state:
    st.session_state.rascunho_lancamentos = []

# --- 2. FUNÇÕES BASE ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None

# --- 3. GESTÃO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("## Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["Novo Cadastro", "Unidades Cadastradas"])
    
    with tab_cad:
        c_busca, c_btn = st.columns([3,1])
        cnpj_input = c_busca.text_input("Consultar CNPJ na Receita Federal", placeholder="Apenas números")
        if c_btn.button("Consultar CNPJ", use_container_width=True):
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

        f = st.session_state.dados_form
        c1, c2 = st.columns(2)
        nome = c1.text_input("Razão Social", value=f['nome'])
        fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
        
        c3, c4, c5 = st.columns([2, 1.5, 1.5])
        cnpj = c3.text_input("CNPJ", value=f['cnpj'])
        regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
        tipo = c5.selectbox("Tipo de Unidade", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
        
        cnae = st.text_input("CNAE Principal", value=f['cnae'])
        endereco = st.text_area("Endereço Completo", value=f['endereco'])
        
        if st.button("Salvar Empresa", use_container_width=True):
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
            st.success("Empresa salva com sucesso.")
            st.rerun()

    with tab_lista:
        conn = get_db_connection()
        try:
            df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo FROM empresas", conn)
            for _, row in df.iterrows():
                col_info, col_btn = st.columns([5, 1])
                col_info.markdown(f"**{row['nome']}** | {row['tipo']}<br>CNPJ: {row['cnpj']} | Regime: {row['regime']}", unsafe_allow_html=True)
                if col_btn.button("Editar", key=f"btn_{row['id']}"):
                    df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                    st.session_state.dados_form = df_edit.iloc[0].to_dict()
                    st.rerun()
                st.divider()
        except:
            pass
        conn.close()

# --- 4. MÓDULO DE APURAÇÃO E RASCUNHO ---
def resetar_tabelas_apuracao():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS lancamentos")
    cursor.execute("DROP TABLE IF EXISTS operacoes")
    
    cursor.execute("""
        CREATE TABLE operacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            tipo ENUM('RECEITA', 'DESPESA') NOT NULL,
            gera_credito BOOLEAN DEFAULT FALSE,
            conta_debito VARCHAR(50),
            conta_credito VARCHAR(50),
            historico_padrao VARCHAR(255)
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
            historico TEXT,
            origem_retroativa BOOLEAN DEFAULT FALSE,
            competencia_origem VARCHAR(7),
            usuario_registro VARCHAR(100),
            motivo_alteracao VARCHAR(255) DEFAULT NULL,
            data_alteracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    
    operacoes_padrao = [
        ("Venda de Mercadorias / Produtos", "RECEITA", False, "3.1.1.01", "2.1.2.05", "Venda Ref. NF"),
        ("Venda de Serviços", "RECEITA", False, "3.1.1.02", "2.1.2.05", "Servico Ref. NF"),
        ("Receita Financeira", "RECEITA", False, "3.2.1.01", "2.1.2.05", "Rec. Financ. Apurada"),
        ("Compra Mercador/Insumos", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Compra Insumo Ref. NF"),
        ("Depreciação", "DESPESA", True, "1.1.3.01", "2.1.1.01", "Quota Depreciacao")
    ]
    cursor.executemany("INSERT INTO operacoes (nome, tipo, gera_credito, conta_debito, conta_credito, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s)", operacoes_padrao)
    conn.commit()
    conn.close()

def calcular_impostos(valor_base, operacao_nome, regime_empresa):
    if regime_empresa == "Lucro Real":
        if operacao_nome == "Receita Financeira":
            return valor_base * 0.0065, valor_base * 0.0400
        else:
            return valor_base * 0.0165, valor_base * 0.0760
    else:
        return valor_base * 0.0065, valor_base * 0.0300

def modulo_apuracao():
    st.markdown("## Apuração Mensal")
    
    with st.expander("Manutenção de Banco de Dados", expanded=False):
        if st.button("Resetar Banco e Aplicar Estrutura Final", use_container_width=True):
            resetar_tabelas_apuracao()
            st.success("Tabelas recriadas. Estrutura de auditoria pronta.")
            st.rerun()

    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes", conn)
    except:
        st.warning("Banco de dados não encontrado. Realize o Reset na opção Manutenção acima.")
        conn.close(); return

    c_empresa, c_comp, c_user = st.columns([2, 1, 1])
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = c_empresa.selectbox("Empresa Ativa", opcoes_empresas)
    empresa_id = int(df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['id'])
    regime_empresa = df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['regime']
    
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=competencia_padrao)
    usuario_atual = c_user.text_input("Usuário (Auditoria)", value="Rodrigo")

    st.write("---")
    
    col_entrada, col_rascunho = st.columns([1, 1.2], gap="large")

    with col_entrada:
        st.markdown("#### Inserção de Dados")
        operacao_nome = st.selectbox("Operação", df_operacoes['nome'].tolist())
        valor_base = st.number_input("Valor (R$)", min_value=0.00, step=100.00, format="%.2f")
        historico = st.text_input("Observação Livre", placeholder="Opcional...")
        
        is_retroativo = st.checkbox("Lançamento de competência anterior (Retroativo)")
        comp_origem = st.text_input("Mês de Origem", placeholder="MM/AAAA", disabled=not is_retroativo)
        
        if st.button("Adicionar à Lista", use_container_width=True):
            if valor_base > 0:
                vp, vc = calcular_impostos(valor_base, operacao_nome, regime_empresa)
                op_id = int(df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]['id'])
                
                novo_item = {
                    "empresa_id": empresa_id,
                    "operacao_nome": operacao_nome,
                    "operacao_id": op_id,
                    "valor_base": valor_base,
                    "valor_pis": vp,
                    "valor_cofins": vc,
                    "historico": historico,
                    "is_retroativo": is_retroativo,
                    "comp_origem": comp_origem if is_retroativo else None
                }
                st.session_state.rascunho_lancamentos.append(novo_item)
                st.rerun()
            else:
                st.error("O valor base deve ser maior que zero.")

    with col_rascunho:
        st.markdown("#### Lançamentos Pendentes (Pré-Banco)")
        if len(st.session_state.rascunho_lancamentos) > 0:
            df_rascunho = pd.DataFrame(st.session_state.rascunho_lancamentos)
            df_view = df_rascunho[['operacao_nome', 'valor_base', 'valor_pis', 'valor_cofins']].copy()
            df_view.columns = ['Operação', 'Base', 'PIS', 'COFINS']
            st.dataframe(df_view, use_container_width=True, hide_index=True)
            
            col_save, col_clear = st.columns(2)
            if col_save.button("Gravar no Banco de Dados", type="primary", use_container_width=True):
                mes_str, ano_str = competencia.split('/')
                competencia_db = f"{ano_str}-{mes_str.zfill(2)}"
                ultimo_dia = calendar.monthrange(int(ano_str), int(mes_str))[1]
                data_lancamento = f"{ano_str}-{mes_str.zfill(2)}-{ultimo_dia:02d}"
                
                cursor = conn.cursor()
                for item in st.session_state.rascunho_lancamentos:
                    cursor.execute("""
                        INSERT INTO lancamentos 
                        (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, origem_retroativa, competencia_origem, usuario_registro) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                        (item['empresa_id'], item['operacao_id'], competencia_db, data_lancamento, item['valor_base'], item['valor_pis'], item['valor_cofins'], item['historico'], item['is_retroativo'], item['comp_origem'], usuario_atual))
                
                conn.commit()
                st.session_state.rascunho_lancamentos = []
                st.success("Lançamentos gravados com sucesso!")
                st.rerun()
                
            if col_clear.button("Apagar Lista e Refazer", use_container_width=True):
                st.session_state.rascunho_lancamentos = []
                st.rerun()
        else:
            st.info("A lista está vazia. Adicione dados ao lado.")

    st.write("---")
    st.markdown("#### Extrato Consolidado")
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        df_lanc = pd.read_sql(f"""SELECT o.nome, l.valor_base, l.valor_pis, l.valor_cofins, l.historico FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {empresa_id} AND l.competencia = '{comp_db}' ORDER BY l.id DESC""", conn)
        if not df_lanc.empty:
            st.dataframe(df_lanc, use_container_width=True, hide_index=True)
    except:
        pass
        
    conn.close()

# --- 5. NAVEGAÇÃO LATERAL ---
with st.sidebar:
    # Verificação de segurança: carrega a imagem apenas se ela existir no diretório
    if os.path.exists("image_b8c586.png"):
        st.image("image_b8c586.png", width=160)
    else:
        st.markdown("<h2 style='color: #004b87; text-align: center;'>🛡️ CRESCERE</h2>", unsafe_allow_html=True)
        
    st.write("---")
    menu = st.radio("Módulos do Sistema", ["Gestão de Empresas", "Apuração Mensal"])

if menu == "Gestão de Empresas":
    modulo_empresas()
elif menu == "Apuração Mensal":
    modulo_apuracao()
