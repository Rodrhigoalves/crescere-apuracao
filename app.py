import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date
import calendar
from fpdf import FPDF
import io

# --- 1. CONFIGURAÇÕES E ESTADO VISUAL ---
st.set_page_config(page_title="Crescere - Apuração", layout="wide", page_icon="📊")

st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .stButton>button { background-color: #6366f1; color: white; border-radius: 6px; border: none; font-weight: 500;}
    .stButton>button:hover { background-color: #4f46e5; color: white; }
    div[data-testid="stForm"] { background-color: #ffffff; padding: 25px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #e5e7eb;}
    .stTextInput>div>div>input, .stNumberInput>div>div>input { border-radius: 6px; }
    h2, h3, h4 { color: #1e293b; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

# --- 2. FUNÇÕES BASE E RELATÓRIO PDF ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except requests.RequestException:
        return None

class PDF_Relatorio(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.set_text_color(30, 58, 138)
        self.cell(0, 10, 'CRESCERE - RELATORIO DE APURACAO FISCAL', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, f'Emissao: {date.today().strftime("%d/%m/%Y")} | Autenticacao Digital', 0, 1, 'C')
        self.ln(5)

# --- 3. MÓDULO DE EMPRESAS ---
def modulo_empresas():
    st.markdown("## 🏢 Gestão de Empresas")
    tab_cad, tab_lista = st.tabs(["📝 Novo Cadastro", "📋 Unidades Cadastradas"])
    
    with tab_cad:
        c_busca, c_btn = st.columns([3,1])
        cnpj_input = c_busca.text_input("Consultar CNPJ na Receita Federal", placeholder="Apenas números")
        if c_btn.button("🔍 Consultar CNPJ", use_container_width=True):
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

        with st.form("form_empresa", clear_on_submit=False):
            st.markdown("#### Dados Cadastrais")
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
            
            if st.form_submit_button("💾 Salvar Empresa", use_container_width=True):
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
                st.success("✅ Empresa salva com sucesso!")
                st.rerun()

    with tab_lista:
        conn = get_db_connection()
        try:
            df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo FROM empresas", conn)
            for _, row in df.iterrows():
                with st.container():
                    col_info, col_btn = st.columns([5, 1])
                    col_info.markdown(f"**{row['nome']}** | {row['tipo']}<br>CNPJ: {row['cnpj']} | Regime: {row['regime']}", unsafe_allow_html=True)
                    if col_btn.button("✏️ Editar", key=f"btn_{row['id']}"):
                        df_edit = pd.read_sql(f"SELECT * FROM empresas WHERE id={row['id']}", conn)
                        st.session_state.dados_form = df_edit.iloc[0].to_dict()
                        st.rerun()
                    st.divider()
        except:
            pass
        conn.close()

# --- 4. MÓDULO DE APURAÇÃO ---
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
            competencia_origem VARCHAR(7)
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
    st.markdown("## 📊 Apuração Mensal")
    with st.expander("⚙️ Manutenção de Banco de Dados", expanded=False):
        if st.button("🚨 Resetar Banco e Criar Tabelas com Contas ERP", use_container_width=True):
            resetar_tabelas_apuracao()
            st.success("Tabelas recriadas e prontas para o ERP!")
            st.rerun()

    conn = get_db_connection()
    try:
        df_empresas = pd.read_sql("SELECT id, nome, cnpj, regime FROM empresas", conn)
        df_operacoes = pd.read_sql("SELECT * FROM operacoes", conn)
    except:
        st.warning("Cadastre uma empresa primeiro ou resete o banco.")
        conn.close(); return

    c_empresa, c_comp = st.columns([3, 1])
    opcoes_empresas = df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1)
    empresa_selecionada = c_empresa.selectbox("Selecione a Empresa", opcoes_empresas)
    empresa_id = int(df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['id'])
    regime_empresa = df_empresas.loc[opcoes_empresas == empresa_selecionada].iloc[0]['regime']
    competencia = c_comp.text_input("Competência (MM/AAAA)", value=date.today().strftime("%m/%Y"))

    st.write("---")
    col_form, col_extrato = st.columns([1, 1], gap="large")

    with col_form:
        with st.form("form_novo_lancamento", clear_on_submit=True):
            st.markdown("### Novo Lançamento")
            c_op, c_val = st.columns([2, 1])
            operacao_nome = c_op.selectbox("Operação", df_operacoes['nome'].tolist())
            valor_base = c_val.number_input("Valor (R$)", min_value=0.01, step=100.00, format="%.2f")
            historico = st.text_input("Observação Livre", placeholder="Opcional...")
            
            st.markdown("<small>Lançamento de competência anterior?</small>", unsafe_allow_html=True)
            c_ret1, c_ret2 = st.columns(2)
            is_retroativo = c_ret1.checkbox("Sim, é retroativo")
            comp_origem = c_ret2.text_input("Mês Origem", placeholder="MM/AAAA", disabled=not is_retroativo)
            
            if st.form_submit_button("➕ Salvar Valor", use_container_width=True):
                mes_str, ano_str = competencia.split('/')
                competencia_db = f"{ano_str}-{mes_str.zfill(2)}"
                op_id = int(df_operacoes[df_operacoes['nome'] == operacao_nome].iloc[0]['id'])
                vp, vc = calcular_impostos(valor_base, operacao_nome, regime_empresa)
                
                ultimo_dia = calendar.monthrange(int(ano_str), int(mes_str))[1]
                data_lancamento = f"{ano_str}-{mes_str.zfill(2)}-{ultimo_dia:02d}"
                
                cursor = conn.cursor()
                cursor.execute("""INSERT INTO lancamentos (empresa_id, operacao_id, competencia, data_lancamento, valor_base, valor_pis, valor_cofins, historico, origem_retroativa, competencia_origem) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                               (empresa_id, op_id, competencia_db, data_lancamento, valor_base, vp, vc, historico, is_retroativo, comp_origem if is_retroativo else None))
                conn.commit()
                st.rerun()

    with col_extrato:
        try:
            m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
            df_lanc = pd.read_sql(f"""SELECT o.nome, l.valor_base, l.valor_pis, l.valor_cofins FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id WHERE l.empresa_id = {empresa_id} AND l.competencia = '{comp_db}' ORDER BY l.id DESC""", conn)
            if not df_lanc.empty:
                st.markdown(f"**Extrato ({regime_empresa})**")
                m1, m2 = st.columns(2)
                m1.metric("PIS Apurado", f"R$ {df_lanc['valor_pis'].sum():,.2f}")
                m2.metric("COFINS Apurado", f"R$ {df_lanc['valor_cofins'].sum():,.2f}")
                st.dataframe(df_lanc, use_container_width=True, hide_index=True)
        except:
            pass
    conn.close()

# --- 5. MÓDULO DE RELATÓRIOS E EXPORTAÇÃO ERP ---
def modulo_relatorios():
    st.markdown("## 📄 Relatórios & Integração ERP")
    st.write("Emissão de documentação e geração do arquivo de importação contábil (XLSX).")
    
    conn = get_db_connection()
    df_empresas = pd.read_sql("SELECT id, nome, cnpj FROM empresas", conn)
    
    if df_empresas.empty:
        st.warning("Cadastre empresas e faça lançamentos primeiro."); conn.close(); return
        
    c1, c2 = st.columns([3, 1])
    emp_sel = c1.selectbox("Empresa", df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1))
    emp_id = int(df_empresas.loc[df_empresas.apply(lambda row: f"{row['nome']} - {row['cnpj']}", axis=1) == emp_sel].iloc[0]['id'])
    nome_empresa = emp_sel.split(' - ')[0]
    competencia = c2.text_input("Competência", value=date.today().strftime("%m/%Y"))
    
    try:
        m, a = competencia.split('/'); comp_db = f"{a}-{m.zfill(2)}"
        query = f"""
            SELECT l.data_lancamento, o.nome as operacao, o.conta_debito, o.conta_credito, o.historico_padrao,
            l.valor_base, l.valor_pis, l.valor_cofins 
            FROM lancamentos l JOIN operacoes o ON l.operacao_id = o.id
            WHERE l.empresa_id = {emp_id} AND l.competencia = '{comp_db}'
        """
        df_dados = pd.read_sql(query, conn)
    except:
        df_dados = pd.DataFrame()

    conn.close()

    if not df_dados.empty:
        col_pdf, col_erp = st.columns(2)
        
        # 1. GERADOR DE PDF
        with col_pdf:
            st.info("Relatório Executivo")
            if st.button("📥 Baixar PDF Consolidado", use_container_width=True):
                pdf = PDF_Relatorio()
                pdf.add_page()
                pdf.set_font('Arial', 'B', 12)
                pdf.cell(0, 10, f'Empresa: {nome_empresa}', 0, 1)
                pdf.cell(0, 10, f'Competencia: {competencia}', 0, 1)
                pdf.ln(5)
                
                pdf.set_font('Arial', 'B', 10)
                pdf.cell(70, 8, 'Operacao', 1)
                pdf.cell(40, 8, 'Base (R$)', 1)
                pdf.cell(40, 8, 'PIS (R$)', 1)
                pdf.cell(40, 8, 'COFINS (R$)', 1)
                pdf.ln()
                
                pdf.set_font('Arial', '', 10)
                for _, row in df_dados.iterrows():
                    pdf.cell(70, 8, str(row['operacao'])[:30], 1)
                    pdf.cell(40, 8, f"{row['valor_base']:,.2f}", 1)
                    pdf.cell(40, 8, f"{row['valor_pis']:,.2f}", 1)
                    pdf.cell(40, 8, f"{row['valor_cofins']:,.2f}", 1)
                    pdf.ln()
                
                pdf.set_font('Arial', 'B', 10)
                pdf.cell(70, 8, 'TOTAIS', 1)
                pdf.cell(40, 8, f"{df_dados['valor_base'].sum():,.2f}", 1)
                pdf.cell(40, 8, f"{df_dados['valor_pis'].sum():,.2f}", 1)
                pdf.cell(40, 8, f"{df_dados['valor_cofins'].sum():,.2f}", 1)
                
                pdf_output = pdf.output(dest='S').encode('latin-1', errors='replace')
                st.download_button("💾 Salvar PDF", data=pdf_output, file_name=f"Apuracao_{comp_db}.pdf", mime="application/pdf", use_container_width=True)

        # 2. GERADOR DE XLSX (ERP) MOLDADO AO SEU ARQUIVO
        with col_erp:
            st.success("Integração Contábil ERP (XLSX)")
            
            # 1. Define o último dia do mês corrente para todos os lançamentos
            ultimo_dia = calendar.monthrange(int(a), int(m))[1]
            data_lancamento_erp = f"{ultimo_dia:02d}/{m.zfill(2)}/{a}"

            # 2. Monta as linhas preenchendo as colunas exatas do modelo
            linhas_erp = []
            for _, row in df_dados.iterrows():
                hist_final = f"{row['historico_padrao']} - {competencia}" 
                
                if row['valor_pis'] > 0:
                    linhas_erp.append({
                        'Lancto Aut.': '',
                        'Debito': row['conta_debito'],
                        'Credito': row['conta_credito'],
                        'Data': data_lancamento_erp,
                        'Valor': row['valor_pis'],
                        'Cod. Historico': '',
                        'Historico': f"PIS: {hist_final}",
                        'Ccusto Debito': '',
                        'Ccusto Credito': '',
                        'Nr.Documento': '',
                        'Complemento': ''
                    })
                
                if row['valor_cofins'] > 0:
                    linhas_erp.append({
                        'Lancto Aut.': '',
                        'Debito': row['conta_debito'],
                        'Credito': row['conta_credito'],
                        'Data': data_lancamento_erp,
                        'Valor': row['valor_cofins'],
                        'Cod. Historico': '',
                        'Historico': f"COFINS: {hist_final}",
                        'Ccusto Debito': '',
                        'Ccusto Credito': '',
                        'Nr.Documento': '',
                        'Complemento': ''
                    })
            
            # Cria o DataFrame preservando a ordem idêntica à do seu modelo
            df_export = pd.DataFrame(linhas_erp, columns=[
                'Lancto Aut.', 'Debito', 'Credito', 'Data', 'Valor', 'Cod. Historico', 
                'Historico', 'Ccusto Debito', 'Ccusto Credito', 'Nr.Documento', 'Complemento'
            ])
            
            # 3. Gerador do XLSX na memória com a biblioteca openpyxl
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_export.to_excel(writer, index=False, sheet_name='Planilha1')
            
            st.download_button(
                label="📥 Baixar Arquivo ERP (Excel XLSX)",
                data=buffer.getvalue(),
                file_name=f"Exportacao_ERP_{comp_db}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            
            st.caption("Arquivo `.xlsx` contendo as 11 colunas originais do modelo. Data fixada no último dia do mês e contas formatadas.")

    else:
        st.info("Nenhum lançamento encontrado para esta empresa e competência.")

# --- 6. NAVEGAÇÃO LATERAL ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135679.png", width=60)
    st.title("Crescere")
    st.caption("Inteligência Contábil")
    st.write("---")
    menu = st.radio("Módulos do Sistema", ["Gestão de Empresas", "Apuração Mensal", "Relatórios e Integração"])

if menu == "Gestão de Empresas":
    modulo_empresas()
elif menu == "Apuração Mensal":
    modulo_apuracao()
elif menu == "Relatórios e Integração":
    modulo_relatorios()
