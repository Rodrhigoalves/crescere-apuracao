import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")

# --- FUNÇÃO TÉCNICA PARA ACHAR O ARQUIVO ---
def find_template(name):
    # Procura na pasta atual, na pasta pai e em todo o diretório do app
    search_paths = [
        name,
        os.path.join("..", name),
        os.path.join(os.path.dirname(__file__), name),
        os.path.join(os.path.dirname(__file__), "..", name)
    ]
    for path in search_paths:
        if os.path.exists(path):
            return path
    return None

nome_arquivo = "INFORME-RENDIMENTO-EDITAVEL.docx"
template_path = find_template(nome_arquivo)

# Exibe o status do arquivo para você saber o que está acontecendo
if template_path:
    st.success(f"✅ Template encontrado em: {template_path}")
else:
    st.error(f"❌ O arquivo '{nome_arquivo}' não foi encontrado em NENHUMA pasta.")
    st.info("Arquivos detectados na raiz: " + str(os.listdir(".")))
    st.stop()
# ------------------------------------------

uploaded_file = st.file_uploader("Suba sua planilha de Aluguéis", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.dataframe(df.head())

        if st.button("Gerar Informes"):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                for index, row in df.iterrows():
                    doc = DocxTemplate(template_path)
                    context = row.to_dict()
                    context['data_emissao'] = "31/12/2025"
                    
                    doc.render(context)
                    doc_io = io.BytesIO()
                    doc.save(doc_io)
                    
                    nome_doc = f"Informe_{str(row['nome_beneficiario']).replace(' ', '_')}.docx"
                    zip_file.writestr(nome_doc, doc_io.getvalue())
            
            st.download_button(
                label="📥 Baixar ZIP",
                data=zip_buffer.getvalue(),
                file_name="Informes_Aluguel.zip",
                mime="application/zip"
            )
    except Exception as e:
        st.error(f"Erro: {e}")
