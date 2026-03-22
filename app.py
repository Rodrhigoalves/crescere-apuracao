import streamlit as st
import mysql.connector
import pandas as pd
import requests
from datetime import date
from fpdf import FPDF
import io

# --- CONFIGURAÇÕES ---
st.set_page_config(page_title="Crescere - PIS/COFINS", layout="wide", page_icon="🛡️")

# Estilo Customizado para Interface Clean
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #004b87; color: white; }
    .stTextInput>div>div>input { border-radius: 5px; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNÇÕES DE NÚCLEO ---
def get_db_connection():
    return mysql.connector.connect(**st.secrets["mysql"])

def consultar_cnpj(cnpj_limpo):
    url = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
    try:
        response = requests.get(url, timeout=10)
        return response.json() if response.status_code == 200 else None
    except:
        return None

# --- CLASSE DE RELATÓRIO PDF ---
class PDF_Relatorio(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, 'CRESCERE - RELATÓRIO DE APURAÇÃO FISCAL', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.cell(0, 5, f'Data de Emissão: {date.today().strftime("%d/%m/%Y")}', 0, 1, 'C')
        self.ln(10)

# --- ESTADOS DO SISTEMA ---
if 'dados_form' not in st.session_state:
    st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}

# --- SIDEBAR NAVEGAÇÃO ---
with st.sidebar:
    st.title("🛡️ Crescere")
    st.caption("v1.2 - Inteligência Contábil")
    menu = st.radio("Módulos", ["Início", "Gestão de Empresas", "Apuração Mensal", "Relatórios & Exportação"])

# --- MÓDULO: GESTÃO DE EMPRESAS (CRUD OTIMIZADO) ---
if menu == "Gestão de Empresas":
    st.subheader("🏢 Cadastro e Edição de Unidades")
    
    tab_cad, tab_lista = st.tabs(["📝 Formulário", "📋 Unidades Cadastradas"])

    with tab_cad:
        # Busca CNPJ integrada no topo do form
        c_busca, c_btn = st.columns([3,1])
        cnpj_input = c_busca.text_input("Consultar CNPJ para preencher", placeholder="00.000.000/0000-00")
        if c_btn.button("🔍 Consultar"):
            res = consultar_cnpj(cnpj_input.replace(".","").replace("/","").replace("-",""))
            if res and res.get('status') != 'ERROR':
                st.session_state.dados_form.update({
                    "nome": res.get('nome', ''),
                    "fantasia": res.get('fantasia', ''),
                    "cnpj": res.get('cnpj', ''),
                    "cnae": res['atividade_principal'][0].get('code', '') if 'atividade_principal' in res else "",
                    "endereco": f"{res.get('logradouro')}, {res.get('numero')} - {res.get('uf')}"
                })
                st.rerun()
            else:
                st.error("Erro na consulta.")

        # Formulário Unificado (Insert/Update)
        with st.form("form_empresa", clear_on_submit=False):
            f = st.session_state.dados_form
            c1, c2 = st.columns(2)
            nome = c1.text_input("Razão Social", value=f['nome'])
            fanta = c2.text_input("Nome Fantasia", value=f['fantasia'])
            
            c3, c4, c5 = st.columns([2, 2, 1])
            cnpj = c3.text_input("CNPJ", value=f['cnpj'])
            regime = c4.selectbox("Regime Tributário", ["Lucro Real", "Lucro Presumido"], index=0 if f['regime'] == "Lucro Real" else 1)
            tipo = c5.selectbox("Tipo", ["Matriz", "Filial"], index=0 if f['tipo'] == "Matriz" else 1)
            
            cnae = st.text_input("CNAE", value=f['cnae'])
            endereco = st.text_area("Endereço", value=f['endereco'])

            if st.form_submit_button("💾 SALVAR ALTERAÇÕES"):
                conn = get_db_connection()
                cursor = conn.cursor()
                if f['id']: # UPDATE
                    sql = "UPDATE empresas SET nome=%s, fantasia=%s, cnpj=%s, regime=%s, tipo=%s, cnae=%s, endereco=%s WHERE id=%s"
                    cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco, f['id']))
                else: # INSERT
                    sql = "INSERT INTO empresas (nome, fantasia, cnpj, regime, tipo, cnae, endereco) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                    cursor.execute(sql, (nome, fanta, cnpj, regime, tipo, cnae, endereco))
                
                conn.commit()
                conn.close()
                st.session_state.dados_form = {"id": None, "nome": "", "fantasia": "", "cnpj": "", "regime": "Lucro Real", "tipo": "Matriz", "cnae": "", "endereco": ""}
                st.success("Dados processados com sucesso!")
                st.rerun()

    with tab_lista:
        conn = get_db_connection()
        df = pd.read_sql("SELECT id, nome, cnpj, regime, tipo, cnae, endereco FROM empresas", conn)
        conn.close()
        
        # Tabela Clean com botões de ação
        for _, row in df.iterrows():
            with st.container():
                col_info, col_btn = st.columns([5, 1])
                col_info.markdown(f"**{row['nome']}** \n`CNPJ: {row['cnpj']}` | `Regime: {row['regime']}`")
                if col_btn.button("✏️ Editar", key=f"btn_{row['id']}"):
                    st.session_state.dados_form = row.to_dict()
                    st.rerun()
                st.divider()

# --- MÓDULO: RELATÓRIOS E EXPORTAÇÃO ---
elif menu == "Relatórios & Exportação":
    st.subheader("📄 Relatórios e Integração ERP")
    
    # Exemplo de lógica de exportação
    c1, c2 = st.columns(2)
    
    with c1:
        st.info("Gerar PDF de Conferência")
        if st.button("📥 Baixar PDF Profissional"):
            pdf = PDF_Relatorio()
            pdf.add_page()
            pdf.set_font('Arial', '', 12)
            pdf.cell(0, 10, "Detalhamento de Impostos - Exemplo", 0, 1)
            # Aqui entraria o loop dos seus dados de apuração
            
            pdf_output = pdf.output(dest='S').encode('latin-1')
            st.download_button("Clique aqui para salvar PDF", data=pdf_output, file_name="apuracao_crescere.pdf", mime="application/pdf")

    with c2:
        st.success("Exportar para ERP (Layout Genérico)")
        # Simulação de dados para o ERP
        df_erp = pd.DataFrame({"CONTA": ["REC.BRUTA", "PIS", "COFINS"], "VALOR": [10000, 165, 760]})
        csv = df_erp.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Baixar CSV para Importação", data=csv, file_name="import_erp.csv", mime="text/csv")
