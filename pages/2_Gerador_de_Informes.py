import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

# Configuração da página para o seu ASUS TUF (Wide Mode)
st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")
st.info("Certifique-se de que a biblioteca 'docxtpl' esteja no seu arquivo requirements.txt")

# LOCALIZAÇÃO DO TEMPLATE
# Busca o template na raiz, subindo um nível a partir da pasta /pages
current_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.normpath(os.path.join(current_dir, "..", "INFORME-RENDIMENTO-EDITAVEL.docx"))

# INTERFACE DE UPLOAD
st.subheader("1. Dados de Entrada")
uploaded_file = st.file_uploader("Selecione a planilha 'Aluguel.xlsx'", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.success(f"Planilha carregada: {len(df)} registros encontrados.")
        st.dataframe(df.head(5))

        # VERIFICAÇÃO DO TEMPLATE
        if not os.path.exists(template_path):
            st.error(f"Arquivo não encontrado: {template_path}")
            st.warning("Verifique se o arquivo .docx está na raiz do projeto com o nome exato.")
        else:
            st.subheader("2. Processamento")
            if st.button("🚀 Gerar Informes em Lote (ZIP)"):
                
                zip_buffer = io.BytesIO()
                
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                    progress_bar = st.progress(0)
                    
                    for index, row in df.iterrows():
                        doc = DocxTemplate(template_path)
                        
                        # Converte a linha do Excel em contexto para o Word
                        context = row.to_dict()
                        
                        # A data de emissão será 31/12/2025 (referente ao ano-calendário anterior)
                        context['data_emissao'] = "31/12/2025"
                        
                        doc.render(context)
                        
                        # Salva o arquivo individual
                        doc_io = io.BytesIO()
                        doc.save(doc_io)
                        
                        # Nome do arquivo baseado no beneficiário
                        nome = str(row['nome_beneficiario']).strip().replace(" ", "_")
                        zip_file.writestr(f"Informe_{nome}.docx", doc_io.getvalue())
                        
                        progress_bar.progress((index + 1) / len(df))

                st.success("Todos os documentos foram gerados com sucesso!")
                st.download_button(
                    label="📥 Baixar Arquivos ZIP",
                    data=zip_buffer.getvalue(),
                    file_name="Informes_Aluguel_2025.zip",
                    mime="application/zip"
                )
                
    except Exception as e:
        st.error(f"Erro técnico: {e}")
