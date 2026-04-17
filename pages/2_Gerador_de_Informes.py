import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")

# --- FUNÇÃO PARA LOCALIZAR O WORD NA RAIZ ---
def buscar_word_na_raiz():
    # Caminho da pasta atual (pages/)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Caminho da pasta pai (raiz do projeto)
    raiz = os.path.normpath(os.path.join(current_dir, ".."))
    
    # Lista todos os arquivos na raiz e procura o primeiro .docx
    try:
        arquivos = os.listdir(raiz)
        for arq in arquivos:
            if arq.lower().endswith(".docx"):
                return os.path.join(raiz, arq)
    except:
        return None
    return None

template_path = buscar_word_na_raiz()

# Verificação visual para o usuário
if template_path:
    nome_exibicao = os.path.basename(template_path)
    st.success(f"✅ Template detectado: **{nome_exibicao}**")
else:
    st.error("❌ Nenhum arquivo Word (.docx) encontrado na raiz do projeto.")
    st.info("Dica: Certifique-se de que o arquivo Word não está dentro da pasta 'pages'.")
    st.stop()
# --------------------------------------------

uploaded_file = st.file_uploader("Suba sua planilha de Aluguéis (Excel)", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.subheader("Dados Identificados")
        st.dataframe(df.head())

        if st.button("🚀 Gerar Informes em ZIP"):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                for index, row in df.iterrows():
                    doc = DocxTemplate(template_path)
                    
                    # Preenche os dados
                    context = row.to_dict()
                    context['data_emissao'] = "31/12/2025"
                    
                    doc.render(context)
                    
                    doc_io = io.BytesIO()
                    doc.save(doc_io)
                    
                    # Nome do arquivo no ZIP
                    nome_benef = str(row['nome_beneficiario']).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_benef}.docx", doc_io.getvalue())
            
            st.success("Processamento concluído!")
            st.download_button(
                label="📥 Baixar ZIP",
                data=zip_buffer.getvalue(),
                file_name="Informes_Gerados.zip",
                mime="application/zip"
            )
    except Exception as e:
        st.error(f"Erro: {e}")
