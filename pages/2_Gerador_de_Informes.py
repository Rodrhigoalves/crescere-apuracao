import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")

# --- CONFIGURAÇÃO DO NOME DO ARQUIVO (IDÊNTICO AO GITHUB) ---
NOME_DO_ARQUIVO = "INFORME-RENDIMENTO-EDITAVEL-2026-1 (1).docx"

# Localização: volta um nível para sair da pasta /pages e chegar na raiz
current_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(current_dir, "..", NOME_DO_ARQUIVO)

# --- VERIFICAÇÃO DE SEGURANÇA ---
if os.path.exists(template_path):
    st.success(f"✅ Arquivo encontrado: {NOME_DO_ARQUIVO}")
else:
    st.error(f"❌ Arquivo NÃO encontrado na raiz do GitHub.")
    st.info(f"Certifique-se de que o nome no GitHub seja exatamente: {NOME_DO_ARQUIVO}")
    st.stop()
# -----------------------------------------------------------

uploaded_file = st.file_uploader("Suba sua planilha de Aluguéis (Excel)", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.subheader("Prévia dos Dados")
        st.dataframe(df.head())

        if st.button("🚀 Gerar Informes em ZIP"):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                for index, row in df.iterrows():
                    doc = DocxTemplate(template_path)
                    
                    # Converte a linha do Excel em dados para o Word
                    context = row.to_dict()
                    # Adiciona a data fixa de emissão
                    context['data_emissao'] = "31/12/2025"
                    
                    doc.render(context)
                    
                    doc_io = io.BytesIO()
                    doc.save(doc_io)
                    
                    # Nome do arquivo individual dentro do ZIP
                    nome_benef = str(row['nome_beneficiario']).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_benef}.docx", doc_io.getvalue())
            
            st.success("Documentos gerados com sucesso!")
            st.download_button(
                label="📥 Baixar Todos os Informes",
                data=zip_buffer.getvalue(),
                file_name="Informes_Gerados.zip",
                mime="application/zip"
            )
    except Exception as e:
        st.error(f"Erro ao processar: {e}")
