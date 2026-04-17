import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")

# --- LOCALIZAÇÃO DO TEMPLATE ---
# 1. Definimos o nome exato que aparece no seu print
NOME_DO_ARQUIVO = "INFORME-RENDIMENTO-EDITAVEL-2026-1 (1).docx"

# 2. Pegamos o caminho da pasta onde este script (.py) está (que é a /pages)
caminho_da_pagina = os.path.dirname(os.path.abspath(__file__))

# 3. Subimos um nível (..) para chegar na raiz e encontrar o Word
template_path = os.path.join(caminho_da_pagina, "..", NOME_DO_ARQUIVO)

# --- VERIFICAÇÃO VISUAL ---
if os.path.exists(template_path):
    st.success(f"✅ Arquivo detectado com sucesso na raiz!")
else:
    st.error(f"❌ O arquivo não foi encontrado.")
    st.info(f"O Python tentou procurar em: {template_path}")
    st.stop() # Para o código aqui se não achar, evitando erro de sistema
# -------------------------------

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
                    
                    # Adiciona a data de emissão fixa
                    context['data_emissao'] = "31/12/2025"
                    
                    doc.render(context)
                    
                    doc_io = io.BytesIO()
                    doc.save(doc_io)
                    
                    # Nome do arquivo individual dentro do ZIP
                    nome_benef = str(row['nome_beneficiario']).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_benef}.docx", doc_io.getvalue())
            
            st.success("Processamento concluído!")
            st.download_button(
                label="📥 Baixar Todos os Informes",
                data=zip_buffer.getvalue(),
                file_name="Informes_Gerados.zip",
                mime="application/zip"
            )
    except Exception as e:
        st.error(f"Erro ao processar: {e}")
