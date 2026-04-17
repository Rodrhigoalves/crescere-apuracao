import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")
st.markdown("---")

# Ajuste automático do caminho do template
# Se o arquivo estiver na raiz, '..' volta um nível a partir da pasta /pages
current_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(current_dir, "..", "INFORME-RENDIMENTO-EDITAVEL.docx")

# Interface de Upload
uploaded_file = st.file_uploader("Suba sua planilha de Aluguéis", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)
    st.subheader("Dados Identificados")
    st.dataframe(df.head())

    # Verificação se o arquivo de template existe no local esperado
    if not os.path.exists(template_path):
        st.error(f"Arquivo de template não encontrado em: {template_path}. Certifique-se de que o arquivo .docx está na pasta raiz do projeto.")
    else:
        if st.button("Gerar 100% dos Informes"):
            try:
                zip_buffer = io.BytesIO()
                
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                    for index, row in df.iterrows():
                        doc = DocxTemplate(template_path)
                        
                        # Converte a linha do Excel em dicionário para preencher o Word
                        # As tags {{campo}} devem ser iguais aos nomes das colunas no Excel
                        context = row.to_dict()
                        
                        # Garante que a data de emissão seja o último dia do ano anterior
                        context['data_emissao'] = "31/12/2025"
                        
                        doc.render(context)
                        
                        doc_io = io.BytesIO()
                        doc.save(doc_io)
                        doc_io.seek(0)
                        
                        # Nomeia o arquivo com o nome do beneficiário (limpando espaços)
                        nome_limpo = str(row['nome_beneficiario']).strip().replace(" ", "_")
                        zip_file.writestr(f"Informe_{nome_limpo}.docx", doc_io.getvalue())
                
                st.success(f"Concluído! {len(df)} documentos processados sem distorções de layout.")
                st.download_button(
                    label="📥 Baixar Arquivos (ZIP)",
                    data=zip_buffer.getvalue(),
                    file_name="Informes_Rendimentos_Aluguel.zip",
                    mime="application/zip"
                )
            except Exception as e:
                st.error(f"Erro no processamento: {e}")
