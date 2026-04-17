import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os
import glob

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")

# --- BUSCA AUTOMÁTICA POR QUALQUER ARQUIVO QUE COMECE COM 'INFORME' ---
def localizar_template():
    # Procura na pasta atual e na pasta acima por qualquer .docx que tenha 'INFORME' no nome
    padrao = "**/INFORME*.docx"
    arquivos = glob.glob(padrao, recursive=True)
    
    # Se estiver no Streamlit Cloud, tenta caminhos relativos comuns
    if not arquivos:
        arquivos = glob.glob("./**/INFORME*.docx", recursive=True)
        
    return arquivos[0] if arquivos else None

template_path = localizar_template()

if template_path:
    st.success(f"✅ Template detectado: **{os.path.basename(template_path)}**")
else:
    st.error("❌ Erro: Não encontrei nenhum arquivo .docx que comece com 'INFORME' nas pastas do projeto.")
    st.info("Arquivos que eu consigo ver agora: " + str(os.listdir(".")))
    st.stop()
# ---------------------------------------------------------------------

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
                    # Garante a data fixa no campo data_emissao
                    context['data_emissao'] = "31/12/2025"
                    
                    doc.render(context)
                    
                    doc_io = io.BytesIO()
                    doc.save(doc_io)
                    
                    # Nome do arquivo individual
                    nome_benef = str(row['nome_beneficiario']).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_benef}.docx", doc_io.getvalue())
            
            st.success("Tudo pronto!")
            st.download_button(
                label="📥 Baixar Todos os Informes",
                data=zip_buffer.getvalue(),
                file_name="Informes_Gerados.zip",
                mime="application/zip"
            )
    except Exception as e:
        st.error(f"Erro ao processar: {e}")
